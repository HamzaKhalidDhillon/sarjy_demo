import os
import httpx

from backend.core.config import settings
from backend.core.http import request_with_retry
from backend.stt.base import SttProvider


class OpenAIWhisperSTT(SttProvider):
    name = "openai_whisper"

    def available(self) -> bool:
        return bool(settings.openai_api_key)

    async def transcribe(self, file_path: str) -> str | None:
        if not self.available():
            return None
        url = "https://api.openai.com/v1/audio/transcriptions"
        headers = {"Authorization": f"Bearer {settings.openai_api_key}"}
        try:
            async with httpx.AsyncClient(timeout=60.0) as client:
                with open(file_path, "rb") as f:
                    files = {"file": (os.path.basename(file_path), f), "model": (None, "whisper-1")}
                    # max_attempts=1: retrying a multipart upload would need to reseek the file
                    # handle between attempts; the STT chain's fallback (local whisper -> offline)
                    # is the reliability net here instead.
                    response = await request_with_retry(
                        client, "POST", url, headers=headers, files=files, max_attempts=1
                    )
            if response.status_code >= 400:
                return None
            return response.json().get("text", "")
        except Exception:
            return None
