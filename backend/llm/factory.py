"""Picks the LLM provider for a turn: a per-request override wins, then the LLM_PROVIDER env
var, then OpenAI if a key is configured, then the offline echo provider (handy for running the
app and tests without any API key). A new provider is one class plus one entry below.
"""
from backend.core.config import settings
from backend.llm.base import LLMProvider
from backend.llm.offline_provider import OfflineEchoProvider
from backend.llm.openai_provider import OpenAILLMProvider

_PROVIDERS: dict[str, type[LLMProvider]] = {
    "openai": OpenAILLMProvider,
    "offline": OfflineEchoProvider,
}


def get_llm_provider(name: str | None = None) -> LLMProvider:
    choice = (name or settings.llm_provider or "").strip().lower()

    if choice and choice in _PROVIDERS:
        return _PROVIDERS[choice]()

    if not choice and settings.openai_api_key:
        return OpenAILLMProvider()

    return OfflineEchoProvider()
