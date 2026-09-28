"""Cross-session memory. recall() loads the user's facts into the LLM context before each
reply; remember() picks new facts out of a message afterwards, as a background task so it adds
no latency. The LLM does the picking (people say "this is Hamza" or "I'm into sushi", which fixed
patterns miss); the regex patterns below are the fallback when there's no LLM.
"""
import json
import re

from sqlalchemy.orm import Session

from backend.agent.guardrails import InputGuardrail
from backend.agent.parsing import EMAIL
from backend.core.logging import logger, timed
from backend.db import SessionLocal
from backend.llm.factory import get_llm_provider
from backend.models import Memory

# Fallback extractor for when no LLM is configured
_PATTERNS: list[tuple[str, str]] = [
    (r"\bmy favorite (\w+) is ([\w\s]+)", "favorite_{0}"),
    (r"\bmy favourite (\w+) is ([\w\s]+)", "favorite_{0}"),
    (r"\bmy name is ([\w\s]+)", "name"),
    (r"\bi live in ([\w\s,]+)", "location"),
    (r"\bi work at ([\w\s,]+)", "employer"),
    (r"\bremember that (.+)", "note"),
]


def recall(db: Session, user_id: str) -> list[Memory]:
    return db.query(Memory).filter(Memory.user_id == user_id).all()


def saved_email(memories: list[Memory]) -> str | None:
    """The user's email, if we have one (saved after a booking, or mentioned before)."""
    for m in sorted(memories, key=lambda m: m.key != "email"):
        value = m.value.strip()
        if "email" in m.key and EMAIL.fullmatch(value):
            return value
    return None


def format_memories_for_prompt(memories: list[Memory]) -> str:
    if not memories:
        return ""
    facts = "; ".join(f"{m.key}={m.value}" for m in memories)
    return f"Known facts about this user from previous sessions: {facts}."


def save_fact(db: Session, user_id: str, key: str, value: str) -> None:
    existing = db.query(Memory).filter(Memory.user_id == user_id, Memory.key == key).first()
    if existing:
        existing.value = value
    else:
        db.add(Memory(user_id=user_id, key=key, value=value))
    db.commit()


def extract_facts(db: Session, user_id: str, user_message: str) -> list[str]:
    """Returns the list of memory keys written this turn (for logging/testing)."""
    lowered = user_message.lower()
    written: list[str] = []
    for pattern, key_template in _PATTERNS:
        match = re.search(pattern, lowered)
        if not match:
            continue
        groups = match.groups()
        if len(groups) == 2:
            key = key_template.format(groups[0].strip().replace(" ", "_"))
            value = groups[1].strip()
        else:
            key = key_template
            value = groups[0].strip()
        if value:
            save_fact(db, user_id, key, value)
            written.append(key)
    return written


_EXTRACT_PROMPT = (
    "You pick out facts about the user worth remembering in future conversations: their name, "
    "job, company, location, preferences, likes and dislikes, plans. Ignore small talk, questions "
    "and anything about the assistant. Reply with ONLY JSON like "
    '{"facts": {"name": "Hamza", "favorite_food": "sushi"}} using short snake_case keys, or '
    '{"facts": {}} if there is nothing worth remembering. '
    "Facts already saved (reuse the same key when a fact changes): "
)
_KEY = re.compile(r"^[a-z][a-z0-9_]{0,39}$")


async def remember(user_id: str, user_message: str) -> list[str]:
    """Background task: save any new facts from this message. Returns the keys written."""
    if not InputGuardrail.check(user_message).allowed:
        return []  # never store anything from a blocked message

    db = SessionLocal()
    try:
        known = {m.key: m.value for m in recall(db, user_id)}
        facts = None
        try:
            with timed("memory_extract_llm"):
                raw = await get_llm_provider().complete([
                    {"role": "system", "content": _EXTRACT_PROMPT + json.dumps(known)},
                    {"role": "user", "content": user_message},
                ])
            facts = json.loads(raw.strip().removeprefix("```json").removesuffix("```")).get("facts")
        except Exception:
            pass  # offline provider, bad JSON, network error -> regex fallback below

        if not isinstance(facts, dict):
            return extract_facts(db, user_id, user_message)

        written = []
        for key, value in list(facts.items())[:10]:
            if isinstance(value, list):  # e.g. {"likes": ["hiking", "chess"]}
                value = ", ".join(str(v) for v in value)
            value = str(value).strip()[:200]
            if _KEY.match(str(key)) and value and known.get(key) != value:
                save_fact(db, user_id, key, value)
                written.append(key)
        if written:
            logger.info("memory saved keys=%s", written)
        return written
    finally:
        db.close()
