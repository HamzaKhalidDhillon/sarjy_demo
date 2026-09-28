"""Everything the conversation flows need to know about the current turn, in one object."""
from dataclasses import dataclass, field

from sqlalchemy.orm import Session

from backend.agent.guardrails import SYSTEM_PROMPT
from backend.agent.parsing import TimeAsk, read_requested_time
from backend.agent.state import ConversationStateMachine
from backend.core.logging import timed
from backend.llm.base import LLMProvider

IN_PROGRESS_NOTE = "A booking is already in progress, so don't tell the user to say 'book a call'."


@dataclass
class Turn:
    db: Session
    user_id: str
    conversation_id: int
    message: str
    llm: LLMProvider
    state: ConversationStateMachine
    tz: str = "UTC"
    history: list[dict] = field(default_factory=list)
    context: str = ""  # memory facts and upcoming calls, added to the system prompt
    saved_email: str | None = None

    async def llm_reply(self, note: str = "") -> str:
        system = " ".join(part for part in (SYSTEM_PROMPT, self.context, note) if part)
        messages = [{"role": "system", "content": system}, *self.history, {"role": "user", "content": self.message}]
        with timed("llm_chat"):
            return await self.llm.complete(messages)

    async def answer_and_nudge(self, nudge: str) -> str:
        """Off-script mid-flow ("how long is the call?"): answer it, then steer back."""
        reply = await self.llm_reply(IN_PROGRESS_NOTE)
        return f"{reply}\n\n{nudge}"

    async def read_time(self) -> TimeAsk:
        return await read_requested_time(self.llm, self.message, self.history, self.tz)
