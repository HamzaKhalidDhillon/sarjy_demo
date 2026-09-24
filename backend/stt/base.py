from abc import ABC, abstractmethod


class SttProvider(ABC):
    name: str = "base"

    @abstractmethod
    async def transcribe(self, file_path: str) -> str | None:
        """Return transcript text, or None if this provider can't/didn't produce one."""
        raise NotImplementedError
