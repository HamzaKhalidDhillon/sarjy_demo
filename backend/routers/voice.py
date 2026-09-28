import os
import tempfile
from typing import Optional

from fastapi import APIRouter, BackgroundTasks, Depends, File, Form, Response, UploadFile
from sqlalchemy.orm import Session

from backend.agent import memory
from backend.agent.orchestrator import agent
from backend.core.config import settings
from backend.core.logging import timed
from backend.db import SessionLocal
from backend.models import Message
from backend.routers.auth import current_user
from backend.routers.chat import _get_or_create_conversation
from backend.stt.chain import SttChain
from backend.tts.chain import TtsChain

router = APIRouter()


async def _transcribe_upload(audio: UploadFile) -> str | None:
    """Stream the upload to a bounded-size temp file and transcribe it. None if it's too big."""
    # keep the real audio type (Safari records mp4, Chrome/Firefox webm) so Whisper reads it right
    suffix = os.path.splitext(audio.filename or "")[1].lower()
    if suffix not in (".webm", ".mp4", ".m4a", ".ogg", ".wav", ".mp3"):
        suffix = ".webm"
    total = 0
    with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as tmp:
        tmp_path = tmp.name
        while True:
            chunk = await audio.read(1024 * 64)
            if not chunk:
                break
            total += len(chunk)
            if total > settings.max_upload_bytes:
                tmp.close()
                os.unlink(tmp_path)
                return None
            tmp.write(chunk)

    try:
        with timed("stt_total"):
            return await SttChain().run(tmp_path)
    finally:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass


@router.post("/transcribe")
async def transcribe(audio: UploadFile = File(...), user_id: str = Depends(current_user)):
    """Speech to text only. The UI uses this so it can show what you said before Sarjy answers,
    then sends the text to /message like a typed message."""
    transcript = await _transcribe_upload(audio)
    if transcript is None:
        return {"error": "file_too_large", "max_bytes": settings.max_upload_bytes}
    return {"transcript": transcript}


@router.post("/stt")
async def stt(
    background: BackgroundTasks,
    conversation_id: Optional[int] = Form(None),
    audio: UploadFile = File(...),
    user_id: str = Depends(current_user),
):
    """Transcribe + reply in one request (the original endpoint, kept for API clients/tests)."""
    transcript = await _transcribe_upload(audio)
    if transcript is None:
        return {"error": "file_too_large", "max_bytes": settings.max_upload_bytes}

    db: Session = SessionLocal()
    try:
        conv = _get_or_create_conversation(db, user_id, conversation_id)

        user_msg = Message(conversation_id=conv.id, role="user", content=transcript)
        db.add(user_msg)
        db.commit()

        reply = await agent.run_turn(db, user_id, conv.id, transcript)

        assist_msg = Message(conversation_id=conv.id, role="assistant", content=reply)
        db.add(assist_msg)
        db.commit()

        background.add_task(memory.remember, user_id, transcript)
        return {"transcript": transcript, "conversation_id": conv.id, "reply": reply}
    finally:
        db.close()


@router.post("/tts")
async def tts_endpoint(text: str = Form(...), user_id: str = Depends(current_user)):
    if len(text) > settings.max_tts_chars:
        return {"error": "text_too_long", "max_chars": settings.max_tts_chars}

    with timed("tts_total"):
        audio = await TtsChain().run(text)
    if audio:
        return Response(content=audio, media_type="audio/mpeg")
    return {"text": text, "warning": "TTS not available server-side; use browser SpeechSynthesis"}
