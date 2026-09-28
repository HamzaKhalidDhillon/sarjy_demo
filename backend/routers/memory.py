from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from backend.db import SessionLocal
from backend.models import Memory
from backend.routers.auth import current_user

router = APIRouter()


@router.post("/memory/set")
def set_memory(key: str, value: str, user_id: str = Depends(current_user)):
    db: Session = SessionLocal()
    try:
        mem = Memory(user_id=user_id, key=key, value=value)
        db.add(mem)
        db.commit()
        db.refresh(mem)
        return {"id": mem.id}
    finally:
        db.close()


@router.get("/memory/get")
def get_memory(key: str, user_id: str = Depends(current_user)):
    db: Session = SessionLocal()
    try:
        rows = db.query(Memory).filter(Memory.user_id == user_id, Memory.key == key).all()
        return {"items": [{"id": r.id, "value": r.value} for r in rows]}
    finally:
        db.close()


@router.get("/memory")
def list_memory(user_id: str = Depends(current_user)):
    """Everything Sarjy remembers about the signed-in user (shown in the UI's memory panel)."""
    db: Session = SessionLocal()
    try:
        rows = db.query(Memory).filter(Memory.user_id == user_id).order_by(Memory.id).all()
        return {"items": [{"id": r.id, "key": r.key, "value": r.value} for r in rows]}
    finally:
        db.close()


@router.delete("/memory/{memory_id}")
def forget_memory(memory_id: int, user_id: str = Depends(current_user)):
    db: Session = SessionLocal()
    try:
        row = db.query(Memory).filter(Memory.id == memory_id, Memory.user_id == user_id).first()
        if not row:
            raise HTTPException(404, "Not found")
        db.delete(row)
        db.commit()
        return {"ok": True}
    finally:
        db.close()
