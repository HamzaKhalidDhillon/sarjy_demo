from fastapi import APIRouter

from backend.stt.local_whisper import local_whisper_available
from backend.core.config import settings

router = APIRouter()


@router.get("/health")
def health():
    return {"status": "ok"}


@router.get("/whisper_health")
def whisper_health():
    return {
        "local_whisper_available": local_whisper_available(),
        "use_local_whisper": settings.use_local_whisper,
    }
