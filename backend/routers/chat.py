from typing import Optional

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from backend.agent import memory
from backend.agent.orchestrator import agent
from backend.db import get_db
from backend.models import Conversation, Message
from backend.routers.auth import current_user

router = APIRouter()


class ChatRequest(BaseModel):
    conversation_id: Optional[int] = None
    message: str
    llm_provider: Optional[str] = None  # per-request override, e.g. "openai" | "offline"
    timezone: Optional[str] = None  # the browser's IANA timezone, e.g. "Asia/Karachi"


def get_or_create_conversation(
    db: Session, user_id: str, conversation_id: Optional[int], first_message: str = ""
) -> Conversation:
    conv = None
    if conversation_id:
        # Only continue a conversation that belongs to this user
        conv = db.query(Conversation).filter(
            Conversation.id == conversation_id, Conversation.user_id == user_id
        ).first()
    if not conv:
        # The first message doubles as the chat's title in the sidebar
        conv = Conversation(user_id=user_id, title=first_message.strip()[:60] or "conversation")
        db.add(conv)
        db.commit()
    return conv


@router.post("/message")
async def message(
    req: ChatRequest, background: BackgroundTasks,
    user_id: str = Depends(current_user), db: Session = Depends(get_db),
):
    conv = get_or_create_conversation(db, user_id, req.conversation_id, req.message)
    db.add(Message(conversation_id=conv.id, role="user", content=req.message))
    db.commit()

    reply = await agent.run_turn(db, user_id, conv.id, req.message, req.llm_provider, req.timezone)

    db.add(Message(conversation_id=conv.id, role="assistant", content=reply))
    db.commit()
    background.add_task(memory.remember, user_id, req.message)  # runs after the reply is sent
    return {"conversation_id": conv.id, "reply": reply}


@router.get("/history")
def history(conversation_id: int, user_id: str = Depends(current_user), db: Session = Depends(get_db)):
    conv = db.query(Conversation).filter(Conversation.id == conversation_id).first()
    if not conv or conv.user_id != user_id:
        raise HTTPException(404, "Conversation not found")
    rows = db.query(Message).filter(Message.conversation_id == conversation_id).order_by(Message.id).all()
    return {"items": [{"id": r.id, "role": r.role, "content": r.content} for r in rows]}


@router.get("/conversations")
def conversations(user_id: str = Depends(current_user), db: Session = Depends(get_db)):
    """The signed-in user's chats, newest first, for the sidebar."""
    rows = (
        db.query(Conversation).filter(Conversation.user_id == user_id)
        .order_by(Conversation.id.desc()).limit(50).all()
    )
    items = []
    for conv in rows:
        first = db.query(Message).filter(
            Message.conversation_id == conv.id, Message.role == "user"
        ).order_by(Message.id).first()
        if not first:
            continue  # created but never used
        title = conv.title if conv.title and conv.title != "conversation" else first.content
        items.append({"id": conv.id, "title": title[:60]})
    return {"items": items}
