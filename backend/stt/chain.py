from backend.core.logging import timed
from backend.stt.base import SttProvider
from backend.stt.local_whisper import LocalFasterWhisperSTT
from backend.stt.offline_stt import OfflineSTT
from backend.stt.openai_whisper import OpenAIWhisperSTT


class SttChain:
    """Tries each provider in order, first non-empty transcript wins."""

    def __init__(self, providers: list[SttProvider] | None = None):
        self.providers = providers or [OpenAIWhisperSTT(), LocalFasterWhisperSTT(), OfflineSTT()]

    async def run(self, file_path: str) -> str:
        for provider in self.providers:
            with timed(f"stt:{provider.name}"):
                transcript = await provider.transcribe(file_path)
            if transcript:
                return transcript
        return ""  # OfflineSTT always returns something, so this shouldn't be reached
