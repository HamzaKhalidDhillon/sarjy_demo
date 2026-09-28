import os
import tempfile
from typing import Optional

from fastapi import APIRouter, Depends, File, Form, Response, UploadFile
from sqlalchemy.orm import Session

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


@router.post("/stt")
async def stt(
    conversation_id: Optional[int] = Form(None),
    audio: UploadFile = File(...),
    user_id: str = Depends(current_user),
):
    """Accept an audio blob, transcribe it (bounded size, streamed to disk), run it through the
    agent like a typed message, and persist both turns -- same contract as before."""
    total = 0
    with tempfile.NamedTemporaryFile(delete=False, suffix=".webm") as tmp:
        tmp_path = tmp.name
        while True:
            chunk = await audio.read(1024 * 64)
            if not chunk:
                break
            total += len(chunk)
            if total > settings.max_upload_bytes:
                tmp.close()
                os.unlink(tmp_path)
                return {"error": "file_too_large", "max_bytes": settings.max_upload_bytes}
            tmp.write(chunk)

    try:
        with timed("stt_total"):
            transcript = await SttChain().run(tmp_path)
    finally:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass

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
