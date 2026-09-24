class ProviderError(Exception):
    """Raised when an external LLM/STT/TTS provider call fails after retries."""


class ToolError(Exception):
    """Raised when a tool call fails after retries."""


class GuardrailBlocked(Exception):
    """Raised when an input or output guardrail refuses to proceed."""

    def __init__(self, reason: str):
        self.reason = reason
        super().__init__(reason)
