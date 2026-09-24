from fastapi import APIRouter
from sqlalchemy.orm import Session

from backend.db import SessionLocal
from backend.models import Memory

router = APIRouter()


@router.post("/memory/set")
def set_memory(user_id: str, key: str, value: str):
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
def get_memory(user_id: str, key: str):
    db: Session = SessionLocal()
    try:
        rows = db.query(Memory).filter(Memory.user_id == user_id, Memory.key == key).all()
        return {"items": [{"id": r.id, "value": r.value} for r in rows]}
    finally:
        db.close()
