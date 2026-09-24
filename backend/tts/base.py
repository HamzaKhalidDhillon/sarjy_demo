from abc import ABC, abstractmethod


class TtsProvider(ABC):
    name: str = "base"

    @abstractmethod
    async def synthesize(self, text: str) -> bytes | None:
        raise NotImplementedError
