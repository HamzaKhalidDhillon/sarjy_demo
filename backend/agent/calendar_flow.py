"""Calendar steps shared by booking a new call and moving an existing one."""
import hashlib
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from sqlalchemy.orm import Session

from backend.agent import templates
from backend.agent.parsing import PARTS_OF_DAY
from backend.agent.state import BookingState
from backend.agent.turn import Turn
from backend.core.logging import timed
from backend.models import BookingAttempt
from backend.tools.calcom import tools as calcom


def idempotency_key(conversation_id: int, event_type_id: int, start: str) -> str:
    return hashlib.sha256(f"{conversation_id}:{event_type_id}:{start}".encode()).hexdigest()


def upcoming_bookings(db: Session, user_id: str) -> list[BookingAttempt]:
    """The user's confirmed future calls across all their chats, from our own records."""
    now = datetime.now(timezone.utc)
    rows = db.query(BookingAttempt).filter(
        BookingAttempt.user_id == user_id, BookingAttempt.status == "confirmed"
    ).all()
    future = [r for r in rows if templates.to_local(r.requested_start) > now]
    return sorted(future, key=lambda r: templates.to_local(r.requested_start))


class CalendarFlow:
    async def _slots(self, start_day, tz: str) -> list[str] | None:
        """Open slots for `start_day` and the week after. One call: a 7-day lookup measured the
        same ~0.5 s as a 1-day one, so there's no second round trip when a day is full."""
        with timed("tool_call:check_availability"):
            result = await calcom.CheckAvailabilityTool().run(
                start_date=start_day.isoformat(), end_date=(start_day + timedelta(days=7)).isoformat(), timezone=tz,
            )
        return result.data.get("slots", []) if result.ok else None

    async def offer_times(self, turn: Turn, day: str | None = None, part: str | None = None) -> str:
        """Open times from `day` (or today) onward, only in the `part` of the day if given."""
        today = datetime.now(ZoneInfo(turn.tz)).date()
        asked = datetime.fromisoformat(day).date() if day else today
        slots = await self._slots(max(asked, today), turn.tz)
        if slots is None:
            return "I can't reach the calendar right now. Could you try again in a moment?"

        note = "That day has already passed. " if asked < today else ""
        if part:
            low, high = PARTS_OF_DAY[part]
            in_part = [s for s in slots if low <= templates.to_local(s, turn.tz).hour < high]
            if not in_part:
                note += f"I don't have any {part} times then. "
            slots = in_part or slots
        return note + templates.slots_offer(slots, turn.tz)

    async def check_time(self, turn: Turn, requested: str, moving_from: str | None = None) -> str:
        """Is `requested` ('YYYY-MM-DDTHH:MM', user's timezone) free? If so, ask to confirm it --
        as a new booking, or as moving the booking that starts at `moving_from`."""
        tz = turn.tz
        wanted = templates.to_local(requested, tz)
        now = datetime.now(ZoneInfo(tz))
        if wanted < now:
            return templates.time_passed(await self._slots(now.date(), tz) or [], tz)

        available = await self._slots(wanted.date(), tz)
        if available is None:
            return "I couldn't check the calendar just now -- could you try again in a moment?"

        match = next((s for s in available if templates.to_local(s, tz) == wanted), None)
        if match and moving_from:
            turn.state.transition(BookingState.CONFIRM_RESCHEDULE, pending_slot_start=match)
            return templates.confirm_reschedule(moving_from, match, tz)
        if match:
            turn.state.transition(BookingState.AWAITING_CONFIRMATION, pending_slot_start=match)
            return f"{templates.pretty_time(match, tz)} is open. Should I go ahead and book it?"

        same_day = [s for s in available if templates.to_local(s, tz).date() == wanted.date()]
        return templates.slot_taken(requested, same_day or available, tz)
