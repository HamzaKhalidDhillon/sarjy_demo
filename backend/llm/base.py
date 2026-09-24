from abc import ABC, abstractmethod


class LLMProvider(ABC):
    name: str = "base"

    @abstractmethod
    async def complete(self, messages: list[dict], **kwargs) -> str:
        """messages: [{"role": "system"|"user"|"assistant", "content": str}, ...]"""
        raise NotImplementedError
