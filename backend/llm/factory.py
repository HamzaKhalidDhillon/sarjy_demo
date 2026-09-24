"""Runtime-selectable LLM provider. This is the "common file" that decides which model backs
a given turn: an explicit per-request override wins, then the LLM_PROVIDER env var, then
whichever provider has a key configured, then the deterministic offline fallback.
"""
from backend.core.config import settings
from backend.llm.base import LLMProvider
from backend.llm.gemini_provider import GeminiLLMProvider
from backend.llm.offline_provider import OfflineEchoProvider
from backend.llm.openai_provider import OpenAILLMProvider

_PROVIDERS: dict[str, type[LLMProvider]] = {
    "openai": OpenAILLMProvider,
    "gemini": GeminiLLMProvider,
    "offline": OfflineEchoProvider,
}


def get_llm_provider(name: str | None = None) -> LLMProvider:
    choice = (name or settings.llm_provider or "").strip().lower()

    if choice and choice in _PROVIDERS:
        return _PROVIDERS[choice]()

    if not choice:
        if settings.openai_api_key:
            return OpenAILLMProvider()
        if settings.gemini_api_key:
            return GeminiLLMProvider()

    return OfflineEchoProvider()
