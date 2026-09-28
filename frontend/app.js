// All server text is rendered with textContent, never innerHTML, so nothing a user or the
// model says can inject script into the page (the sign-in token lives in localStorage).
const $ = (id) => document.getElementById(id);
const logEl = $('log');
const messagesEl = $('messages');
let conversationId = null;
// sent with every message so booking times are shown in your own timezone
const userTimezone = Intl.DateTimeFormat().resolvedOptions().timeZone || 'UTC';
let busy = false;
let knownMemoryIds = new Set();
let memoryLoaded = false;
let voiceOn = true;
try { voiceOn = localStorage.getItem('sarjy_voice') !== 'off'; } catch (e) {}

// ---------- sign in ----------
function token() { try { return localStorage.getItem('sarjy_token'); } catch (e) { return null; } }

function showApp() {
  const signedIn = !!token();
  $('login-box').hidden = signedIn;
  $('app').hidden = !signedIn;
  if (!signedIn) return;
  $('whoami').textContent = localStorage.getItem('sarjy_username') || '';
  const narrow = window.innerWidth <= 900;
  $('memory-panel').hidden = narrow;
  $('chats-panel').hidden = narrow;
  renderVoiceToggle();
  // reopen the chat you were in (e.g. after a refresh), otherwise start fresh
  let last = null;
  try { last = sessionStorage.getItem('sarjy_conv'); } catch (e) {}
  if (last) openChat(Number(last)); else newChat();
  loadChats();
  loadMemory();
}

$('login-form').onsubmit = async (e) => {
  e.preventDefault();
  $('login-error').textContent = '';
  $('login').disabled = true;
  try {
    const res = await fetch('/login', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ username: $('username').value, password: $('password').value }),
    });
    const j = await res.json();
    if (!res.ok) { $('login-error').textContent = j.detail || 'Sign in failed'; return; }
    localStorage.setItem('sarjy_token', j.token);
    localStorage.setItem('sarjy_username', j.username);
    showApp();
  } catch (err) {
    $('login-error').textContent = 'Could not reach the server. It may be waking up, try again in a moment.';
  } finally {
    $('login').disabled = false;
  }
};

function logout() {
  stopSpeaking();
  localStorage.removeItem('sarjy_token');
  localStorage.removeItem('sarjy_username');
  try { sessionStorage.removeItem('sarjy_conv'); } catch (e) {}
  conversationId = null;
  knownMemoryIds = new Set();
  memoryLoaded = false;
  showApp();
}
$('logout').onclick = logout;

// fetch() that adds the token, and sends you back to sign in if it's no longer valid
async function api(url, options = {}) {
  const headers = { ...(options.headers || {}), Authorization: `Bearer ${token()}` };
  const res = await fetch(url, { ...options, headers });
  if (res.status === 401) { logout(); throw new Error('signed out'); }
  return res;
}

// ---------- status line ----------
const STATUS = {
  idle: ['Ready', ''],
  listening: ['Listening… tap the mic again to send', 'listening'],
  uploading: ['Uploading your recording…', 'busy'],
  transcribing: ['Transcribing what you said…', 'busy'],
  thinking: ['Sarjy is thinking…', 'busy'],
  voicing: ['Preparing voice…', 'busy'],
  speaking: ['Sarjy is speaking', 'busy'],
};
let slowTimer = null;
function setStatus(state, textOverride) {
  const [text, cls] = STATUS[state];
  $('status').className = 'status ' + cls;
  $('status-text').textContent = textOverride || text;
  $('stop').hidden = state !== 'speaking' && state !== 'voicing';
  $('header-orb').classList.toggle('active', state === 'speaking');
  clearTimeout(slowTimer);
  if (state === 'thinking') {
    // the free server sleeps when idle, and the first request after that is slow
    slowTimer = setTimeout(() => {
      $('status-text').textContent = 'Still working… the server may be waking up (up to a minute)';
    }, 8000);
  }
}

function setBusy(value) {
  busy = value;
  $('send').disabled = value;
  $('record').disabled = value && !recording;
}

// ---------- messages ----------
function scrollDown() { messagesEl.scrollTop = messagesEl.scrollHeight; }

function addRow(who, text, via) {
  logEl.querySelector('.empty')?.remove();
  const row = document.createElement('div');
  row.className = 'row ' + who;
  if (who !== 'user') {
    const orb = document.createElement('div');
    orb.className = 'orb small';
    row.appendChild(orb);
  }
  const bubble = document.createElement('div');
  bubble.className = 'bubble';
  if (via) {
    const tag = document.createElement('span');
    tag.className = 'via';
    tag.textContent = via;
    bubble.appendChild(tag);
  }
  const body = document.createElement('span');
  body.textContent = text || '';
  bubble.appendChild(body);
  row.appendChild(bubble);
  logEl.appendChild(row);
  scrollDown();
  return { row, body };
}

function addTypingRow() {
  const { row, body } = addRow('sarjy', '');
  const dots = document.createElement('span');
  dots.className = 'typing';
  dots.append(document.createElement('span'), document.createElement('span'), document.createElement('span'));
  body.appendChild(dots);
  return { row, body };
}

function showEmptyState() {
  logEl.textContent = '';
  const wrap = document.createElement('div');
  wrap.className = 'empty';
  const orb = document.createElement('div');
  orb.className = 'orb big';
  const h = document.createElement('h2');
  h.textContent = `Hi ${localStorage.getItem('sarjy_username') || 'there'}, I'm Sarjy`;
  const p = document.createElement('p');
  p.textContent = 'Talk to me or type. I remember what you tell me across sessions, and I can book a call with our team for you.';
  const chips = document.createElement('div');
  chips.className = 'chips';
  for (const s of ['Book a call with your sales team', 'What do you remember about me?', "Hi, I'm new here, what can you do?"]) {
    const b = document.createElement('button');
    b.className = 'chip';
    b.textContent = s;
    b.onclick = () => sendMessage(s);
    chips.appendChild(b);
  }
  wrap.append(orb, h, p, chips);
  logEl.appendChild(wrap);
}

function setConversation(id) {
  conversationId = id;
  try { id ? sessionStorage.setItem('sarjy_conv', id) : sessionStorage.removeItem('sarjy_conv'); } catch (e) {}
  document.querySelectorAll('.chat-list button').forEach((b) => b.classList.toggle('active', Number(b.dataset.id) === id));
}

function newChat() {
  stopSpeaking();
  setConversation(null);
  showEmptyState();
  setStatus('idle');
  setBusy(false);
  if (window.innerWidth <= 900) $('chats-panel').hidden = true;
}
$('new-chat').onclick = newChat;

// ---------- chats sidebar ----------
$('chats-toggle').onclick = () => { $('chats-panel').hidden = !$('chats-panel').hidden; };
$('chats-close').onclick = () => { $('chats-panel').hidden = true; };

async function loadChats() {
  let items = [];
  try { items = (await (await api('/conversations')).json()).items || []; } catch (e) { return; }
  const list = $('chat-list');
  list.textContent = '';
  if (!items.length) {
    const li = document.createElement('li');
    li.className = 'none';
    li.textContent = 'Your chats will show up here.';
    list.appendChild(li);
  }
  for (const item of items) {
    const li = document.createElement('li');
    const b = document.createElement('button');
    b.textContent = item.title;
    b.title = item.title;
    b.dataset.id = item.id;
    b.classList.toggle('active', item.id === conversationId);
    b.onclick = () => openChat(item.id);
    li.appendChild(b);
    list.appendChild(li);
  }
}

// open an earlier chat: its messages come back, and Sarjy continues with that chat's context
async function openChat(id) {
  stopSpeaking();
  let items = [];
  try {
    const res = await api(`/history?conversation_id=${id}`);
    if (!res.ok) { newChat(); return; }
    items = (await res.json()).items || [];
  } catch (e) { return; }
  setConversation(id);
  logEl.textContent = '';
  if (!items.length) showEmptyState();
  for (const m of items) addRow(m.role === 'user' ? 'user' : 'sarjy', m.content);
  setStatus('idle');
  setBusy(false);
  if (window.innerWidth <= 900) $('chats-panel').hidden = true;
}

// ---------- sending a message ----------
$('composer').onsubmit = (e) => {
  e.preventDefault();
  const text = $('msg').value.trim();
  if (!text || busy) return;
  $('msg').value = '';
  sendMessage(text);
};

async function sendMessage(text, alreadyShown = false) {
  if (busy && !alreadyShown) return;
  stopSpeaking();
  setBusy(true);
  if (!alreadyShown) addRow('user', text);
  const pending = addTypingRow();
  setStatus('thinking');
  try {
    const res = await api('/message', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ conversation_id: conversationId, message: text, timezone: userTimezone }),
    });
    if (!res.ok) throw new Error('bad response');
    const j = await res.json();
    const isNew = conversationId !== j.conversation_id;
    setConversation(j.conversation_id);
    if (isNew) loadChats();  // a new chat shows up in the sidebar
    await speakAndType(pending.body, j.reply);
    // memory is saved in the background after the reply, so check for new facts shortly after
    setTimeout(loadMemory, 2500);
  } catch (err) {
    if (err.message === 'signed out') return;
    pending.row.classList.add('error');
    pending.body.textContent = 'Something went wrong, please try again.';
  } finally {
    setStatus('idle');
    setBusy(false);
  }
}

// ---------- voice out + text typed in time with it ----------
let currentAudio = null;
let typingTimer = null;
let finishTyping = null;

function typeOut(el, text, totalMs) {
  return new Promise((resolve) => {
    const words = text.split(/(\s+)/);
    const step = Math.max(15, Math.min(220, totalMs / Math.max(words.length, 1)));
    let i = 0;
    el.textContent = '';
    el.classList.add('cursor');
    finishTyping = () => {
      clearInterval(typingTimer);
      el.textContent = text;
      el.classList.remove('cursor');
      finishTyping = null;
      resolve();
    };
    typingTimer = setInterval(() => {
      el.textContent += words[i++] || '';
      scrollDown();
      if (i >= words.length) finishTyping();
    }, step);
  });
}

async function fetchSpeech(text) {
  // give up after 12s and use the browser voice instead (OpenAI TTS occasionally stalls)
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), 12000);
  try {
    const form = new FormData();
    form.append('text', text);
    const res = await api('/tts', { method: 'POST', body: form, signal: controller.signal });
    if (!(res.headers.get('content-type') || '').startsWith('audio/')) return null;
    return URL.createObjectURL(await res.blob());
  } catch (e) {
    return null;
  } finally {
    clearTimeout(timer);
  }
}

async function speakAndType(el, text) {
  if (!voiceOn) {
    await typeOut(el, text, text.length * 18);
    return;
  }
  setStatus('voicing');
  const url = await fetchSpeech(text);
  setStatus('speaking');

  if (url) {
    const audio = new Audio(url);
    currentAudio = audio;
    await new Promise((r) => { audio.onloadedmetadata = r; audio.onerror = r; });
    const duration = isFinite(audio.duration) ? audio.duration * 1000 : text.length * 60;
    const done = new Promise((r) => { audio.onended = r; audio.onpause = r; });
    audio.play().catch(() => {});
    await typeOut(el, text, duration * 0.92);
    await done;
    URL.revokeObjectURL(url);
    currentAudio = null;
    return;
  }

  // fallback: the browser's built-in voice
  try {
    const u = new SpeechSynthesisUtterance(text);
    const done = new Promise((r) => { u.onend = r; u.onerror = r; });
    speechSynthesis.cancel();
    speechSynthesis.speak(u);
    await typeOut(el, text, text.length * 60);
    await Promise.race([done, new Promise((r) => setTimeout(r, 30000))]);
  } catch (e) {
    await typeOut(el, text, text.length * 18);
  }
}

function stopSpeaking() {
  if (currentAudio) { currentAudio.pause(); currentAudio = null; }
  try { speechSynthesis.cancel(); } catch (e) {}
  if (finishTyping) finishTyping();
}
$('stop').onclick = stopSpeaking;

function renderVoiceToggle() {
  $('voice-toggle').classList.toggle('voice-off', !voiceOn);
  $('voice-waves').style.display = voiceOn ? '' : 'none';
  $('voice-toggle').title = voiceOn ? 'Voice replies on (click to mute)' : 'Voice replies off (click to unmute)';
}
$('voice-toggle').onclick = () => {
  voiceOn = !voiceOn;
  try { localStorage.setItem('sarjy_voice', voiceOn ? 'on' : 'off'); } catch (e) {}
  if (!voiceOn) stopSpeaking();
  renderVoiceToggle();
};

// ---------- voice in: record -> upload -> transcribe -> send ----------
let recording = false;
let mediaRecorder = null;
let chunks = [];
let levelFrame = null;
let audioCtx = null;
let recordStart = 0;

$('record').onclick = async () => {
  if (recording) { mediaRecorder.stop(); return; }
  if (busy) return;
  stopSpeaking();

  let stream;
  try {
    stream = await navigator.mediaDevices.getUserMedia({ audio: true });
  } catch (e) {
    addRow('error', 'I need microphone access to hear you. Check your browser permissions and try again.');
    return;
  }

  recording = true;
  chunks = [];
  mediaRecorder = new MediaRecorder(stream);
  mediaRecorder.ondataavailable = (e) => chunks.push(e.data);
  mediaRecorder.onstop = () => {
    recording = false;
    stream.getTracks().forEach((t) => t.stop());
    cancelAnimationFrame(levelFrame);
    audioCtx?.close();
    $('record').classList.remove('recording');
    $('record').style.setProperty('--level', 0);
    uploadRecording(new Blob(chunks, { type: mediaRecorder.mimeType || 'audio/webm' }));
  };
  mediaRecorder.start();
  recordStart = Date.now();
  $('record').classList.add('recording');
  setStatus('listening');
  showLevel(stream);
};

// makes the ring around the mic grow with your voice, and shows a timer
function showLevel(stream) {
  audioCtx = new AudioContext();
  const analyser = audioCtx.createAnalyser();
  analyser.fftSize = 512;
  audioCtx.createMediaStreamSource(stream).connect(analyser);
  const data = new Uint8Array(analyser.fftSize);
  const tick = () => {
    analyser.getByteTimeDomainData(data);
    let sum = 0;
    for (const v of data) sum += ((v - 128) / 128) ** 2;
    const level = Math.min(1, Math.sqrt(sum / data.length) * 4);
    $('record').style.setProperty('--level', level.toFixed(2));
    const secs = Math.floor((Date.now() - recordStart) / 1000);
    $('status-text').textContent = `Listening… ${Math.floor(secs / 60)}:${String(secs % 60).padStart(2, '0')}  (tap the mic again to send)`;
    levelFrame = requestAnimationFrame(tick);
  };
  tick();
}

function uploadRecording(blob) {
  setBusy(true);
  setStatus('uploading');
  const form = new FormData();
  form.append('audio', blob, blob.type.includes('mp4') ? 'audio.mp4' : 'audio.webm');

  // XMLHttpRequest instead of fetch() so we can show upload progress
  const xhr = new XMLHttpRequest();
  xhr.open('POST', '/transcribe');
  xhr.setRequestHeader('Authorization', `Bearer ${token()}`);
  xhr.upload.onprogress = (e) => {
    if (e.lengthComputable) setStatus('uploading', `Uploading your recording… ${Math.round((e.loaded / e.total) * 100)}%`);
  };
  xhr.upload.onload = () => setStatus('transcribing');
  xhr.onload = () => {
    if (xhr.status === 401) return logout();
    let transcript = '';
    try { transcript = (JSON.parse(xhr.responseText).transcript || '').trim(); } catch (e) {}
    if (!transcript) {
      addRow('error', "I didn't catch that. Try again, a little closer to the mic.");
      setStatus('idle');
      setBusy(false);
      return;
    }
    addRow('user', transcript, '🎙 You said');
    setBusy(false);
    sendMessage(transcript, true);
  };
  xhr.onerror = () => {
    addRow('error', 'Upload failed, please try again.');
    setStatus('idle');
    setBusy(false);
  };
  xhr.send(form);
}

// ---------- memory panel ----------
$('memory-toggle').onclick = () => { $('memory-panel').hidden = !$('memory-panel').hidden; };
$('memory-close').onclick = () => { $('memory-panel').hidden = true; };

function prettyKey(key) {
  const s = key.replace(/_/g, ' ');
  return s.charAt(0).toUpperCase() + s.slice(1);
}

async function loadMemory() {
  let items = [];
  try {
    const res = await api('/memory');
    items = (await res.json()).items || [];
  } catch (e) { return; }

  const list = $('memory-list');
  list.textContent = '';
  $('memory-count').textContent = items.length;
  $('memory-count').hidden = items.length === 0;
  if (!items.length) {
    const li = document.createElement('li');
    li.className = 'memory-empty';
    li.textContent = 'Nothing yet. Tell Sarjy about yourself, like your name, your job or your favorite food.';
    list.appendChild(li);
  }
  for (const item of items) {
    const li = document.createElement('li');
    li.className = 'memory-item' + (memoryLoaded && !knownMemoryIds.has(item.id) ? ' new' : '');
    const text = document.createElement('div');
    text.className = 'text';
    const k = document.createElement('span');
    k.className = 'k';
    k.textContent = prettyKey(item.key);
    const v = document.createElement('span');
    v.className = 'v';
    v.textContent = item.value;
    text.append(k, v);
    const forget = document.createElement('button');
    forget.textContent = '×';
    forget.title = 'Forget this';
    forget.onclick = async () => {
      await api(`/memory/${item.id}`, { method: 'DELETE' });
      loadMemory();
    };
    li.append(text, forget);
    list.appendChild(li);
  }
  knownMemoryIds = new Set(items.map((i) => i.id));
  memoryLoaded = true;
}

showApp();
