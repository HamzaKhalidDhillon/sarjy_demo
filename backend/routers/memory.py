from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from backend.db import get_db
from backend.models import Memory
from backend.routers.auth import current_user

router = APIRouter()


@router.get("/memory")
def list_memory(user_id: str = Depends(current_user), db: Session = Depends(get_db)):
    """Everything Sarjy remembers about the signed-in user (the UI's memory panel)."""
    rows = db.query(Memory).filter(Memory.user_id == user_id).order_by(Memory.id).all()
    return {"items": [{"id": r.id, "key": r.key, "value": r.value} for r in rows]}


@router.delete("/memory/{memory_id}")
def forget_memory(memory_id: int, user_id: str = Depends(current_user), db: Session = Depends(get_db)):
    row = db.query(Memory).filter(Memory.id == memory_id, Memory.user_id == user_id).first()
    if not row:
        raise HTTPException(404, "Not found")
    db.delete(row)
    db.commit()
    return {"ok": True}
