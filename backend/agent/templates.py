"""The only place booking-outcome text is composed. Every string here is built from real data
returned by a tool call -- never freely generated -- so a confirmation can't be hallucinated.
"""
from datetime import datetime
from zoneinfo import ZoneInfo

GUARDRAIL_REFUSAL = (
    "I can't help with that request. Let's get back to scheduling your meeting, or ask me "
    "something else I can help with."
)

NO_SLOTS_FOUND = (
    "I couldn't find any open slots in that range. Want me to check a different date?"
)


def to_local(iso: str, tz: str = "UTC") -> datetime:
    """Parse an ISO time from Cal.com (or a naive 'YYYY-MM-DDTHH:MM' meant in the user's own
    timezone) and convert it to the user's timezone."""
    dt = datetime.fromisoformat(iso.replace("Z", "+00:00"))
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=ZoneInfo(tz))
    return dt.astimezone(ZoneInfo(tz))


def _clock(dt: datetime) -> str:
    return dt.strftime("%I:%M %p").lstrip("0")


def pretty_time(iso: str, tz: str = "UTC") -> str:
    """'2026-09-29T10:00:00.000Z' -> 'Tuesday Sep 29 at 3:00 PM PKT', so it reads well out loud."""
    try:
        dt = to_local(iso, tz)
    except ValueError:
        return iso
    return f"{dt.strftime('%A %b')} {dt.day} at {_clock(dt)} {dt.tzname()}"


def _grouped(slots: list[str], tz: str, days: int = 3, per_day: int = 3) -> str:
    """'Tuesday Sep 29: 9:00 AM, 9:30 AM or 10:00 AM; Wednesday Sep 30: ...' -- much easier to
    follow when spoken than a flat list of full dates."""
    by_day: dict[str, list[str]] = {}
    for slot in slots:
        dt = to_local(slot, tz)
        day = f"{dt.strftime('%A %b')} {dt.day}"
        if day not in by_day and len(by_day) == days:
            break
        by_day.setdefault(day, [])
        if len(by_day[day]) < per_day:
            by_day[day].append(_clock(dt))
    parts = []
    for day, times in by_day.items():
        joined = times[0] if len(times) == 1 else ", ".join(times[:-1]) + " or " + times[-1]
        parts.append(f"{day}: {joined}")
    zone = to_local(slots[0], tz).tzname()
    return "; ".join(parts) + f" ({zone})"


def slots_offer(slots: list[str], tz: str = "UTC") -> str:
    if not slots:
        return NO_SLOTS_FOUND
    return f"Here are some open times. {_grouped(slots, tz)}. Which one works for you?"


def slot_taken(requested: str, alternatives: list[str], tz: str = "UTC") -> str:
    msg = f"Sorry, {pretty_time(requested, tz)} isn't available."
    if not alternatives:
        return msg + " I couldn't find any open times in the week after either. Want me to check another date?"
    return msg + f" I could do {_grouped(alternatives, tz, days=2)} instead. Would any of those work?"


def time_passed(alternatives: list[str], tz: str = "UTC") -> str:
    msg = "That time has already passed."
    if not alternatives:
        return msg + " What day and time would work for you?"
    return msg + f" The next open times are {_grouped(alternatives, tz, days=2)}. Would any of those work?"


def recipients(emails: list[str]) -> str:
    """['a', 'b', 'c'] -> 'a, b and c'"""
    return emails[0] if len(emails) == 1 else ", ".join(emails[:-1]) + " and " + emails[-1]


def booked(start: str, uid: str, emails: list[str] | None = None, tz: str = "UTC") -> str:
    to = f" The invite is going to {recipients(emails)}." if emails else ""
    return f"You're booked for {pretty_time(start, tz)}.{to} Confirmation reference: {uid}."


def ask_which_email(saved: str) -> str:
    return (
        f"Should I send the invite to {saved}, the email I have on file, or a different one? "
        "You can also add someone else, like a colleague."
    )


def ask_for_email() -> str:
    return (
        "What email should I send the invite to? You can give more than one if someone else "
        "should join, like a colleague."
    )


def only_real_booking(start: str, uid: str, tz: str = "UTC") -> str:
    """Used when the LLM tried to claim a booking that didn't happen this turn: state the truth."""
    return (
        f"Yes, your call is booked for {pretty_time(start, tz)} (reference {uid}), and the "
        "confirmation email has the video link. That's the only booking in this chat; say "
        "'book another call' if you'd like one more."
    )


def holding(start: str, next_step: str, tz: str = "UTC") -> str:
    return f"Nothing is booked yet: I'm holding {pretty_time(start, tz)} for you. {next_step}"


def nothing_booked_yet() -> str:
    return "I haven't booked anything yet. Tell me a day and time and I'll check the calendar."


def booking_failed(reason: str) -> str:
    return f"I wasn't able to book that slot ({reason}). Want to try a different time?"


def booking_pending_reconciliation() -> str:
    return "I'm not certain that went through -- let me double check before I confirm anything."


def cancelled() -> str:
    return "No problem, I've dropped the booking request."
