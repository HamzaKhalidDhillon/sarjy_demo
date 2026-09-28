# Sarjy: Goals & Decisions

Decisions and write-up for the Sarj take-home (the brief is in `instruction.md`).

## What the assignment requires

Build, deploy, and present a voice assistant ("Sarjy") that: listens/responds by voice, remembers
facts/preferences across sessions, calls at least one external API that adds real value
(justified in 2-3 sentences), and is deployed to a URL a reviewer can open with no special setup.
Additionally: pick **one** "deep dive" area and go deep on it -- "one done well is better than
three done shallowly."

## Decisions

### Deep dive: Guardrails & Reliability

We're prioritizing reliability and security together, since a scheduling agent's actual failure
modes -- a hallucinated confirmation, a double-booked slot, a jailbreak attempt, losing track of
a booking when the user goes off-script -- are exactly what "guardrails and reliability" means
for this kind of assistant. Concretely, we built:

- Deterministic input guardrails (jailbreak/prohibited-topic detection) that run before any LLM
  or tool call.
- A structural (not just prompted) guarantee that the assistant never claims a meeting is booked
  unless Cal.com's API actually returned a real booking id that turn -- see
  `agent/templates.py` and `agent/guardrails.py::OutputGuardrail`.
- An idempotency key on booking confirmations so a retried "yes, book it" can't double-book.
- A retry/backoff helper shared by every external call (OpenAI, Cal.com), with booking
  *creation* specifically excluded from blind retries (a retried write could double-book) --
  ambiguous outcomes are reconciled instead of guessed at.

Latency got real attention too (structured per-stage timing in every pipeline run -- see
`core/logging.py`) but isn't the headline; we didn't do a dedicated time-to-first-audio
optimization pass.

### The use case, concretely

Sarjy is a voice-based scheduling receptionist -- a voice front-end for a Cal.com booking page.
Someone talks to it, says they want to set up a quick intro chat with the team, and Sarjy checks
real availability and books directly onto one fixed calendar. No per-caller calendars, no OAuth,
no picking between multiple hosts -- deliberately narrow and demoable rather than a general-
purpose "do anything" assistant. This is also a real, existing category (AI voice receptionists
that replace "click this Calendly link"), which makes it a believable answer to "why this API,
why this use case" rather than a contrived one.

### External API: Cal.com (meeting scheduling)

We chose Cal.com over a weather/maps API because it lets Sarjy do something genuinely useful for
a voice assistant in the Sarj (voice AI) space: check real availability and book a real meeting
end to end, not just fetch a read-only fact. It's a real external API with a side effect (an
actual scheduled meeting, confirmation email, video link), which makes the "don't hallucinate
when using tools" reliability story concrete instead of hypothetical. Auth is a single free API
key (no OAuth), which kept setup cost low relative to Google Calendar.

**Setup required to actually exercise the booking flow:**
1. Create a free account at cal.com and generate an API key (Settings -> Security).
2. Make sure the account has a public event type (new accounts come with `30min` and `15min`).
   The app uses `30min` automatically, or the first public one; `CALCOM_EVENT_TYPE_ID` in
   `.env` overrides that if you want a specific one.
3. In `backend/.env`, set `CALCOM_API_KEY`.
4. Cal.com versions its API per-endpoint via a `cal-api-version` header. The defaults in
   `.env.example` (`CALCOM_API_VERSION_SLOTS`, `CALCOM_API_VERSION_BOOKINGS`) were pulled from
   cal.com/docs at implementation time -- if slot lookups or bookings start failing, check
   `https://cal.com/docs/api-reference/v2/{slots,bookings}` for the current value and override it
   via `.env` (no code change needed).

Without a Cal.com key configured, the booking flow fails gracefully with a clear "can't reach the
calendar right now" message rather than crashing -- see `CalComClient._ensure_configured()`.

**Verified end to end against a real account**, not just in unit tests: availability check ->
slot selection -> confirmation -> email collection -> real `POST /v2/bookings` -> real booking
(with a Cal Video link and a Cal.com-side confirmation) -> cancelled via `cancel_booking` for
cleanup. Two things only live testing caught, now fixed:
- `GET /v2/event-types` 404s if a `cal-api-version` header is sent at all (unlike slots/bookings,
  which require one) and its response shape is `data.eventTypeGroups[*].eventTypes[*]`, not a
  flat list -- `tools/calcom/client.py` was fixed to match.
- Cal.com rejects a booking whose attendee has no email or phone ("Attendee must have at least
  one contact method"), even though the docs summary we started from said name+timezone were
  sufficient. The booking flow now has an explicit `awaiting_contact` state that asks for an
  email before ever calling `create_booking` -- see `agent/state.py`'s `BookingState` and
  `agent/booking_flow.py` (`BookingFlow.collect_contact`).

### Memory: cross-session recall

Memory is keyed by the signed-in username. On **every turn**, `agent/memory.py` loads all of the
user's saved facts and adds them to the LLM's instructions ("Known facts about this user: ..."),
and the last few messages of the current conversation go along too, so Sarjy has both long-term
memory and short-term context.

**After the reply has been sent** (a FastAPI background task, so no added latency), the LLM picks
out facts worth keeping from the user's message and returns them as JSON (`name`, `job`,
`favorite_food`, ...). It's given the already-saved facts so it updates a key instead of creating
duplicates. We started with a regex extractor ("my name is ...") but live testing showed people
don't talk like that: "this is Hamza", "I'm really into sushi" were all missed. The regex patterns
are still the fallback when no LLM is configured or its output isn't valid JSON. Guardrail-blocked
messages are never stored, and keys/values are length-checked before saving.

The UI shows everything Sarjy remembers in a side panel, and each fact can be deleted, so memory
is visible and under the user's control rather than a black box.

### Booking flow, from the user's side

"Book me a call with your agent" -> Sarjy lists real open times. If the user names a time
themselves ("tomorrow at 3pm", in the first message or later), Sarjy checks exactly that time:
if it's open it asks to confirm; if it isn't, it says so and suggests other open times that day,
or the next open ones after it if the day is full. All times come from Cal.com, never the LLM.

### Sign-in

A deliberately small sign-in: username + password, and the first login with a new username
creates the account (no separate signup page, no email). The brief wants reviewers to open the
URL "without special setup", so this is one form, not an onboarding flow. What it buys us: memory
and bookings are tied to an account nobody else can read, instead of a free-text user id anyone
could type. See "Data security" below for how it's built.

### Frontend

Still a single static `index.html` with plain JavaScript (no build step), but designed for voice:
chat bubbles, a status line that walks through each step (listening -> uploading -> transcribing
-> thinking -> preparing voice -> speaking), a mic whose ring follows your voice level, replies
typed out in time with the audio, a stop button, a mute toggle, and the memory panel. Voice input
is transcribed first (`/transcribe`) so your words appear before Sarjy answers. All server text is
rendered with `textContent`, never `innerHTML`.

## Data security

Sarjy stores personal data (remembered facts about people, conversation history, and the email
addresses used for bookings), so security was treated as part of the reliability deep dive.

**What's in place**
- **Passwords** are hashed with PBKDF2-SHA256 (200k iterations, random per-user salt, stdlib only)
  and compared in constant time. Plaintext passwords are never stored or logged.
- **Identity comes from the server, not the browser.** Sign-in returns a random 256-bit token;
  every endpoint looks the user up from that token. The browser never sends a `user_id`, so
  editing a request can't make you someone else.
- **Ownership checks**: a conversation can only be continued or read by the user who owns it
  (`/message`, `/history`; covered by `tests/test_auth.py`).
- **Cost abuse**: every endpoint that spends provider credits (`/transcribe`, `/tts`,
  `/message`) requires sign-in, so an open URL can't be used to drain API keys.
- **Supabase Row Level Security** is switched on for every table at startup. Supabase serves the
  `public` schema through its REST API using the anon key; with RLS on and no policies, that API
  sees nothing, while the backend (connecting as the table owner) is unaffected.
- **Secrets** live only in environment variables (Render dashboard, `sync: false` in
  `render.yaml`); `.env` is gitignored.
- **Transport**: HTTPS is terminated by Render; the Supabase connection is TLS (add
  `?sslmode=require` to `DATABASE_URL` to enforce it rather than just prefer it).
- **Input limits and guardrails**: 5 MB audio cap, TTS length cap, and the input guardrails run
  before any LLM or tool call. A message blocked by the guardrails is never stored as a memory.
- **No script injection from the model**: the frontend renders every message with
  `textContent`, never `innerHTML`, which matters because the sign-in token lives in
  `localStorage`.
- **Logs** contain request ids, paths and timings only: no message text, tokens or emails.

**What we'd do next**
- Store tokens hashed and give them an expiry; move them from `localStorage` to an `HttpOnly`,
  `SameSite` cookie so even an XSS bug couldn't read them.
- Rate-limit `/login` (brute force) and the LLM endpoints per user.
- A "forget me" endpoint that deletes a user's memories, messages and bookings (GDPR-style right
  to erasure), plus a retention window on conversation history.
- Real signup with email verification and a password policy once this is more than a demo.
- Lock `ALLOWED_ORIGINS` to the deployed URL (the frontend is same-origin, so CORS isn't needed
  at all today).

## Latency

Not our deep dive, but measured and designed for. Every request logs `path=... ms=...` and every
pipeline stage logs `stage=... ms=...` with the same request id (`core/logging.py`), so one voice
turn can be broken down straight from the Render logs.

**Where the time goes in one voice turn** (it's sequential today):
1. The browser uploads the whole recording after the user stops, then Whisper transcribes it.
2. The agent turn: sign-in lookup, conversation/state/memory reads and message writes (roughly
   8-10 database round trips), then one non-streamed LLM call. Booking turns add Cal.com.
3. The reply is spoken: the browser makes a second request to `/tts` and waits for the full
   audio file before playing it. The browser's built-in speech is only a fallback if that fails.

**Measured** (from a laptop against the live APIs, a few runs each)

| Stage | Time |
|---|---|
| Whisper transcription (3 s clip) | ~1.4-1.8 s |
| LLM reply (`gpt-3.5-turbo` at the time; now `gpt-4o-mini`) | ~1.8-1.9 s warm, ~4 s first call |
| OpenAI TTS (one sentence, full audio) | ~4-6 s, one outlier at 47 s |
| Cal.com slot lookup | ~0.5 s warm, ~1.7 s first call |

So a voice turn takes roughly 8-10 s to first audio (a typed one ~6-8 s), and TTS alone is about
half of it, because we wait for the whole audio file before playing anything. We chose one
consistent, good-quality voice over speed for now; the browser's built-in voice would be instant
but noticeably robotic. The 47 s outlier is also why the TTS call needs a tighter timeout with a
quick fallback to browser speech.

- Cal.com slot lookup: ~530 ms warm, ~1.7 s on the first call of a process (event-type lookup +
  a new TLS connection).
- A 7-day slot lookup costs the same as a 1-day one (528 vs 533 ms), so when checking a requested
  time we fetch that day plus the following week in one call. When the day is full, that saves a
  second ~0.5 s round trip before suggesting alternatives.
- Password hashing: ~16 ms, once per sign-in only.

**Deployment choices that matter**
- Put the Supabase project in the same region as the Render service. With ~10 database round
  trips per turn, a cross-region hop adds up fast.
- Render's free tier sleeps after 15 idle minutes; the first request after that takes ~30-50 s.
  Open the URL once before a demo.

**What we'd do next, by expected impact**
1. Stream TTS audio (and the LLM reply feeding it) and start playing on the first sentence,
   instead of waiting for the full reply and the full audio file. TTS is the biggest single
   cost measured above, so this is the biggest win for time-to-first-audio.
2. Reuse one HTTP client per provider (OpenAI, Cal.com) so calls skip a new TLS handshake each
   time; today every call opens a fresh connection.
3. Stream audio to STT while the user is still talking, instead of uploading after they stop.
4. Cut database round trips per turn (one commit per turn, cache the token lookup), and cache
   Cal.com slots for a minute within a booking conversation.

## Deployment

One Render web service (Docker, `render.yaml` blueprint) runs the backend, which also serves the
frontend; `/` redirects to it. Persistence is Supabase Postgres via its Session pooler (Render
can't reach Supabase's IPv6-only direct connection). SQLite is still the default locally.
Render's free disk is wiped on every restart, which would have silently broken cross-session
memory, hence an external database.

## Architecture

See `ARCHITECTURE.md` (a turn step by step, and the booking state machine) and the "Project
structure" section of the README. In short: providers behind small interfaces (`llm/`, `stt/`,
`tts/`), Cal.com as MCP-shaped tools (`tools/calcom/`), and in `agent/` an orchestrator that runs
guardrails and memory and hands each message to `BookingFlow` or `ChangeFlow` depending on the
conversation's state.

## Future work (explicitly out of scope for this pass)

- **Real standalone MCP server process** -- `tools/base.py`'s `Tool` shape (name, description,
  JSON-schema params, `run()`) is intentionally MCP-tool-compatible, but we didn't stand up an
  actual MCP server/client transport for this deadline.
- **Full circuit breakers** with cross-request provider health tracking -- the per-request
  fallback chains are reliability enough for a take-home.
- **OAuth for Cal.com** -- explicitly avoided; API key + one event type was the right amount of
  setup for this scope.
- **Production-grade auth** -- the sign-in above is deliberately minimal; see "Data security"
  for what we'd add.
- **Alembic migrations** -- `create_all()` plus adding missing nullable columns at startup
  (`db.py`) is enough for this schema; a real migration tool would come with more changes.
- **Streaming speech** -- start playing the reply on its first sentence (see "Latency").
- **Bookings made outside Sarjy** -- Sarjy knows the calls it booked; reading the rest from
  Cal.com's bookings API would let it cancel or move those too.
- **LLM-based moderation as the primary guardrail** -- deterministic regex/keyword checks are
  the primary layer; an LLM classifier behind a flag would be a reasonable second pass.
- **Booking reconciliation via a real Cal.com `GET /bookings` lookup** -- ambiguous
  (network-timeout) booking attempts currently stay `pending` and ask the user to retry rather
  than being automatically reconciled against Cal.com's own record.
