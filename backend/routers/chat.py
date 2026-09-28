from typing import Optional

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from backend.agent import memory
from backend.agent.orchestrator import agent
from backend.db import SessionLocal
from backend.models import Conversation, Message
from backend.routers.auth import current_user

router = APIRouter()


class ChatRequest(BaseModel):
    conversation_id: Optional[int] = None
    message: str
    llm_provider: Optional[str] = None  # per-request override, e.g. "openai" | "gemini" | "offline"
    timezone: Optional[str] = None  # the browser's IANA timezone, e.g. "Asia/Karachi"


def _get_or_create_conversation(
    db: Session, user_id: str, conversation_id: Optional[int], first_message: str = ""
) -> Conversation:
    conv = None
    if conversation_id:
        # Only continue a conversation that belongs to this user
        conv = (
            db.query(Conversation)
            .filter(Conversation.id == conversation_id, Conversation.user_id == user_id)
            .first()
        )
    if not conv:
        # the first message doubles as the chat's title in the sidebar
        conv = Conversation(user_id=user_id, title=first_message.strip()[:60] or "conversation")
        db.add(conv)
        db.commit()
        db.refresh(conv)
    return conv


@router.post("/message")
async def message(req: ChatRequest, background: BackgroundTasks, user_id: str = Depends(current_user)):
    db: Session = SessionLocal()
    try:
        conv = _get_or_create_conversation(db, user_id, req.conversation_id, req.message)

        user_msg = Message(conversation_id=conv.id, role="user", content=req.message)
        db.add(user_msg)
        db.commit()

        reply = await agent.run_turn(db, user_id, conv.id, req.message, req.llm_provider, req.timezone)

        assist_msg = Message(conversation_id=conv.id, role="assistant", content=reply)
        db.add(assist_msg)
        db.commit()

        background.add_task(memory.remember, user_id, req.message)  # runs after the reply is sent
        return {"conversation_id": conv.id, "reply": reply}
    finally:
        db.close()


@router.get("/history")
def history(conversation_id: int, user_id: str = Depends(current_user)):
    db: Session = SessionLocal()
    try:
        conv = db.query(Conversation).filter(Conversation.id == conversation_id).first()
        if not conv or conv.user_id != user_id:
            raise HTTPException(404, "Conversation not found")
        rows = (
            db.query(Message)
            .filter(Message.conversation_id == conversation_id)
            .order_by(Message.id)
            .all()
        )
        return {"items": [{"id": r.id, "role": r.role, "content": r.content} for r in rows]}
    finally:
        db.close()


@router.get("/conversations")
def conversations(user_id: str = Depends(current_user)):
    """The signed-in user's chats, newest first, for the sidebar."""
    db: Session = SessionLocal()
    try:
        rows = (
            db.query(Conversation).filter(Conversation.user_id == user_id)
            .order_by(Conversation.id.desc()).limit(50).all()
        )
        items = []
        for conv in rows:
            first = (
                db.query(Message).filter(Message.conversation_id == conv.id, Message.role == "user")
                .order_by(Message.id).first()
            )
            if not first:
                continue  # never used
            title = conv.title if conv.title and conv.title != "conversation" else first.content
            items.append({"id": conv.id, "title": title[:60]})
        return {"items": items}
    finally:
        db.close()
