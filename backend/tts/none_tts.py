from backend.tts.base import TtsProvider


class NoneTTS(TtsProvider):
    """Terminal fallback: signals the frontend should use browser speechSynthesis."""

    name = "none"

    async def synthesize(self, text: str) -> bytes | None:
        return None
