from backend.llm.base import LLMProvider


class OfflineEchoProvider(LLMProvider):
    """Deterministic fallback used when no provider key is configured or all providers fail."""

    name = "offline"

    async def complete(self, messages: list[dict], **kwargs) -> str:
        last_user = next((m["content"] for m in reversed(messages) if m["role"] == "user"), "")
        return f"(offline) I heard: {last_user}"
