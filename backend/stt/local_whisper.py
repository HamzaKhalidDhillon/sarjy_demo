from typing import Any

from backend.core.config import settings
from backend.core.logging import logger
from backend.stt.base import SttProvider

_model: Any = None
_load_attempted = False


def _get_model() -> Any:
    global _model, _load_attempted
    if _load_attempted:
        return _model
    _load_attempted = True
    if not settings.use_local_whisper:
        return None
    try:
        from faster_whisper import WhisperModel

        _model = WhisperModel(settings.local_whisper_model_size, device=settings.local_whisper_device)
    except Exception:
        logger.exception("failed to load local faster-whisper model")
        _model = None
    return _model


def local_whisper_available() -> bool:
    return _get_model() is not None


class LocalFasterWhisperSTT(SttProvider):
    name = "local_whisper"

    async def transcribe(self, file_path: str) -> str | None:
        model = _get_model()
        if model is None:
            return None
        try:
            segments, _info = model.transcribe(file_path, beam_size=5)
            return "".join(s.text for s in segments).strip()
        except Exception:
            logger.exception("local whisper transcription failed")
            return None
