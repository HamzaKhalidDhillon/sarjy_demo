"""Cross-session memory: recall() feeds known facts into the LLM context before a reply is
generated, extract_facts() pulls new facts out of the exchange afterward. This is what actually
makes "what's my favorite color?" work across sessions -- previously /memory/set and /memory/get
existed but nothing ever called them automatically.
"""
import re

from sqlalchemy.orm import Session

from backend.models import Memory

# Deliberately a fast deterministic extractor rather than a second LLM call: cheap, testable,
# no added latency/cost. An LLM-based extractor behind a flag is a reasonable future upgrade.
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


def format_memories_for_prompt(memories: list[Memory]) -> str:
    if not memories:
        return ""
    facts = "; ".join(f"{m.key}={m.value}" for m in memories)
    return f"Known facts about this user from previous sessions: {facts}."


def _upsert(db: Session, user_id: str, key: str, value: str) -> None:
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
            _upsert(db, user_id, key, value)
            written.append(key)
    return written
