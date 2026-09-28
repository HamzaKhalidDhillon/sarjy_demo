"""Turning what the user said into structured data: intents, yes/no, email addresses and the
time they're asking for. Everything here is deterministic except read_requested_time(), whose
LLM output is validated and only ever used to look up real Cal.com slots."""
import json
import re
from datetime import datetime, timedelta
from typing import NamedTuple
from zoneinfo import ZoneInfo

from backend.core.logging import timed
from backend.llm.base import LLMProvider

# ---------- intents ----------

BOOK_INTENT = re.compile(
    r"\b(book|booking|schedule|set up|arrange)\b.{0,30}\b(meeting|call|appointment|chat|demo|sales|agent|team|someone)\b"
    r"|\b(talk|speak) (to|with) (someone|sales|a person|your team|the team|an agent|a human)\b"
    r"|\bbook (me )?(another|one more|it again)\b",
    re.IGNORECASE,
)
CANCEL_INTENT = re.compile(
    r"\b(cancel|call off|scrap)\b.{0,40}\b(call|meeting|booking|appointment|demo)\b", re.IGNORECASE
)
RESCHEDULE_INTENT = re.compile(
    r"\breschedule\b|\b(move|change|push|shift|postpone)\b.{0,40}\b(call|meeting|booking|appointment|demo)\b",
    re.IGNORECASE,
)
LIST_BOOKINGS = re.compile(
    r"\b(what|which|any|list|show)\b.{0,30}\b(calls|meetings|bookings|appointments)\b"
    r"|\bmy (upcoming )?(calls|meetings|bookings)\b|\bwhen is my (call|meeting)\b",
    re.IGNORECASE,
)
# "is my meeting booked?" / "did you book it?" -- answered from our records, never by the LLM
STATUS_QUESTION = re.compile(
    r"\b(is|was|has) (my|the|our) (meeting|call|booking|appointment)\b.{0,20}\b(booked|scheduled|confirmed|set)\b"
    r"|\bdid you (already |actually )?(book|schedule|confirm)\b|\bam i (booked|scheduled|confirmed)\b",
    re.IGNORECASE,
)

CONFIRM = re.compile(
    r"\b(yes|yeah|yep|yup|sure|ok|okay|perfect|absolutely|definitely|confirm|book it|go ahead|"
    r"sounds good|that works|please do|do it)\b",
    re.IGNORECASE,
)
# In a longer message a casual "ok" isn't a yes ("ok, what about tomorrow at 10?")
STRONG_CONFIRM = re.compile(r"\b(confirm|book it|go ahead|please do|do it)\b", re.IGNORECASE)
DECLINE = re.compile(r"\b(no|different time|another time|not that|actually)\b", re.IGNORECASE)
KEEP_IT = re.compile(r"\b(no|keep|don'?t|never mind|nevermind|leave it)\b", re.IGNORECASE)

# "yes, the same one" -> the saved email; "and also sam@x.com" -> saved email plus others
USE_SAVED_EMAIL = re.compile(r"\b(same|that one|that email|that address|saved|on file|use it|also|too|as well)\b", re.IGNORECASE)
NOT_SAVED_EMAIL = re.compile(r"\b(instead|rather|different|another email|other email|not that)\b", re.IGNORECASE)


def is_confirmation(message: str) -> bool:
    if not CONFIRM.search(message) or DECLINE.search(message):
        return False
    return len(message.split()) <= 6 or bool(STRONG_CONFIRM.search(message))


def is_short_non_time(message: str) -> bool:
    """A few words with no digits, e.g. "yes" or just an email address."""
    text = EMAIL.sub("", message)
    return len(text.split()) <= 4 and not re.search(r"\d", text)


# ---------- emails ----------

EMAIL = re.compile(r"[\w.+-]+@[\w-]+(\.[\w-]+)+")
# Voice users say "hamza at gmail dot com", which Whisper often transcribes literally
SPOKEN_EMAIL = re.compile(
    r"((?:[\w+-]+\s*(?:\.|\bdot\b)\s*)*[\w+-]+)\s+at\s+([\w-]+(?:\s*(?:\.|\bdot\b)\s*[\w-]+)+)", re.IGNORECASE
)
_SPOKEN_DOT = re.compile(r"\s*(?:\.|\bdot\b)\s*", re.IGNORECASE)


def find_email(message: str) -> str | None:
    emails = find_emails(message)
    return emails[0] if emails else None


def find_emails(message: str) -> list[str]:
    """Every email in the message, typed or spoken, in order and without duplicates."""
    found = [m.group(0).rstrip(".") for m in EMAIL.finditer(message)]
    if not found:
        spoken = SPOKEN_EMAIL.search(message)
        if spoken:
            local, domain = (_SPOKEN_DOT.sub(".", part) for part in spoken.groups())
            candidate = f"{local}@{domain}"
            if EMAIL.fullmatch(candidate):
                found = [candidate]
    return list(dict.fromkeys(e.lower() for e in found))


# ---------- times ----------

ISO_DATETIME = re.compile(r"\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}")
ISO_DATE = re.compile(r"\d{4}-\d{2}-\d{2}")
HH_MM = re.compile(r"^(\d{1,2}):(\d{2})")
PARTS_OF_DAY = {"morning": (0, 12), "afternoon": (12, 17), "evening": (17, 24)}


def safe_tz(name: str | None) -> str:
    try:
        ZoneInfo(name or "UTC")
        return name or "UTC"
    except Exception:
        return "UTC"


class TimeAsk(NamedTuple):
    exact: str | None = None  # 'YYYY-MM-DDTHH:MM' in the user's timezone
    day: str | None = None    # 'YYYY-MM-DD' when they only named a day
    part: str | None = None   # 'morning' / 'afternoon' / 'evening'


_TIME_PROMPT = (
    "Now it is {now} for the user. Next 14 days: {calendar}. Work out which meeting time the user "
    "is asking for in their LAST message. The assistant may have just offered or proposed times; "
    "'the first one', 'the 5pm one' or 'make it 11 instead' refer to those, and a bare time means "
    "the day being discussed. Reply with ONLY JSON: "
    '{{"date": "YYYY-MM-DD" or null, "time": "HH:MM" (24-hour) or null, '
    '"timezone": the timezone the user explicitly named, e.g. "UTC", or null, '
    '"part_of_day": "morning", "afternoon" or "evening" if they said one, or null}}. '
    "Give the time exactly as the user means it, without converting timezones. For a vague range "
    "like 'next week', give its first day as date. Use null for everything when they are not "
    "asking for a time.\n\nRecent conversation:\n{recent}"
)


async def read_requested_time(llm: LLMProvider, message: str, history: list[dict], tz: str) -> TimeAsk:
    """What time is the user asking for? The LLM sees the recent conversation (so "the first one"
    resolves against the times just offered) and a small calendar (so it doesn't do weekday
    arithmetic). It only reports what the user said; timezone conversion happens here in code."""
    inline = ISO_DATETIME.search(message)
    if inline:
        return TimeAsk(exact=inline.group(0).replace(" ", "T"))

    now = datetime.now(ZoneInfo(tz))
    calendar = ", ".join(f"{(now + timedelta(days=i)):%a %Y-%m-%d}" for i in range(14))
    recent = "\n".join(f"{m['role']}: {m['content']}" for m in history[-4:])
    prompt = _TIME_PROMPT.format(now=f"{now:%A %Y-%m-%d %H:%M}", calendar=calendar, recent=recent)
    try:
        with timed("llm_read_time"):
            raw = await llm.complete([{"role": "system", "content": prompt}, {"role": "user", "content": message}])
        parsed = json.loads(raw.strip().removeprefix("```json").removesuffix("```"))
    except Exception:
        return TimeAsk()  # unusable output means "no time found", never a guess

    part = parsed.get("part_of_day") if parsed.get("part_of_day") in PARTS_OF_DAY else None
    day = parsed.get("date")
    if not (isinstance(day, str) and ISO_DATE.fullmatch(day)):
        return TimeAsk(part=part)
    clock = HH_MM.match(str(parsed.get("time") or ""))
    if not clock:
        return TimeAsk(day=day, part=part)

    # A named timezone ("10am UTC") is converted to the user's own. Anything that isn't a real
    # timezone name (e.g. "PKT", echoed from Sarjy's own message) means the user's timezone.
    said = str(parsed.get("timezone") or "").strip()
    said = {"GMT": "UTC", "Z": "UTC"}.get(said.upper(), said)
    source_tz = said if said and safe_tz(said) == said else tz
    try:
        when = datetime.fromisoformat(f"{day}T{int(clock.group(1)):02d}:{clock.group(2)}")
    except ValueError:
        return TimeAsk(day=day, part=part)
    local = when.replace(tzinfo=ZoneInfo(source_tz)).astimezone(ZoneInfo(tz))
    return TimeAsk(exact=f"{local:%Y-%m-%dT%H:%M}")
