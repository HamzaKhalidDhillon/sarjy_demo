"""One conversation turn: guardrails -> memory -> route to the right flow -> output guardrail.

Booking, cancelling and rescheduling are explicit state machines (booking_flow.py,
change_flow.py), not free-form LLM reasoning. The LLM chats and reads times; it never decides
that something was booked. Any reply claiming a booking/cancel/reschedule that Cal.com didn't
confirm in this turn is replaced with the facts from our own records.
"""
from sqlalchemy.orm import Session

from backend.agent import memory, templates
from backend.agent.booking_flow import BookingFlow
from backend.agent.calendar_flow import upcoming_bookings
from backend.agent.change_flow import CANCEL, RESCHEDULE, ChangeFlow
from backend.agent.guardrails import InputGuardrail, OutputGuardrail
from backend.agent.parsing import (
    BOOK_INTENT, CANCEL_INTENT, LIST_BOOKINGS, RESCHEDULE_INTENT, STATUS_QUESTION, find_emails, safe_tz,
)
from backend.agent.state import BookingState, ConversationStateMachine
from backend.agent.turn import Turn
from backend.core.logging import logger, timed
from backend.llm.factory import get_llm_provider
from backend.models import Message

# States where nothing is in progress, so a new request (book / cancel / move) can start
FREE_STATES = (BookingState.IDLE, BookingState.BOOKED, BookingState.CHANGED)
CHANGE_STATES = (
    BookingState.CHOOSE_TO_CANCEL, BookingState.CHOOSE_TO_RESCHEDULE,
    BookingState.RESCHEDULE_TIME, BookingState.CONFIRM_RESCHEDULE,
)


def recent_history(db: Session, conversation_id: int, limit: int = 10) -> list[dict]:
    """The last few messages of this chat. The router saves the current user message before
    calling the agent, so that newest row is skipped."""
    rows = (
        db.query(Message).filter(Message.conversation_id == conversation_id)
        .order_by(Message.id.desc()).limit(limit + 1).all()
    )
    return [{"role": r.role, "content": r.content} for r in reversed(rows[1:])]


class Agent:
    def __init__(self):
        self.booking = BookingFlow()
        self.changes = ChangeFlow()
        # What to do with a message, given where the conversation is
        self.handlers = {
            BookingState.COLLECTING_TIME: self.booking.collect_time,
            BookingState.AWAITING_CONFIRMATION: self.booking.confirm,
            BookingState.AWAITING_CONTACT: self.booking.collect_contact,
            BookingState.CHOOSE_TO_CANCEL: lambda turn: self.changes.choose(turn, CANCEL),
            BookingState.CHOOSE_TO_RESCHEDULE: lambda turn: self.changes.choose(turn, RESCHEDULE),
            BookingState.CONFIRM_CANCEL: self.changes.confirm_cancel,
            BookingState.RESCHEDULE_TIME: self.changes.reschedule_time,
            BookingState.CONFIRM_RESCHEDULE: self.changes.confirm_reschedule,
        }

    async def run_turn(
        self, db: Session, user_id: str, conversation_id: int, message: str,
        llm_provider_name: str | None = None, timezone_name: str | None = None,
    ) -> str:
        state = ConversationStateMachine(db, conversation_id)
        tz = safe_tz(timezone_name)

        was_changing = state.state in CHANGE_STATES
        if state.maybe_cancel(message):
            # "never mind" while moving a call keeps the call; it only drops the change
            return "Okay, I'll leave your call as it is." if was_changing else templates.cancelled()

        with timed("guardrail_input"):
            verdict = InputGuardrail.check(message)
        if not verdict.allowed:
            logger.info("input guardrail blocked turn: %s", verdict.reason)
            return templates.GUARDRAIL_REFUSAL

        with timed("memory_recall"):
            memories = memory.recall(db, user_id)
        turn = Turn(
            db=db, user_id=user_id, conversation_id=conversation_id, message=message,
            llm=get_llm_provider(llm_provider_name), state=state, tz=tz,
            history=recent_history(db, conversation_id),
            context=memory.format_memories_for_prompt(memories),
            saved_email=memory.saved_email(memories),
        )

        if STATUS_QUESTION.search(message) or LIST_BOOKINGS.search(message):
            return self.facts(turn)

        # The LLM knows the user's real upcoming calls ("should I prepare anything for Friday?");
        # anything it claims about them still goes through the output guardrail below.
        upcoming = upcoming_bookings(db, user_id)
        if upcoming:
            times = ", ".join(templates.pretty_time(b.requested_start, tz) for b in upcoming)
            turn.context = f"{turn.context} The user's upcoming calls, from the booking system: {times}.".strip()

        state_before = state.state
        reply = await self.route(turn)

        # A booking/cancel/reschedule only counts in the turn Cal.com confirmed it. Otherwise the
        # LLM could repeat an earlier confirmation from the history.
        done = (BookingState.BOOKED, BookingState.CHANGED)
        verified = state_before not in done and state.state in done
        if not OutputGuardrail.check(reply, booking_verified=verified).allowed:
            logger.warning("output guardrail replaced an unverified booking claim")
            reply = self.facts(turn)
        return reply

    async def route(self, turn: Turn) -> str:
        current, message = turn.state.state, turn.message
        if current in FREE_STATES:
            if CANCEL_INTENT.search(message):
                return await self.changes.start(turn, CANCEL)
            if RESCHEDULE_INTENT.search(message):
                return await self.changes.start(turn, RESCHEDULE)
            if BOOK_INTENT.search(message):
                return await self.booking.start(turn)
            if current == BookingState.BOOKED and find_emails(message):
                return templates.already_booked_email(find_emails(message))

        handler = self.handlers.get(current)
        return await handler(turn) if handler else await turn.llm_reply()

    @staticmethod
    def facts(turn: Turn) -> str:
        """The truth about the user's bookings, from our own records. Answers "is my meeting
        booked?" / "what calls do I have?", and replaces claims the LLM wasn't allowed to make."""
        state, pending = turn.state.state, turn.state.row.pending_slot_start
        if pending and state == BookingState.AWAITING_CONTACT:
            return templates.holding(pending, "Just tell me your email address and I'll book it.", turn.tz)
        if pending and state == BookingState.AWAITING_CONFIRMATION:
            return templates.holding(pending, "Say 'yes' and I'll book it.", turn.tz)
        upcoming = upcoming_bookings(turn.db, turn.user_id)
        return templates.upcoming(upcoming, turn.tz) if upcoming else templates.nothing_booked_yet()


agent = Agent()
