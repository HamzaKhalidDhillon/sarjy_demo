from backend.tools.base import Tool


class ToolRegistry:
    def __init__(self):
        self._tools: dict[str, Tool] = {}

    def register(self, tool: Tool) -> None:
        self._tools[tool.name] = tool

    def get(self, name: str) -> Tool | None:
        return self._tools.get(name)

    def list_specs(self) -> list[dict]:
        return [t.spec() for t in self._tools.values()]


def build_default_registry() -> ToolRegistry:
    from backend.tools.calcom.tools import BookMeetingTool, CancelMeetingTool, CheckAvailabilityTool

    registry = ToolRegistry()
    registry.register(CheckAvailabilityTool())
    registry.register(BookMeetingTool())
    registry.register(CancelMeetingTool())
    return registry
