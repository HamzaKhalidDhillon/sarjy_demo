"""The only place booking-outcome text is composed. Every string here is built from real data
returned by a tool call -- never freely generated -- so a confirmation can't be hallucinated.
"""

GUARDRAIL_REFUSAL = (
    "I can't help with that request. Let's get back to scheduling your meeting, or ask me "
    "something else I can help with."
)

NO_SLOTS_FOUND = (
    "I couldn't find any open slots in that range. Want me to check a different date?"
)


def slots_offer(slots: list[str]) -> str:
    if not slots:
        return NO_SLOTS_FOUND
    shown = ", ".join(slots[:5])
    return f"Here are some open times: {shown}. Which one works for you?"


def booked(start: str, uid: str) -> str:
    return f"You're booked for {start}. Confirmation reference: {uid}."


def booking_failed(reason: str) -> str:
    return f"I wasn't able to book that slot ({reason}). Want to try a different time?"


def booking_pending_reconciliation() -> str:
    return "I'm not certain that went through -- let me double check before I confirm anything."


def cancelled() -> str:
    return "No problem, I've dropped the booking request."
