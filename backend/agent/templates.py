"""The only place booking-outcome text is composed. Every string here is built from real data
returned by a tool call -- never freely generated -- so a confirmation can't be hallucinated.
"""
from datetime import datetime

GUARDRAIL_REFUSAL = (
    "I can't help with that request. Let's get back to scheduling your meeting, or ask me "
    "something else I can help with."
)

NO_SLOTS_FOUND = (
    "I couldn't find any open slots in that range. Want me to check a different date?"
)


def pretty_time(iso: str) -> str:
    """'2026-09-29T15:00:00.000Z' -> 'Tuesday Sep 29 at 3:00 PM UTC', so it reads well out loud."""
    try:
        dt = datetime.fromisoformat(iso.replace("Z", "+00:00"))
    except ValueError:
        return iso
    return dt.strftime("%A %b %d at %I:%M %p").replace(" 0", " ") + " UTC"


def slots_offer(slots: list[str]) -> str:
    if not slots:
        return NO_SLOTS_FOUND
    shown = ", ".join(pretty_time(s) for s in slots[:5])
    return f"Here are some open times: {shown}. Which one works for you?"


def slot_taken(requested: str, alternatives: list[str]) -> str:
    msg = f"Sorry, {pretty_time(requested)} isn't available."
    if not alternatives:
        return msg + " I couldn't find any open times in the week after either. Want me to check another date?"
    shown = ", ".join(pretty_time(s) for s in alternatives[:3])
    return msg + f" I could do {shown} instead. Would any of those work?"


def booked(start: str, uid: str) -> str:
    return f"You're booked for {pretty_time(start)}. Confirmation reference: {uid}."


def booking_failed(reason: str) -> str:
    return f"I wasn't able to book that slot ({reason}). Want to try a different time?"


def booking_pending_reconciliation() -> str:
    return "I'm not certain that went through -- let me double check before I confirm anything."


def cancelled() -> str:
    return "No problem, I've dropped the booking request."
