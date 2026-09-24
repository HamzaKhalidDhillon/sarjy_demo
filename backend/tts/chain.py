from backend.core.logging import timed
from backend.tts.base import TtsProvider
from backend.tts.none_tts import NoneTTS
from backend.tts.openai_tts import OpenAITTS


class TtsChain:
    def __init__(self, providers: list[TtsProvider] | None = None):
        self.providers = providers or [OpenAITTS(), NoneTTS()]

    async def run(self, text: str) -> bytes | None:
        for provider in self.providers:
            with timed(f"tts:{provider.name}"):
                audio = await provider.synthesize(text)
            if audio:
                return audio
        return None
