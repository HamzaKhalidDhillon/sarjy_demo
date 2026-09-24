import httpx

from backend.core.config import settings
from backend.core.http import request_with_retry
from backend.tts.base import TtsProvider


class OpenAITTS(TtsProvider):
    name = "openai_tts"

    async def synthesize(self, text: str) -> bytes | None:
        if not settings.openai_api_key:
            return None
        url = "https://api.openai.com/v1/audio/speech"
        headers = {"Authorization": f"Bearer {settings.openai_api_key}"}
        # Fix for a real bug in the original adapter: OpenAI's speech endpoint requires "input",
        # not "text" -- the old payload silently failed with a 400 that was swallowed.
        payload = {
            "model": settings.openai_tts_model,
            "input": text,
            "voice": "alloy",
            "response_format": "mp3",
        }
        try:
            async with httpx.AsyncClient(timeout=60.0) as client:
                response = await request_with_retry(client, "POST", url, json=payload, headers=headers)
            if response.status_code >= 400:
                return None
            return response.content
        except Exception:
            return None
