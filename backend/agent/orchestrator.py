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
from typing import NamedTuple
from zoneinfo import ZoneInfo

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
from backend.tools.calcom.tools import (
    BookMeetingTool, CancelMeetingTool, CheckAvailabilityTool, RescheduleMeetingTool, get_event_type_id,
)

_BOOK_INTENT = re.compile(
    r"\b(book|booking|schedule|set up|arrange)\b.{0,30}\b(meeting|call|appointment|chat|demo|sales|agent|team|someone)\b"
    r"|\b(talk|speak) (to|with) (someone|sales|a person|your team|the team|an agent|a human)\b"
    r"|\bbook (me )?(another|one more|it again)\b",
    re.IGNORECASE,
)
_CONFIRM_INTENT = re.compile(
    r"\b(yes|yeah|yep|yup|sure|ok|okay|perfect|absolutely|definitely|confirm|book it|go ahead|"
    r"sounds good|that works|please do|do it)\b",
    re.IGNORECASE,
)
# In a longer message a casual "ok" isn't a yes ("ok, what about tomorrow at 10?"), so longer
# messages only count as confirming with one of these.
_STRONG_CONFIRM = re.compile(r"\b(confirm|book it|go ahead|please do|do it)\b", re.IGNORECASE)


def _is_confirmation(message: str) -> bool:
    if not _CONFIRM_INTENT.search(message) or _DECLINE_INTENT.search(message):
        return False
    return len(message.split()) <= 6 or bool(_STRONG_CONFIRM.search(message))
_DECLINE_INTENT = re.compile(
    r"\b(no|different time|another time|not that|actually)\b", re.IGNORECASE
)
_CANCEL_BOOKING = re.compile(
    r"\b(cancel|call off|scrap)\b.{0,40}\b(call|meeting|booking|appointment|demo)\b", re.IGNORECASE
)
_RESCHEDULE = re.compile(
    r"\breschedule\b|\b(move|change|push|shift|postpone)\b.{0,40}\b(call|meeting|booking|appointment|demo)\b",
    re.IGNORECASE,
)
_LIST_BOOKINGS = re.compile(
    r"\b(what|which|any|list|show)\b.{0,30}\b(calls|meetings|bookings|appointments)\b"
    r"|\bmy (upcoming )?(calls|meetings|bookings)\b|\bwhen is my (call|meeting)\b",
    re.IGNORECASE,
)
_KEEP_IT = re.compile(r"\b(no|keep|don'?t|never mind|nevermind|leave it)\b", re.IGNORECASE)

# "is my meeting booked?" / "did you book it?" -- answered from our own records, not the LLM
_STATUS_QUESTION = re.compile(
    r"\b(is|was|has) (my|the|our) (meeting|call|booking|appointment)\b.{0,20}\b(booked|scheduled|confirmed|set)\b"
    r"|\bdid you (already |actually )?(book|schedule|confirm)\b|\bam i (booked|scheduled|confirmed)\b",
    re.IGNORECASE,
)
_ISO_DATETIME = re.compile(r"\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}")
_ISO_DATE = re.compile(r"\d{4}-\d{2}-\d{2}")
# Cal.com requires an attendee contact method (email or phone) -- name+timezone alone is
# rejected with a 400, discovered by testing against the real API.
_EMAIL = re.compile(r"[\w.+-]+@[\w-]+(\.[\w-]+)+")
# Voice users say "hamza at gmail dot com", which Whisper often transcribes literally
_SPOKEN_EMAIL = re.compile(
    r"((?:[\w+-]+\s*(?:\.|\bdot\b)\s*)*[\w+-]+)\s+at\s+([\w-]+(?:\s*(?:\.|\bdot\b)\s*[\w-]+)+)", re.IGNORECASE
)


def _find_email(message: str) -> str | None:
    match = _EMAIL.search(message)
    if match:
        return match.group(0).rstrip(".")
    spoken = _SPOKEN_EMAIL.search(message)
    if spoken:
        local, domain = (
            re.sub(r"\s*(?:\.|\bdot\b)\s*", ".", part, flags=re.IGNORECASE) for part in spoken.groups()
        )
        candidate = f"{local}@{domain}".lower()
        if _EMAIL.fullmatch(candidate):
            return candidate
    return None


def _find_emails(message: str) -> list[str]:
    """Every email in the message, typed or spoken, in order and without duplicates."""
    found = [m.group(0).rstrip(".") for m in _EMAIL.finditer(message)]
    if not found and _find_email(message):
        found = [_find_email(message)]
    return list(dict.fromkeys(e.lower() for e in found))


def _invitees(state_machine: ConversationStateMachine) -> list[str]:
    """Emails collected for the pending booking: the first is the attendee, the rest are guests.
    Kept comma-separated in the existing pending_attendee_email column (no schema change)."""
    return [e for e in (state_machine.row.pending_attendee_email or "").split(",") if e]


def _add_invitees(state_machine: ConversationStateMachine, emails: list[str]) -> None:
    merged = list(dict.fromkeys(_invitees(state_machine) + emails))
    state_machine.transition(state_machine.state, pending_attendee_email=",".join(merged))


def _saved_email(memories) -> str | None:
    """The user's email from memory, if we have one (saved after a booking, or mentioned before)."""
    for m in sorted(memories, key=lambda m: m.key != "email"):
        if "email" in m.key and _EMAIL.fullmatch(m.value.strip()):
            return m.value.strip()
    return None


# "yes, the same one" / "use that" -> the saved email; "also" / "too" -> saved email plus others
_USE_SAVED = re.compile(r"\b(same|that one|that email|that address|saved|on file|use it|also|too|as well)\b", re.IGNORECASE)
_NOT_SAVED = re.compile(r"\b(instead|rather|different|another email|other email|not that)\b", re.IGNORECASE)


def _safe_tz(name: str | None) -> str:
    try:
        ZoneInfo(name or "UTC")
        return name or "UTC"
    except Exception:
        return "UTC"


def _idempotency_key(conversation_id: int, event_type_id: int, start: str) -> str:
    raw = f"{conversation_id}:{event_type_id}:{start}"
    return hashlib.sha256(raw.encode()).hexdigest()


_HHMM = re.compile(r"^(\d{1,2}):(\d{2})")
_PARTS = {"morning": (0, 12), "afternoon": (12, 17), "evening": (17, 24)}


class TimeAsk(NamedTuple):
    exact: str | None = None  # 'YYYY-MM-DDTHH:MM' in the user's timezone
    day: str | None = None    # 'YYYY-MM-DD' when they only named a day
    part: str | None = None   # 'morning' / 'afternoon' / 'evening' if they said one


async def _read_requested_time(llm: LLMProvider, message: str, history: list[dict], tz: str) -> TimeAsk:
    """Work out what time the user is asking for: an exact time, just a day, and/or a part of the
    day ("afternoon"). All empty if they didn't ask for a time (usually a question). The LLM sees the recent conversation, so "the first one" or "make it
    11 instead" resolve against what was just discussed. It only reports what the user said
    (date, time, any timezone they named); timezone conversion is done here in code, because the
    model got that arithmetic wrong in testing. Its output is only used to *look up* real slots
    on Cal.com, never shown or booked as-is."""
    inline = _ISO_DATETIME.search(message)
    if inline:
        return TimeAsk(exact=inline.group(0).replace(" ", "T"))

    now = datetime.now(ZoneInfo(tz))
    recent = "\n".join(f"{m['role']}: {m['content']}" for m in history[-4:])
    # A small calendar, so "Sunday" or "next Monday" don't depend on the model's date arithmetic
    calendar = ", ".join(
        f"{(now + timedelta(days=i)).strftime('%a')} {(now + timedelta(days=i)).date()}" for i in range(14)
    )
    prompt = [
        {
            "role": "system",
            "content": (
                f"Now it is {now.strftime('%A %Y-%m-%d %H:%M')} for the user. Next 14 days: {calendar}. "
                "Work out which meeting "
                "time the user is asking for in their LAST message. The assistant may have just "
                "offered or proposed times; 'the first one', 'the 5pm one' or 'make it 11 instead' "
                "refer to those, and a bare time means the day being discussed. Reply with ONLY JSON: "
                '{"date": "YYYY-MM-DD" or null, "time": "HH:MM" (24-hour) or null, '
                '"timezone": the timezone the user explicitly named, e.g. "UTC", or null, '
                '"part_of_day": "morning", "afternoon" or "evening" if they said one, or null}. '
                "Give the time exactly as the user means it, without converting timezones. "
                "For a vague range like 'next week', give its first day as date. "
                "Use null for everything when they are not asking for a time.\n\n"
                f"Recent conversation:\n{recent}"
            ),
        },
        {"role": "user", "content": message},
    ]
    try:
        with timed("llm_read_time"):
            raw = await llm.complete(prompt)
        parsed = json.loads(raw.strip().removeprefix("```json").removesuffix("```"))
    except Exception:
        # Malformed LLM output is treated as "no time found", never trusted as-is.
        return TimeAsk()

    part = parsed.get("part_of_day") if parsed.get("part_of_day") in _PARTS else None
    day = parsed.get("date")
    if not (isinstance(day, str) and _ISO_DATE.fullmatch(day)):
        return TimeAsk(part=part)
    clock = _HHMM.match(str(parsed.get("time") or ""))
    if not clock:
        return TimeAsk(day=day, part=part)

    # The user may name a timezone ("10am UTC"); convert to their own timezone here in code.
    # Anything that isn't a real timezone name (e.g. "PKT", echoed from Sarjy's own message)
    # is taken to mean the user's own timezone.
    said_tz = str(parsed.get("timezone") or "").strip()
    said_tz = {"GMT": "UTC", "Z": "UTC"}.get(said_tz.upper(), said_tz)
    source_tz = said_tz if said_tz and _safe_tz(said_tz) == said_tz else tz
    try:
        when = datetime.fromisoformat(f"{day}T{int(clock.group(1)):02d}:{clock.group(2)}")
    except ValueError:
        return TimeAsk(day=day, part=part)
    local = when.replace(tzinfo=ZoneInfo(source_tz)).astimezone(ZoneInfo(tz))
    return TimeAsk(exact=local.strftime("%Y-%m-%dT%H:%M"))


def _upcoming_bookings(db: Session, user_id: str) -> list[BookingAttempt]:
    """The user's confirmed future calls, across all their chats, from our own records."""
    now = datetime.now(timezone.utc)
    rows = db.query(BookingAttempt).filter(
        BookingAttempt.user_id == user_id, BookingAttempt.status == "confirmed"
    ).all()
    future = [r for r in rows if templates.to_local(r.requested_start) > now]
    return sorted(future, key=lambda r: templates.to_local(r.requested_start))


def _match_booking(bookings: list[BookingAttempt], ask: TimeAsk, tz: str) -> BookingAttempt | None:
    """Which of the user's bookings they mean ("my Friday call", "the 11am one")."""
    if ask.exact:
        wanted = templates.to_local(ask.exact, tz)
        hits = [b for b in bookings if templates.to_local(b.requested_start, tz) == wanted]
    elif ask.day:
        hits = [b for b in bookings if templates.to_local(b.requested_start, tz).date().isoformat() == ask.day]
    else:
        hits = []
    return hits[0] if len(hits) == 1 else None


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
        llm_provider_name: str | None = None, timezone_name: str | None = None,
    ) -> str:
        state_machine = ConversationStateMachine(db, conversation_id)
        tz = _safe_tz(timezone_name)

        changing = state_machine.state in (
            BookingState.CHOOSE_TO_CANCEL, BookingState.CHOOSE_TO_RESCHEDULE,
            BookingState.RESCHEDULE_TIME, BookingState.CONFIRM_RESCHEDULE,
        )
        if state_machine.maybe_cancel(message):
            # "never mind" while moving a call keeps the call; it only drops the change
            return "Okay, I'll leave your call as it is." if changing else templates.cancelled()

        with timed("guardrail_input"):
            verdict = InputGuardrail.check(message)
        if not verdict.allowed:
            logger.info("input guardrail blocked turn: %s", verdict.reason)
            return templates.GUARDRAIL_REFUSAL

        llm = get_llm_provider(llm_provider_name)

        with timed("memory_recall"):
            memories = memory.recall(db, user_id)
        memory_context = memory.format_memories_for_prompt(memories)
        history = _recent_history(db, conversation_id)

        if _STATUS_QUESTION.search(message) or _LIST_BOOKINGS.search(message):
            return self._what_is_actually_booked(db, user_id, state_machine, tz)

        # Let the LLM know about the user's real upcoming calls (for questions like "should I
        # prepare anything for Friday?"); any claim it makes about them still goes through the
        # output guardrail below.
        upcoming = _upcoming_bookings(db, user_id)
        if upcoming:
            times = ", ".join(templates.pretty_time(b.requested_start, tz) for b in upcoming)
            memory_context = f"{memory_context} The user's upcoming calls, from the booking system: {times}.".strip()

        state_before = state_machine.state
        free = state_before in (BookingState.IDLE, BookingState.BOOKED, BookingState.CHANGED)
        cancelling = free and _CANCEL_BOOKING.search(message)
        rescheduling = free and not cancelling and _RESCHEDULE.search(message)
        starting = free and not (cancelling or rescheduling) and _BOOK_INTENT.search(message)

        saved_email = _saved_email(memories)

        # Emails can come at any point in the booking ("book a call at 3, I'm hamza@x.com"),
        # and more than one: the first is the attendee, the rest are invited as guests.
        emails = _find_emails(message)
        if emails and (starting or state_before in (BookingState.COLLECTING_TIME, BookingState.AWAITING_CONFIRMATION)):
            if starting:
                state_machine.transition(state_before, pending_attendee_email=None)
            _add_invitees(state_machine, emails)

        if state_before == BookingState.BOOKED and emails and not starting:
            reply = (
                "Your booking is already confirmed, and I can't change who it goes to. If you'd like "
                f"another call with the invite sent to {templates.recipients(emails)}, just say 'book another call'."
            )
        elif cancelling or rescheduling:
            action = "cancel" if cancelling else "reschedule"
            reply = await self._start_change(db, user_id, llm, state_machine, message, history, tz, action)
        elif state_before in (BookingState.CHOOSE_TO_CANCEL, BookingState.CHOOSE_TO_RESCHEDULE):
            action = "cancel" if state_before == BookingState.CHOOSE_TO_CANCEL else "reschedule"
            reply = await self._choose_booking(db, user_id, llm, state_machine, message, history, memory_context, tz, action)
        elif state_before == BookingState.CONFIRM_CANCEL:
            reply = await self._confirm_cancel(db, llm, state_machine, message, history, memory_context, tz)
        elif state_before == BookingState.RESCHEDULE_TIME:
            reply = await self._reschedule_time(db, llm, state_machine, message, history, memory_context, tz)
        elif state_before == BookingState.CONFIRM_RESCHEDULE:
            reply = await self._confirm_reschedule(db, llm, state_machine, message, history, memory_context, tz)
        elif starting:
            reply = await self._start_booking(llm, state_machine, message, history, tz)
        elif state_before == BookingState.COLLECTING_TIME:
            reply = await self._collect_time(llm, state_machine, message, history, memory_context, tz)
        elif state_before == BookingState.AWAITING_CONFIRMATION:
            reply = await self._handle_confirmation(db, user_id, conversation_id, llm, state_machine, message, history, memory_context, tz, saved_email)
        elif state_before == BookingState.AWAITING_CONTACT:
            reply = await self._collect_contact(db, user_id, conversation_id, llm, state_machine, message, history, memory_context, tz, saved_email)
        else:
            reply = await self._chat(llm, memory_context, message, history)

        # A booking/cancel/reschedule only counts as verified in the turn it actually happened.
        # Otherwise the LLM could repeat an earlier confirmation from the history ("I've booked
        # another call...").
        done = (BookingState.BOOKED, BookingState.CHANGED)
        booking_verified = state_before not in done and state_machine.state in done
        out_verdict = OutputGuardrail.check(reply, booking_verified=booking_verified)
        if not out_verdict.allowed:
            logger.warning("output guardrail rejected a reply (%s), replacing with the facts", out_verdict.reason)
            reply = self._what_is_actually_booked(db, user_id, state_machine, tz)

        return reply

    def _what_is_actually_booked(
        self, db: Session, user_id: str, state_machine: ConversationStateMachine, tz: str
    ) -> str:
        """The truth about the user's bookings, straight from our own records. Used to answer
        "is my meeting booked?" / "what calls do I have?" and to replace any booking claim the LLM
        wasn't allowed to make."""
        state, pending = state_machine.state, state_machine.row.pending_slot_start
        if pending and state == BookingState.AWAITING_CONTACT:
            return templates.holding(pending, "Just tell me your email address and I'll book it.", tz)
        if pending and state == BookingState.AWAITING_CONFIRMATION:
            return templates.holding(pending, "Say 'yes' and I'll book it.", tz)

        upcoming = _upcoming_bookings(db, user_id)
        if upcoming:
            return templates.upcoming(upcoming, tz)
        return templates.nothing_booked_yet()

    async def _chat(
        self, llm: LLMProvider, memory_context: str, message: str, history: list[dict] | None = None
    ) -> str:
        system_content = SYSTEM_PROMPT
        if memory_context:
            system_content += " " + memory_context
        messages = [{"role": "system", "content": system_content}, *(history or []), {"role": "user", "content": message}]
        with timed("llm_chat"):
            return await llm.complete(messages)

    async def _answer_and_nudge(
        self, llm: LLMProvider, memory_context: str, message: str, history: list[dict], nudge: str
    ) -> str:
        """Off-script mid-booking ("how long is the call?"): answer it, keep the booking parked."""
        note = "A booking is already in progress, so don't tell the user to say 'book a call'."
        reply = await self._chat(llm, f"{memory_context} {note}".strip(), message, history)
        return f"{reply}\n\n{nudge}"

    async def _start_booking(
        self, llm: LLMProvider, state_machine: ConversationStateMachine, message: str,
        history: list[dict], tz: str,
    ) -> str:
        state_machine.transition(BookingState.COLLECTING_TIME, pending_slot_start=None)

        # "Book me a call tomorrow at 3pm" / "...on Friday" -- use it straight away
        ask = await _read_requested_time(llm, message, history, tz)
        if ask.exact:
            return await self._check_time(state_machine, ask.exact, tz)
        return await self._offer_times(ask.day, tz, ask.part)

    async def _offer_times(self, day: str | None, tz: str, part: str | None = None) -> str:
        """Open times from `day` (or today) onward, only in the `part` of the day if given."""
        today = datetime.now(ZoneInfo(tz)).date()
        asked = datetime.fromisoformat(day).date() if day else today
        start = max(asked, today)
        with timed("tool_call:check_availability"):
            result = await CheckAvailabilityTool().run(
                start_date=start.isoformat(), end_date=(start + timedelta(days=7)).isoformat(), timezone=tz,
            )
        if not result.ok:
            return "I'd love to help you book a meeting, but I can't reach the calendar right now. Could you try again in a moment?"
        slots = result.data.get("slots", [])
        note = "That day has already passed. " if asked < today else ""
        if part:
            low, high = _PARTS[part]
            in_part = [s for s in slots if low <= templates.to_local(s, tz).hour < high]
            if not in_part:
                note += f"I don't have any {part} times then. "
            slots = in_part or slots
        return note + templates.slots_offer(slots, tz)

    async def _collect_time(
        self, llm: LLMProvider, state_machine: ConversationStateMachine, message: str,
        history: list[dict], memory_context: str, tz: str,
    ) -> str:
        # Short replies that aren't a time ("yes", or just an email address) are handled before
        # asking the LLM, which would otherwise guess a time from the conversation.
        without_email = _EMAIL.sub("", message) if _find_email(message) else message
        short = len(without_email.split()) <= 4 and not re.search(r"\d", without_email)
        if _find_email(message) and short:
            return f"Thanks, I'll send the invite to {templates.recipients(_invitees(state_machine))}. Which time works for you?"
        if _CONFIRM_INTENT.search(message) and short:
            return "Which of those times would you like? You can say something like 'the 9:30 one' or 'Thursday at 10'."

        ask = await _read_requested_time(llm, message, history, tz)
        if ask.exact:
            return await self._check_time(state_machine, ask.exact, tz)
        if ask.day or ask.part:
            return await self._offer_times(ask.day, tz, ask.part)
        return await self._answer_and_nudge(
            llm, memory_context, message, history,
            "Whenever you're ready, just tell me a day and time for the call, or say 'never mind' to skip it.",
        )

    async def _check_time(
        self, state_machine: ConversationStateMachine, requested: str, tz: str, moving_from: str | None = None,
    ) -> str:
        """`requested` is 'YYYY-MM-DDTHH:MM' in the user's timezone. `moving_from` is the current
        start of a booking being rescheduled (changes what we ask for confirmation)."""
        wanted = templates.to_local(requested, tz)
        now = datetime.now(ZoneInfo(tz))
        if wanted < now:
            with timed("tool_call:check_availability"):
                result = await CheckAvailabilityTool().run(
                    start_date=now.date().isoformat(), end_date=(now.date() + timedelta(days=7)).isoformat(), timezone=tz,
                )
            return templates.time_passed(result.data.get("slots", []) if result.ok else [], tz)

        # One call for the requested day plus the week after: measured, a 7-day lookup takes the
        # same ~0.5s as a 1-day one, so this avoids a second round trip when the day is full.
        day = wanted.date()
        with timed("tool_call:check_availability"):
            result = await CheckAvailabilityTool().run(
                start_date=day.isoformat(), end_date=(day + timedelta(days=7)).isoformat(), timezone=tz,
            )
        if not result.ok:
            return "I couldn't check the calendar just now -- could you try again in a moment?"

        available = result.data.get("slots", [])
        match = next((s for s in available if templates.to_local(s, tz) == wanted), None)
        if match and moving_from:
            state_machine.transition(BookingState.CONFIRM_RESCHEDULE, pending_slot_start=match)
            return templates.confirm_reschedule(moving_from, match, tz)
        if match:
            state_machine.transition(BookingState.AWAITING_CONFIRMATION, pending_slot_start=match)
            return f"{templates.pretty_time(match, tz)} is open. Should I go ahead and book it?"

        # That time is taken: suggest other times the same day, or the next open ones after it.
        same_day = [s for s in available if templates.to_local(s, tz).date() == day]
        return templates.slot_taken(requested, same_day or available, tz)

    async def _handle_confirmation(
        self, db: Session, user_id: str, conversation_id: int, llm: LLMProvider,
        state_machine: ConversationStateMachine, message: str, history: list[dict],
        memory_context: str, tz: str, saved_email: str | None = None,
    ) -> str:
        pending = templates.pretty_time(state_machine.row.pending_slot_start, tz)
        confirmed = _is_confirmation(message)

        if not confirmed and _find_email(message) and not _ISO_DATETIME.search(message):
            return f"Thanks, I'll send the invite to {templates.recipients(_invitees(state_machine))}. Shall I book {pending}?"

        if not confirmed:
            # "actually make it 11am instead" -> check the new time right away
            ask = await _read_requested_time(llm, message, history, tz)
            if ask.exact:
                return await self._check_time(state_machine, ask.exact, tz)
            if ask.day or ask.part:
                state_machine.transition(BookingState.COLLECTING_TIME, pending_slot_start=None)
                return await self._offer_times(ask.day, tz, ask.part)
            if _DECLINE_INTENT.search(message):
                state_machine.transition(BookingState.COLLECTING_TIME, pending_slot_start=None)
                return "No problem -- what time would work better?"
            return await self._answer_and_nudge(
                llm, memory_context, message, history,
                f"I still have {pending} ready for you. Say 'yes' to book it, or 'never mind' to drop it.",
            )

        if not _invitees(state_machine):
            state_machine.transition(BookingState.AWAITING_CONTACT)
            if saved_email:
                return templates.ask_which_email(saved_email)
            return templates.ask_for_email()

        return await self._create_booking(db, user_id, conversation_id, state_machine, tz)

    async def _collect_contact(
        self, db: Session, user_id: str, conversation_id: int, llm: LLMProvider,
        state_machine: ConversationStateMachine, message: str, history: list[dict],
        memory_context: str, tz: str, saved_email: str | None = None,
    ) -> str:
        emails = _find_emails(message)
        # "yes" / "the same one" / "and also sam@x.com" -> include the email we have on file
        if saved_email and not _NOT_SAVED.search(message) and (
            (_CONFIRM_INTENT.search(message) and not _DECLINE_INTENT.search(message)) or _USE_SAVED.search(message)
        ):
            emails = [saved_email] + emails
        if emails:
            _add_invitees(state_machine, emails)
            state_machine.transition(BookingState.AWAITING_CONFIRMATION)
            return await self._create_booking(db, user_id, conversation_id, state_machine, tz)

        if saved_email and _DECLINE_INTENT.search(message):
            return "No problem -- which email should I send the invite to instead?"
        if "@" in message or re.search(r"\b(email|mail|gmail|outlook)\b", message, re.IGNORECASE):
            return "That doesn't look like a complete email address -- could you say or type it again?"
        pending = templates.pretty_time(state_machine.row.pending_slot_start, tz)
        return await self._answer_and_nudge(
            llm, memory_context, message, history,
            f"To finish booking {pending}, I just need an email address for the invite.",
        )

    # ---------- changing an existing booking ----------

    def _target(self, db: Session, state_machine: ConversationStateMachine) -> BookingAttempt | None:
        target_id = state_machine.row.target_booking_id
        return db.get(BookingAttempt, target_id) if target_id else None

    async def _start_change(
        self, db: Session, user_id: str, llm: LLMProvider, state_machine: ConversationStateMachine,
        message: str, history: list[dict], tz: str, action: str,
    ) -> str:
        """"cancel my Friday call" / "move my call to Monday": find which booking they mean."""
        bookings = _upcoming_bookings(db, user_id)
        if not bookings:
            state_machine.transition(BookingState.IDLE)
            return templates.no_upcoming()

        ask = await _read_requested_time(llm, message, history, tz)
        target = bookings[0] if len(bookings) == 1 else _match_booking(bookings, ask, tz)
        if not target:
            choose = BookingState.CHOOSE_TO_CANCEL if action == "cancel" else BookingState.CHOOSE_TO_RESCHEDULE
            state_machine.transition(choose, target_booking_id=None, pending_slot_start=None)
            return templates.which_booking(bookings, action, tz)

        # With a single booking, "move my call to Monday at 10" already names the new time
        new_time = ask if (action == "reschedule" and len(bookings) == 1 and _match_booking(bookings, ask, tz) is None) else TimeAsk()
        return await self._begin_change(state_machine, target, action, tz, new_time)

    async def _begin_change(
        self, state_machine: ConversationStateMachine, target: BookingAttempt, action: str, tz: str,
        new_time: TimeAsk = TimeAsk(),
    ) -> str:
        if action == "cancel":
            state_machine.transition(BookingState.CONFIRM_CANCEL, target_booking_id=target.id)
            return templates.confirm_cancel(target.requested_start, tz)

        state_machine.transition(BookingState.RESCHEDULE_TIME, target_booking_id=target.id, pending_slot_start=None)
        if new_time.exact:
            return await self._check_time(state_machine, new_time.exact, tz, moving_from=target.requested_start)
        if new_time.day or new_time.part:
            return await self._offer_times(new_time.day, tz, new_time.part)
        return templates.ask_new_time(target.requested_start, tz)

    async def _choose_booking(
        self, db: Session, user_id: str, llm: LLMProvider, state_machine: ConversationStateMachine,
        message: str, history: list[dict], memory_context: str, tz: str, action: str,
    ) -> str:
        bookings = _upcoming_bookings(db, user_id)
        if not bookings:
            state_machine.transition(BookingState.IDLE)
            return templates.no_upcoming()
        target = _match_booking(bookings, await _read_requested_time(llm, message, history, tz), tz)
        if target:
            return await self._begin_change(state_machine, target, action, tz)
        if _KEEP_IT.search(message):
            state_machine.transition(BookingState.IDLE)
            return "Okay, I'll leave your calls as they are."
        return templates.which_booking(bookings, action, tz)

    async def _confirm_cancel(
        self, db: Session, llm: LLMProvider, state_machine: ConversationStateMachine, message: str,
        history: list[dict], memory_context: str, tz: str,
    ) -> str:
        target = self._target(db, state_machine)
        if not target or target.status != "confirmed":
            state_machine.transition(BookingState.IDLE, target_booking_id=None)
            return "I can't find that booking anymore, so there's nothing to cancel."

        if _KEEP_IT.search(message) and not re.search(r"\bcancel (it|that)\b", message, re.IGNORECASE):
            state_machine.transition(BookingState.IDLE, target_booking_id=None)
            return f"Okay, I'll keep your call on {templates.pretty_time(target.requested_start, tz)}."
        if not (_is_confirmation(message) or re.search(r"\bcancel\b", message, re.IGNORECASE)):
            return await self._answer_and_nudge(
                llm, memory_context, message, history,
                f"Should I cancel your call on {templates.pretty_time(target.requested_start, tz)}? Say yes or no.",
            )

        with timed("tool_call:cancel_meeting"):
            result = await CancelMeetingTool().run(uid=target.calcom_booking_uid, reason="Cancelled via Sarjy")
        if not result.ok:
            state_machine.transition(BookingState.IDLE, target_booking_id=None)
            return f"I couldn't cancel it just now ({result.error}), so it's still booked. Want me to try again?"
        target.status = "cancelled"
        db.commit()
        state_machine.transition(BookingState.CHANGED, target_booking_id=None)
        return templates.cancelled_booking(target.requested_start, tz)

    async def _reschedule_time(
        self, db: Session, llm: LLMProvider, state_machine: ConversationStateMachine, message: str,
        history: list[dict], memory_context: str, tz: str,
    ) -> str:
        target = self._target(db, state_machine)
        if not target:
            state_machine.transition(BookingState.IDLE)
            return "I can't find that booking anymore."
        ask = await _read_requested_time(llm, message, history, tz)
        if ask.exact:
            return await self._check_time(state_machine, ask.exact, tz, moving_from=target.requested_start)
        if ask.day or ask.part:
            return await self._offer_times(ask.day, tz, ask.part)
        return await self._answer_and_nudge(
            llm, memory_context, message, history,
            f"What day and time would you like instead of {templates.pretty_time(target.requested_start, tz)}? Or say 'never mind' to keep it.",
        )

    async def _confirm_reschedule(
        self, db: Session, llm: LLMProvider, state_machine: ConversationStateMachine, message: str,
        history: list[dict], memory_context: str, tz: str,
    ) -> str:
        target, new_start = self._target(db, state_machine), state_machine.row.pending_slot_start
        if not target or not new_start:
            state_machine.transition(BookingState.IDLE)
            return "I lost track of that change -- could you tell me again which call to move?"

        if not _is_confirmation(message):
            state_machine.transition(BookingState.RESCHEDULE_TIME, pending_slot_start=None)
            return await self._reschedule_time(db, llm, state_machine, message, history, memory_context, tz)

        with timed("tool_call:reschedule_meeting"):
            result = await RescheduleMeetingTool().run(uid=target.calcom_booking_uid, start=new_start)
        if not result.ok:
            state_machine.transition(BookingState.IDLE, target_booking_id=None, pending_slot_start=None)
            return f"I couldn't move it just now ({result.error}), so it's still on {templates.pretty_time(target.requested_start, tz)}."
        target.requested_start = new_start
        target.calcom_booking_uid = result.data.get("uid")
        target.calcom_booking_id = result.data.get("id")
        target.idempotency_key = _idempotency_key(target.conversation_id, target.event_type_id, new_start)
        db.commit()
        state_machine.transition(BookingState.CHANGED, target_booking_id=None, pending_slot_start=None)
        return templates.rescheduled(new_start, target.calcom_booking_uid, tz)

    async def _create_booking(
        self, db: Session, user_id: str, conversation_id: int, state_machine: ConversationStateMachine,
        tz: str = "UTC",
    ) -> str:
        start = state_machine.row.pending_slot_start
        try:
            event_type_id = await get_event_type_id(CalComClient())
        except ToolError as exc:
            return templates.booking_failed(str(exc))
        invitees = _invitees(state_machine)
        attendee_email, guests = invitees[0], invitees[1:]
        idem_key = _idempotency_key(conversation_id, event_type_id, start)

        attempt = db.query(BookingAttempt).filter(BookingAttempt.idempotency_key == idem_key).first()
        if attempt and attempt.status == "confirmed":
            state_machine.transition(
                BookingState.BOOKED, pending_slot_start=None, pending_event_type_id=None,
                pending_attendee_email=None,
            )
            return templates.booked(start, attempt.calcom_booking_uid, invitees, tz)

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
                start=start, attendee_name=user_id, attendee_timezone=tz,
                attendee_email=attendee_email, guests=guests,
            )

        if result.ok:
            attempt.status = "confirmed"
            attempt.calcom_booking_uid = result.data.get("uid")
            attempt.calcom_booking_id = result.data.get("id")
            db.commit()
            memory._upsert(db, user_id, "email", attendee_email)  # offered next time
            state_machine.transition(
                BookingState.BOOKED, pending_slot_start=None, pending_event_type_id=None,
                pending_attendee_email=None,
            )
            return templates.booked(start, attempt.calcom_booking_uid, invitees, tz)

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
