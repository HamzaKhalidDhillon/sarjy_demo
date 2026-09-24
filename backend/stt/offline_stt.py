import os

from backend.stt.base import SttProvider


class OfflineSTT(SttProvider):
    """Last-resort fallback: never fails, always returns something."""

    name = "offline"

    async def transcribe(self, file_path: str) -> str | None:
        try:
            size = os.path.getsize(file_path)
        except OSError:
            size = 0
        return f"(offline) audio received ({size} bytes)"
