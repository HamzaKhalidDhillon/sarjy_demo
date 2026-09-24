from backend.core.config import settings
from backend.core.errors import ToolError
from backend.tools.base import Tool, ToolResult
from backend.tools.calcom.client import CalComClient


class CheckAvailabilityTool(Tool):
    name = "check_availability"
    description = "Look up real open meeting slots for a date range on the configured Cal.com event type."
    parameters = {
        "type": "object",
        "properties": {
            "start_date": {"type": "string", "description": "ISO date, e.g. 2026-09-25"},
            "end_date": {"type": "string", "description": "ISO date, e.g. 2026-09-26"},
            "timezone": {"type": "string", "description": "IANA timezone, e.g. America/New_York"},
        },
        "required": ["start_date", "end_date"],
    }

    def __init__(self, client: CalComClient | None = None):
        self.client = client or CalComClient()

    async def run(self, start_date: str, end_date: str, timezone: str = "UTC", **_) -> ToolResult:
        try:
            slots_by_date = await self.client.get_slots(
                settings.calcom_event_type_id, start_date, end_date, timezone
            )
        except ToolError as exc:
            return ToolResult(ok=False, error=str(exc))

        flat: list[str] = []
        for slots in slots_by_date.values():
            for slot in slots:
                flat.append(slot.get("time") or slot.get("start"))
        return ToolResult(ok=True, data={"slots": flat})


class BookMeetingTool(Tool):
    name = "book_meeting"
    description = "Book a real meeting on Cal.com at an exact ISO8601 start time. Only call this after the user has explicitly confirmed a specific slot returned by check_availability."
    parameters = {
        "type": "object",
        "properties": {
            "start": {"type": "string", "description": "Exact ISO8601 start time from a real check_availability result"},
            "attendee_name": {"type": "string"},
            "attendee_timezone": {"type": "string"},
            "attendee_email": {"type": "string"},
        },
        "required": ["start", "attendee_name", "attendee_timezone"],
    }

    def __init__(self, client: CalComClient | None = None):
        self.client = client or CalComClient()

    async def run(
        self, start: str, attendee_name: str, attendee_timezone: str,
        attendee_email: str | None = None, **_,
    ) -> ToolResult:
        try:
            data = await self.client.create_booking(
                settings.calcom_event_type_id, start, attendee_name, attendee_timezone, attendee_email
            )
        except ToolError as exc:
            # A definite response came back from Cal.com and it was an error -- not ambiguous.
            return ToolResult(ok=False, error=str(exc), ambiguous=False)
        except Exception as exc:
            # No confirmed response (timeout/connection error) -- outcome genuinely unknown.
            # The caller must NOT treat this as "failed" (could double-book on blind retry) or
            # "succeeded" (could tell the user a false confirmation).
            return ToolResult(ok=False, error=str(exc), ambiguous=True)

        if not data.get("uid"):
            return ToolResult(ok=False, error="Cal.com response missing booking uid", ambiguous=False)
        return ToolResult(ok=True, data=data)


class CancelMeetingTool(Tool):
    name = "cancel_meeting"
    description = "Cancel a previously booked Cal.com meeting by its booking uid."
    parameters = {
        "type": "object",
        "properties": {
            "uid": {"type": "string"},
            "reason": {"type": "string"},
        },
        "required": ["uid"],
    }

    def __init__(self, client: CalComClient | None = None):
        self.client = client or CalComClient()

    async def run(self, uid: str, reason: str = "", **_) -> ToolResult:
        try:
            data = await self.client.cancel_booking(uid, reason)
        except ToolError as exc:
            return ToolResult(ok=False, error=str(exc))
        return ToolResult(ok=True, data=data)
