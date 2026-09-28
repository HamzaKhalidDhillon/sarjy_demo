"""The full agent pipeline for one conversation turn.

Guardrails run before anything else. Memory is recalled before the LLM call and extracted
after. The booking sub-flow is an explicit state machine, not free-form LLM reasoning, and a
"booked" reply is only ever emitted right after a real Cal.com success -- see templates.py and
guardrails.OutputGuardrail for how that's enforced structurally rather than just prompted.
"""
import hashlib
import json
import re
from datetime import datetime, timedelta, timezone

from sqlalchemy.orm import Session

from backend.agent import memory, templates
from backend.agent.guardrails import SYSTEM_PROMPT, InputGuardrail, OutputGuardrail
from backend.agent.state import BookingState, ConversationStateMachine
from backend.core.logging import logger, timed
from backend.llm.base import LLMProvider
from backend.llm.factory import get_llm_provider
from backend.models import BookingAttempt, Message
from backend.core.errors import ToolError
from backend.tools.calcom.client import CalComClient
from backend.tools.calcom.tools import BookMeetingTool, CheckAvailabilityTool, get_event_type_id

_BOOK_INTENT = re.compile(
    r"\b(book|booking|schedule|set up|arrange)\b.{0,30}\b(meeting|call|appointment|chat|demo|sales|agent|team|someone)\b"
    r"|\b(talk|speak) (to|with) (someone|sales|a person|your team|the team|an agent|a human)\b",
    re.IGNORECASE,
)
_CONFIRM_INTENT = re.compile(
    r"\b(yes|yeah|yep|confirm|book it|go ahead|sounds good|that works|please do)\b", re.IGNORECASE
)
_DECLINE_INTENT = re.compile(
    r"\b(no|different time|another time|not that|actually)\b", re.IGNORECASE
)
_ISO_DATETIME = re.compile(r"\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}")
# Cal.com requires an attendee contact method (email or phone) -- name+timezone alone is
# rejected with a 400, discovered by testing against the real API.
_EMAIL = re.compile(r"[^\s@]+@[^\s@]+\.[^\s@]+")


def _idempotency_key(conversation_id: int, event_type_id: int, start: str) -> str:
    raw = f"{conversation_id}:{event_type_id}:{start}"
    return hashlib.sha256(raw.encode()).hexdigest()


async def _extract_requested_datetime(llm: LLMProvider, message: str) -> str | None:
    """Best-effort: try an explicit ISO-ish pattern first (works even with no LLM configured),
    then fall back to asking the LLM to resolve relative phrases like "Thursday at 3pm"."""
    inline = _ISO_DATETIME.search(message)
    if inline:
        return inline.group(0).replace(" ", "T")

    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    prompt = [
        {
            "role": "system",
            "content": (
                f"Today is {today} (UTC). Extract the meeting start time the user is requesting "
                'as strict JSON: {"iso": "<ISO8601 datetime>"} or {"iso": null} if none is stated. '
                "Reply with ONLY that JSON, nothing else."
            ),
        },
        {"role": "user", "content": message},
    ]
    try:
        raw = await llm.complete(prompt)
        parsed = json.loads(raw.strip())
        iso = parsed.get("iso")
        return iso if iso and _ISO_DATETIME.search(iso) else None
    except Exception:
        # Malformed LLM output is treated as "couldn't find a time", never trusted as-is.
        return None


def _recent_history(db: Session, conversation_id: int, limit: int = 10) -> list[dict]:
    """The last few turns of this conversation, so the LLM has short-term context too. The router
    saves the current user message before calling the agent, so that newest row is skipped."""
    rows = (
        db.query(Message).filter(Message.conversation_id == conversation_id)
        .order_by(Message.id.desc()).limit(limit + 1).all()
    )
    return [{"role": r.role, "content": r.content} for r in reversed(rows[1:])]


class Agent:
    async def run_turn(
        self, db: Session, user_id: str, conversation_id: int, message: str,
        llm_provider_name: str | None = None,
    ) -> str:
        state_machine = ConversationStateMachine(db, conversation_id)

        if state_machine.maybe_cancel(message):
            return templates.cancelled()

        with timed("guardrail_input"):
            verdict = InputGuardrail.check(message)
        if not verdict.allowed:
            logger.info("input guardrail blocked turn: %s", verdict.reason)
            return templates.GUARDRAIL_REFUSAL

        llm = get_llm_provider(llm_provider_name)

        with timed("memory_recall"):
            memories = memory.recall(db, user_id)
        memory_context = memory.format_memories_for_prompt(memories)

        if state_machine.state in (BookingState.IDLE, BookingState.BOOKED) and _BOOK_INTENT.search(message):
            reply = await self._start_booking(db, llm, state_machine, message)
        elif state_machine.state == BookingState.COLLECTING_TIME:
            reply = await self._collect_time(db, llm, state_machine, message)
        elif state_machine.state == BookingState.AWAITING_CONFIRMATION:
            reply = await self._handle_confirmation(db, user_id, conversation_id, state_machine, message)
        elif state_machine.state == BookingState.AWAITING_CONTACT:
            reply = await self._collect_contact(db, user_id, conversation_id, state_machine, message)
        else:
            reply = await self._chat(llm, memory_context, message, _recent_history(db, conversation_id))

        booking_verified = state_machine.state == BookingState.BOOKED
        out_verdict = OutputGuardrail.check(reply, booking_verified=booking_verified)
        if not out_verdict.allowed:
            logger.warning("output guardrail rejected a reply (%s), replacing with safe fallback", out_verdict.reason)
            reply = templates.booking_pending_reconciliation()

        return reply

    async def _chat(
        self, llm: LLMProvider, memory_context: str, message: str, history: list[dict] | None = None
    ) -> str:
        system_content = SYSTEM_PROMPT
        if memory_context:
            system_content += " " + memory_context
        messages = [{"role": "system", "content": system_content}, *(history or []), {"role": "user", "content": message}]
        with timed("llm_chat"):
            return await llm.complete(messages)

    async def _start_booking(
        self, db: Session, llm: LLMProvider, state_machine: ConversationStateMachine, message: str
    ) -> str:
        state_machine.transition(BookingState.COLLECTING_TIME)

        # "Book me a call tomorrow at 3pm" -- check that time straight away
        requested = await _extract_requested_datetime(llm, message)
        if requested:
            return await self._check_time(state_machine, requested)

        today = datetime.now(timezone.utc)
        start = today.strftime("%Y-%m-%d")
        end = (today + timedelta(days=6)).strftime("%Y-%m-%d")
        with timed("tool_call:check_availability"):
            result = await CheckAvailabilityTool().run(start_date=start, end_date=end)
        if not result.ok:
            return "I'd love to help you book a meeting, but I can't reach the calendar right now. What time works for you and I'll try again?"
        return templates.slots_offer(result.data.get("slots", []))

    async def _collect_time(
        self, db: Session, llm: LLMProvider, state_machine: ConversationStateMachine, message: str
    ) -> str:
        requested = await _extract_requested_datetime(llm, message)
        if not requested:
            return "I didn't catch a specific day and time -- could you give me one, e.g. 'tomorrow at 3pm'?"
        return await self._check_time(state_machine, requested)

    async def _check_time(self, state_machine: ConversationStateMachine, requested: str) -> str:
        # One call for the requested day plus the week after: measured, a 7-day lookup takes the
        # same ~0.5s as a 1-day one, so this avoids a second round trip when the day is full.
        date_only = requested.split("T")[0]
        week_later = (datetime.fromisoformat(date_only) + timedelta(days=7)).strftime("%Y-%m-%d")
        with timed("tool_call:check_availability"):
            result = await CheckAvailabilityTool().run(start_date=date_only, end_date=week_later)
        if not result.ok:
            return "I couldn't check the calendar just now -- could you try again in a moment?"

        available = result.data.get("slots", [])
        match = next((s for s in available if s.startswith(requested[:16])), None)
        if match:
            state_machine.transition(BookingState.AWAITING_CONFIRMATION, pending_slot_start=match)
            return f"{templates.pretty_time(match)} is open. Should I go ahead and book it?"

        # That time is taken: suggest other times the same day, or the next open ones after it.
        same_day = [s for s in available if s.startswith(date_only)]
        return templates.slot_taken(requested, same_day or available)

    async def _handle_confirmation(
        self, db: Session, user_id: str, conversation_id: int,
        state_machine: ConversationStateMachine, message: str,
    ) -> str:
        if _DECLINE_INTENT.search(message) and not _CONFIRM_INTENT.search(message):
            state_machine.transition(BookingState.COLLECTING_TIME, pending_slot_start=None)
            return "No problem -- what time would work better?"

        if not _CONFIRM_INTENT.search(message):
            # Off-script: answer normally but keep the pending confirmation parked.
            reply = await self._chat(get_llm_provider(), "", message)
            return f"{reply}\n(By the way, I still have {templates.pretty_time(state_machine.row.pending_slot_start)} pending -- say 'yes' to confirm it, or 'never mind' to drop it.)"

        if not state_machine.row.pending_attendee_email:
            state_machine.transition(BookingState.AWAITING_CONTACT)
            return "What email should I send the confirmation to?"

        return await self._create_booking(db, user_id, conversation_id, state_machine)

    async def _collect_contact(
        self, db: Session, user_id: str, conversation_id: int,
        state_machine: ConversationStateMachine, message: str,
    ) -> str:
        match = _EMAIL.search(message)
        if not match:
            return "I need a valid email address to send the confirmation to -- what's the best one?"

        state_machine.transition(BookingState.AWAITING_CONFIRMATION, pending_attendee_email=match.group(0))
        return await self._create_booking(db, user_id, conversation_id, state_machine)

    async def _create_booking(
        self, db: Session, user_id: str, conversation_id: int, state_machine: ConversationStateMachine,
    ) -> str:
        start = state_machine.row.pending_slot_start
        try:
            event_type_id = await get_event_type_id(CalComClient())
        except ToolError as exc:
            return templates.booking_failed(str(exc))
        attendee_email = state_machine.row.pending_attendee_email
        idem_key = _idempotency_key(conversation_id, event_type_id, start)

        attempt = db.query(BookingAttempt).filter(BookingAttempt.idempotency_key == idem_key).first()
        if attempt and attempt.status == "confirmed":
            state_machine.transition(
                BookingState.BOOKED, pending_slot_start=None, pending_event_type_id=None,
                pending_attendee_email=None,
            )
            return templates.booked(start, attempt.calcom_booking_uid)

        if not attempt:
            attempt = BookingAttempt(
                user_id=user_id, conversation_id=conversation_id, idempotency_key=idem_key,
                event_type_id=event_type_id, requested_start=start, status="pending",
            )
            db.add(attempt)
            db.commit()
            db.refresh(attempt)

        with timed("tool_call:book_meeting"):
            result = await BookMeetingTool().run(
                start=start, attendee_name=user_id, attendee_timezone="UTC",
                attendee_email=attendee_email,
            )

        if result.ok:
            attempt.status = "confirmed"
            attempt.calcom_booking_uid = result.data.get("uid")
            attempt.calcom_booking_id = result.data.get("id")
            db.commit()
            state_machine.transition(
                BookingState.BOOKED, pending_slot_start=None, pending_event_type_id=None,
                pending_attendee_email=None,
            )
            return templates.booked(start, attempt.calcom_booking_uid)

        if result.ambiguous:
            # Left as "pending" on purpose -- a future turn/GET-bookings check can reconcile this
            # without ever guessing at booked/failed in the meantime (see GOALS.md future work).
            return templates.booking_pending_reconciliation()

        attempt.status = "failed"
        attempt.error_message = result.error
        db.commit()
        state_machine.transition(BookingState.COLLECTING_TIME, pending_slot_start=None, pending_attendee_email=None)
        return templates.booking_failed(result.error or "unknown error")


agent = Agent()
