import pytest

from backend.core.config import settings
from backend.llm.factory import get_llm_provider
from backend.llm.gemini_provider import GeminiLLMProvider
from backend.llm.offline_provider import OfflineEchoProvider
from backend.llm.openai_provider import OpenAILLMProvider


@pytest.fixture(autouse=True)
def _reset_settings():
    original = (settings.openai_api_key, settings.gemini_api_key, settings.llm_provider)
    yield
    settings.openai_api_key, settings.gemini_api_key, settings.llm_provider = original


def test_defaults_to_offline_with_no_keys():
    settings.openai_api_key = ""
    settings.gemini_api_key = ""
    settings.llm_provider = ""
    assert isinstance(get_llm_provider(), OfflineEchoProvider)


def test_auto_selects_openai_when_key_present():
    settings.openai_api_key = "sk-test"
    settings.gemini_api_key = ""
    settings.llm_provider = ""
    assert isinstance(get_llm_provider(), OpenAILLMProvider)


def test_auto_selects_gemini_when_only_gemini_key_present():
    settings.openai_api_key = ""
    settings.gemini_api_key = "g-test"
    settings.llm_provider = ""
    assert isinstance(get_llm_provider(), GeminiLLMProvider)


def test_per_request_override_wins_over_env():
    settings.openai_api_key = "sk-test"
    settings.llm_provider = "openai"
    assert isinstance(get_llm_provider("offline"), OfflineEchoProvider)


def test_env_provider_wins_over_auto_detect():
    settings.openai_api_key = "sk-test"
    settings.gemini_api_key = "g-test"
    settings.llm_provider = "gemini"
    assert isinstance(get_llm_provider(), GeminiLLMProvider)
