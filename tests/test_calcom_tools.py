import pytest

from backend.core.errors import ToolError
from backend.tools.calcom.tools import BookMeetingTool, CheckAvailabilityTool


class FakeCalComClient:
    def __init__(self, slots=None, booking_data=None, raise_tool_error=False, raise_network_error=False):
        self._slots = slots or {}
        self._booking_data = booking_data or {}
        self._raise_tool_error = raise_tool_error
        self._raise_network_error = raise_network_error

    async def get_slots(self, event_type_id, start, end, timezone="UTC"):
        if self._raise_tool_error:
            raise ToolError("boom")
        return self._slots

    async def create_booking(self, event_type_id, start, attendee_name, attendee_timezone, attendee_email=None):
        if self._raise_tool_error:
            raise ToolError("Cal.com create_booking failed: 409 slot taken")
        if self._raise_network_error:
            raise ConnectionError("network blip")
        return self._booking_data


@pytest.mark.asyncio
async def test_check_availability_flattens_slots():
    client = FakeCalComClient(slots={"2026-09-25": [{"time": "2026-09-25T15:00:00Z"}]})
    result = await CheckAvailabilityTool(client=client).run(start_date="2026-09-25", end_date="2026-09-25")
    assert result.ok
    assert result.data["slots"] == ["2026-09-25T15:00:00Z"]


@pytest.mark.asyncio
async def test_check_availability_surfaces_tool_error():
    client = FakeCalComClient(raise_tool_error=True)
    result = await CheckAvailabilityTool(client=client).run(start_date="2026-09-25", end_date="2026-09-25")
    assert not result.ok
    assert not result.ambiguous


@pytest.mark.asyncio
async def test_book_meeting_success_requires_real_uid():
    client = FakeCalComClient(booking_data={"uid": "abc123", "id": 1, "status": "accepted"})
    result = await BookMeetingTool(client=client).run(
        start="2026-09-25T15:00:00Z", attendee_name="demo-user", attendee_timezone="UTC"
    )
    assert result.ok
    assert result.data["uid"] == "abc123"


@pytest.mark.asyncio
async def test_book_meeting_missing_uid_is_not_ok():
    client = FakeCalComClient(booking_data={"status": "accepted"})
    result = await BookMeetingTool(client=client).run(
        start="2026-09-25T15:00:00Z", attendee_name="demo-user", attendee_timezone="UTC"
    )
    assert not result.ok
    assert not result.ambiguous


@pytest.mark.asyncio
async def test_book_meeting_definite_failure_is_not_ambiguous():
    client = FakeCalComClient(raise_tool_error=True)
    result = await BookMeetingTool(client=client).run(
        start="2026-09-25T15:00:00Z", attendee_name="demo-user", attendee_timezone="UTC"
    )
    assert not result.ok
    assert not result.ambiguous


@pytest.mark.asyncio
async def test_book_meeting_network_error_is_ambiguous_not_failed():
    client = FakeCalComClient(raise_network_error=True)
    result = await BookMeetingTool(client=client).run(
        start="2026-09-25T15:00:00Z", attendee_name="demo-user", attendee_timezone="UTC"
    )
    assert not result.ok
    assert result.ambiguous
