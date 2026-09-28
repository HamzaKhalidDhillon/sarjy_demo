import os
import tempfile

from fastapi import APIRouter, Depends, File, Form, Response, UploadFile

from backend.core.config import settings
from backend.core.logging import timed
from backend.routers.auth import current_user
from backend.stt.chain import SttChain
from backend.tts.chain import TtsChain

router = APIRouter(dependencies=[Depends(current_user)])  # both endpoints spend API credits

AUDIO_TYPES = (".webm", ".mp4", ".m4a", ".ogg", ".wav", ".mp3")


async def _save_upload(audio: UploadFile) -> str | None:
    """Stream the upload to a temp file, refusing anything over the size limit. Keeps the real
    audio type (Safari records mp4, Chrome/Firefox webm) so Whisper reads it correctly."""
    suffix = os.path.splitext(audio.filename or "")[1].lower()
    total = 0
    with tempfile.NamedTemporaryFile(delete=False, suffix=suffix if suffix in AUDIO_TYPES else ".webm") as tmp:
        while chunk := await audio.read(64 * 1024):
            total += len(chunk)
            if total > settings.max_upload_bytes:
                tmp.close()
                os.unlink(tmp.name)
                return None
            tmp.write(chunk)
    return tmp.name


@router.post("/transcribe")
async def transcribe(audio: UploadFile = File(...)):
    """Speech to text only. The UI shows what you said, then sends it to /message like a typed
    message."""
    path = await _save_upload(audio)
    if path is None:
        return {"error": "file_too_large", "max_bytes": settings.max_upload_bytes}
    try:
        with timed("stt_total"):
            return {"transcript": await SttChain().run(path)}
    finally:
        os.unlink(path)


@router.post("/tts")
async def tts(text: str = Form(...)):
    if len(text) > settings.max_tts_chars:
        return {"error": "text_too_long", "max_chars": settings.max_tts_chars}
    with timed("tts_total"):
        audio = await TtsChain().run(text)
    if audio:
        return Response(content=audio, media_type="audio/mpeg")
    return {"text": text, "warning": "TTS not available server-side; use browser SpeechSynthesis"}
