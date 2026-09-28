"""Central runtime configuration. One place to read env vars from."""
from functools import lru_cache

from pydantic import BaseSettings


class Settings(BaseSettings):
    # LLM providers
    openai_api_key: str = ""
    gemini_api_key: str = ""
    llm_provider: str = ""  # "openai" | "gemini" | "offline" | "" (auto-detect)
    openai_chat_model: str = "gpt-4o-mini"
    openai_tts_model: str = "gpt-4o-mini-tts"
    gemini_model: str = "text-bison-001"

    # STT
    use_local_whisper: bool = False
    local_whisper_model_size: str = "small"
    local_whisper_device: str = "cpu"
    max_upload_bytes: int = 5 * 1024 * 1024

    # TTS
    max_tts_chars: int = 2000

    # Cal.com
    calcom_api_key: str = ""
    calcom_username: str = ""
    calcom_event_type_id: int = 0
    # Cal.com versions its API per-endpoint via this header. Both defaults below are confirmed
    # working against the live API (get_slots and a real create_booking + cancel_booking round
    # trip) -- override via .env only if Cal.com changes them in the future.
    calcom_api_version_slots: str = "2024-09-04"
    calcom_api_version_bookings: str = "2026-02-25"
    calcom_base_url: str = "https://api.cal.com/v2"

    # HTTP / persistence
    database_url: str = "sqlite:///./data/data.db"
    allowed_origins: str = "*"  # comma-separated list, or "*"

    class Config:
        env_file = ".env"
        case_sensitive = False

    @property
    def allowed_origins_list(self) -> list[str]:
        if self.allowed_origins.strip() == "*":
            return ["*"]
        return [o.strip() for o in self.allowed_origins.split(",") if o.strip()]


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
