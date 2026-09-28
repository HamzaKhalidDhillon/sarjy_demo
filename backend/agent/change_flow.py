"""Cancelling or moving an existing call: which call -> confirm -> Cal.com cancel/reschedule."""
import re

from backend.agent import templates
from backend.agent.calendar_flow import CalendarFlow, idempotency_key, upcoming_bookings
from backend.agent.parsing import KEEP_IT, TimeAsk, is_confirmation
from backend.agent.state import BookingState
from backend.agent.turn import Turn
from backend.core.logging import timed
from backend.models import BookingAttempt
from backend.tools.calcom import tools as calcom

CANCEL, RESCHEDULE = "cancel", "reschedule"


def match_booking(bookings: list[BookingAttempt], ask: TimeAsk, tz: str) -> BookingAttempt | None:
    """Which booking the user means ("my Friday call", "the 11am one"), if it's unambiguous."""
    if ask.exact:
        wanted = templates.to_local(ask.exact, tz)
        hits = [b for b in bookings if templates.to_local(b.requested_start, tz) == wanted]
    elif ask.day:
        hits = [b for b in bookings if templates.to_local(b.requested_start, tz).date().isoformat() == ask.day]
    else:
        hits = []
    return hits[0] if len(hits) == 1 else None


class ChangeFlow(CalendarFlow):
    async def start(self, turn: Turn, action: str) -> str:
        """ "Cancel my Friday call" / "move my call to Monday at 10"."""
        bookings = upcoming_bookings(turn.db, turn.user_id)
        if not bookings:
            turn.state.transition(BookingState.IDLE)
            return templates.no_upcoming()

        ask = await turn.read_time()
        matched = match_booking(bookings, ask, turn.tz)
        target = bookings[0] if len(bookings) == 1 else matched
        if not target:
            choose = BookingState.CHOOSE_TO_CANCEL if action == CANCEL else BookingState.CHOOSE_TO_RESCHEDULE
            turn.state.transition(choose, target_booking_id=None, pending_slot_start=None)
            return templates.which_booking(bookings, action, turn.tz)

        # With one booking, a time that isn't that booking's is the new time they want
        new_time = ask if action == RESCHEDULE and not matched else TimeAsk()
        return await self._begin(turn, target, action, new_time)

    async def choose(self, turn: Turn, action: str) -> str:
        bookings = upcoming_bookings(turn.db, turn.user_id)
        if not bookings:
            turn.state.transition(BookingState.IDLE)
            return templates.no_upcoming()
        target = match_booking(bookings, await turn.read_time(), turn.tz)
        if target:
            return await self._begin(turn, target, action)
        if KEEP_IT.search(turn.message):
            turn.state.transition(BookingState.IDLE)
            return "Okay, I'll leave your calls as they are."
        return templates.which_booking(bookings, action, turn.tz)

    async def confirm_cancel(self, turn: Turn) -> str:
        target = self._target(turn)
        if not target or target.status != "confirmed":
            turn.state.transition(BookingState.IDLE, target_booking_id=None)
            return "I can't find that booking anymore, so there's nothing to cancel."
        when = templates.pretty_time(target.requested_start, turn.tz)

        said_cancel = re.search(r"\bcancel\b", turn.message, re.IGNORECASE)
        if KEEP_IT.search(turn.message) and not re.search(r"\bcancel (it|that)\b", turn.message, re.IGNORECASE):
            turn.state.transition(BookingState.IDLE, target_booking_id=None)
            return f"Okay, I'll keep your call on {when}."
        if not (is_confirmation(turn.message) or said_cancel):
            return await turn.answer_and_nudge(f"Should I cancel your call on {when}? Say yes or no.")

        with timed("tool_call:cancel_meeting"):
            result = await calcom.CancelMeetingTool().run(uid=target.calcom_booking_uid, reason="Cancelled via Sarjy")
        if not result.ok:
            turn.state.transition(BookingState.IDLE, target_booking_id=None)
            return f"I couldn't cancel it just now ({result.error}), so it's still booked. Want me to try again?"
        target.status = "cancelled"
        turn.db.commit()
        turn.state.transition(BookingState.CHANGED, target_booking_id=None)
        return templates.cancelled_booking(target.requested_start, turn.tz)

    async def reschedule_time(self, turn: Turn) -> str:
        target = self._target(turn)
        if not target:
            turn.state.transition(BookingState.IDLE)
            return "I can't find that booking anymore."
        ask = await turn.read_time()
        if ask.exact:
            return await self.check_time(turn, ask.exact, moving_from=target.requested_start)
        if ask.day or ask.part:
            return await self.offer_times(turn, ask.day, ask.part)
        when = templates.pretty_time(target.requested_start, turn.tz)
        return await turn.answer_and_nudge(f"What day and time would you like instead of {when}? Or say 'never mind' to keep it.")

    async def confirm_reschedule(self, turn: Turn) -> str:
        target, new_start = self._target(turn), turn.state.row.pending_slot_start
        if not target or not new_start:
            turn.state.transition(BookingState.IDLE)
            return "I lost track of that change -- could you tell me again which call to move?"
        if not is_confirmation(turn.message):
            turn.state.transition(BookingState.RESCHEDULE_TIME, pending_slot_start=None)
            return await self.reschedule_time(turn)

        with timed("tool_call:reschedule_meeting"):
            result = await calcom.RescheduleMeetingTool().run(uid=target.calcom_booking_uid, start=new_start)
        if not result.ok:
            turn.state.transition(BookingState.IDLE, target_booking_id=None, pending_slot_start=None)
            old = templates.pretty_time(target.requested_start, turn.tz)
            return f"I couldn't move it just now ({result.error}), so it's still on {old}."

        # Cal.com gives the moved booking a new uid
        target.requested_start = new_start
        target.calcom_booking_uid = result.data.get("uid")
        target.calcom_booking_id = result.data.get("id")
        target.idempotency_key = idempotency_key(target.conversation_id, target.event_type_id, new_start)
        turn.db.commit()
        turn.state.transition(BookingState.CHANGED, target_booking_id=None, pending_slot_start=None)
        return templates.rescheduled(new_start, target.calcom_booking_uid, turn.tz)

    async def _begin(self, turn: Turn, target: BookingAttempt, action: str, new_time: TimeAsk = TimeAsk()) -> str:
        if action == CANCEL:
            turn.state.transition(BookingState.CONFIRM_CANCEL, target_booking_id=target.id)
            return templates.confirm_cancel(target.requested_start, turn.tz)

        turn.state.transition(BookingState.RESCHEDULE_TIME, target_booking_id=target.id, pending_slot_start=None)
        if new_time.exact:
            return await self.check_time(turn, new_time.exact, moving_from=target.requested_start)
        if new_time.day or new_time.part:
            return await self.offer_times(turn, new_time.day, new_time.part)
        return templates.ask_new_time(target.requested_start, turn.tz)

    @staticmethod
    def _target(turn: Turn) -> BookingAttempt | None:
        target_id = turn.state.row.target_booking_id
        return turn.db.get(BookingAttempt, target_id) if target_id else None
