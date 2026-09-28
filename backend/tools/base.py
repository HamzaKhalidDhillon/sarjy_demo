"""Tool contract, shaped like an MCP tool (name, description, JSON-schema parameters, async
run()) so the Cal.com tools could be exposed through an MCP server without changing them.
"""
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any


@dataclass
class ToolResult:
    ok: bool
    data: dict[str, Any] = field(default_factory=dict)
    error: str | None = None
    # True only when ok=False and we genuinely don't know the outcome (e.g. a network error with
    # no response received) -- as opposed to a definite failure the server told us about.
    ambiguous: bool = False


class Tool(ABC):
    name: str
    description: str
    parameters: dict[str, Any]  # JSON-schema-shaped

    @abstractmethod
    async def run(self, **kwargs) -> ToolResult:
        raise NotImplementedError

    def spec(self) -> dict[str, Any]:
        return {"name": self.name, "description": self.description, "parameters": self.parameters}
