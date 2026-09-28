"""Booking a new call: pick a time -> confirm -> who to invite -> book on Cal.com.

The invite list lives in the conversation state's pending_attendee_email column as
comma-separated emails: the first is the attendee, the rest are Cal.com guests.
"""
from backend.agent import memory, templates
from backend.agent.calendar_flow import CalendarFlow, idempotency_key
from backend.agent.parsing import (
    CONFIRM, DECLINE, ISO_DATETIME, NOT_SAVED_EMAIL, USE_SAVED_EMAIL,
    find_emails, is_confirmation, is_short_non_time,
)
from backend.agent.state import BookingState, ConversationStateMachine
from backend.agent.turn import Turn
from backend.core.errors import ToolError
from backend.core.logging import timed
from backend.models import BookingAttempt
from backend.tools.calcom import tools as calcom
from backend.tools.calcom.client import CalComClient


def invitees(state: ConversationStateMachine) -> list[str]:
    return [e for e in (state.row.pending_attendee_email or "").split(",") if e]


def add_invitees(state: ConversationStateMachine, emails: list[str]) -> None:
    merged = list(dict.fromkeys(invitees(state) + emails))
    state.transition(state.state, pending_attendee_email=",".join(merged))


class BookingFlow(CalendarFlow):
    async def start(self, turn: Turn) -> str:
        """ "Book a call" / "book me a call tomorrow at 3pm" / "...on Friday afternoon"."""
        turn.state.transition(BookingState.COLLECTING_TIME, pending_slot_start=None, pending_attendee_email=None)
        add_invitees(turn.state, find_emails(turn.message))
        ask = await turn.read_time()
        if ask.exact:
            return await self.check_time(turn, ask.exact)
        return await self.offer_times(turn, ask.day, ask.part)

    async def collect_time(self, turn: Turn) -> str:
        emails = find_emails(turn.message)
        add_invitees(turn.state, emails)
        # Short non-time replies are handled before asking the LLM, which would otherwise guess
        # a time from the conversation
        if is_short_non_time(turn.message):
            if emails:
                return f"Thanks, I'll send the invite to {templates.recipients(invitees(turn.state))}. Which time works for you?"
            if CONFIRM.search(turn.message):
                return "Which of those times would you like? You can say something like 'the 9:30 one' or 'Thursday at 10'."

        ask = await turn.read_time()
        if ask.exact:
            return await self.check_time(turn, ask.exact)
        if ask.day or ask.part:
            return await self.offer_times(turn, ask.day, ask.part)
        return await turn.answer_and_nudge(
            "Whenever you're ready, just tell me a day and time for the call, or say 'never mind' to skip it."
        )

    async def confirm(self, turn: Turn) -> str:
        """A time is waiting for a yes."""
        state, message = turn.state, turn.message
        pending = templates.pretty_time(state.row.pending_slot_start, turn.tz)
        emails = find_emails(message)
        add_invitees(state, emails)

        if is_confirmation(message):
            if invitees(state):
                return await self.create(turn)
            state.transition(BookingState.AWAITING_CONTACT)
            return templates.ask_which_email(turn.saved_email) if turn.saved_email else templates.ask_for_email()

        if emails and not ISO_DATETIME.search(message):
            return f"Thanks, I'll send the invite to {templates.recipients(invitees(state))}. Shall I book {pending}?"

        # "actually make it 11am instead" -> check the new time right away
        ask = await turn.read_time()
        if ask.exact:
            return await self.check_time(turn, ask.exact)
        if ask.day or ask.part:
            state.transition(BookingState.COLLECTING_TIME, pending_slot_start=None)
            return await self.offer_times(turn, ask.day, ask.part)
        if DECLINE.search(message):
            state.transition(BookingState.COLLECTING_TIME, pending_slot_start=None)
            return "No problem -- what time would work better?"
        return await turn.answer_and_nudge(f"I still have {pending} ready for you. Say 'yes' to book it, or 'never mind' to drop it.")

    async def collect_contact(self, turn: Turn) -> str:
        """Who gets the invite. Offers the email on file; extra emails become guests."""
        message, saved = turn.message, turn.saved_email
        emails = find_emails(message)
        wants_saved = (CONFIRM.search(message) and not DECLINE.search(message)) or USE_SAVED_EMAIL.search(message)
        if saved and wants_saved and not NOT_SAVED_EMAIL.search(message):
            emails = [saved] + emails
        if emails:
            add_invitees(turn.state, emails)
            turn.state.transition(BookingState.AWAITING_CONFIRMATION)
            return await self.create(turn)

        if saved and DECLINE.search(message):
            return "No problem -- which email should I send the invite to instead?"
        if "@" in message or any(w in message.lower() for w in ("email", "mail")):
            return "That doesn't look like a complete email address -- could you say or type it again?"
        pending = templates.pretty_time(turn.state.row.pending_slot_start, turn.tz)
        return await turn.answer_and_nudge(f"To finish booking {pending}, I just need an email address for the invite.")

    async def create(self, turn: Turn) -> str:
        """Book on Cal.com. Guarded by an idempotency key, so a repeated "yes" can't double-book,
        and a reply only says "booked" when Cal.com returned a real booking uid."""
        db, state, tz = turn.db, turn.state, turn.tz
        start = state.row.pending_slot_start
        try:
            event_type_id = await calcom.get_event_type_id(CalComClient())
        except ToolError as exc:
            return templates.booking_failed(str(exc))
        people = invitees(state)
        key = idempotency_key(turn.conversation_id, event_type_id, start)

        attempt = db.query(BookingAttempt).filter(BookingAttempt.idempotency_key == key).first()
        if attempt and attempt.status == "confirmed":
            self._finish(state)
            return templates.booked(start, attempt.calcom_booking_uid, people, tz)
        if not attempt:
            attempt = BookingAttempt(
                user_id=turn.user_id, conversation_id=turn.conversation_id, idempotency_key=key,
                event_type_id=event_type_id, requested_start=start, status="pending",
            )
            db.add(attempt)
            db.commit()

        with timed("tool_call:book_meeting"):
            result = await calcom.BookMeetingTool().run(
                start=start, attendee_name=turn.user_id, attendee_timezone=tz,
                attendee_email=people[0], guests=people[1:],
            )

        if result.ok:
            attempt.status = "confirmed"
            attempt.calcom_booking_uid = result.data.get("uid")
            attempt.calcom_booking_id = result.data.get("id")
            db.commit()
            memory.save_fact(db, turn.user_id, "email", people[0])  # offered next time
            self._finish(state)
            return templates.booked(start, attempt.calcom_booking_uid, people, tz)

        if result.ambiguous:
            # No response from Cal.com: it may or may not have booked. Stay "pending" rather
            # than guess either way.
            return templates.booking_pending_reconciliation()

        attempt.status = "failed"
        attempt.error_message = result.error
        db.commit()
        state.transition(BookingState.COLLECTING_TIME, pending_slot_start=None, pending_attendee_email=None)
        return templates.booking_failed(result.error or "unknown error")

    @staticmethod
    def _finish(state: ConversationStateMachine) -> None:
        state.transition(BookingState.BOOKED, pending_slot_start=None, pending_attendee_email=None)
