"""Simple sign-in. No separate signup: the first login with a new username creates the account,
after that the password has to match. The returned token goes in the Authorization header, and
every other endpoint gets the user from it instead of trusting a user_id sent by the browser.
"""
import hashlib
import secrets
from typing import Optional

from fastapi import APIRouter, Header, HTTPException
from pydantic import BaseModel

from backend.db import SessionLocal
from backend.models import User

router = APIRouter()


class LoginRequest(BaseModel):
    username: str
    password: str


def _hash_password(password: str, salt: str) -> str:
    return hashlib.pbkdf2_hmac("sha256", password.encode(), salt.encode(), 200_000).hex()


def _check_password(password: str, stored: str) -> bool:
    salt, expected = stored.split("$")
    return secrets.compare_digest(_hash_password(password, salt), expected)


@router.post("/login")
def login(req: LoginRequest):
    username = req.username.strip().lower()
    if not username or len(req.password) < 4:
        raise HTTPException(400, "Username is required and password must be at least 4 characters")

    db = SessionLocal()
    try:
        user = db.query(User).filter(User.username == username).first()
        if not user:
            salt = secrets.token_hex(16)
            user = User(username=username, password_hash=f"{salt}${_hash_password(req.password, salt)}")
            db.add(user)
        elif not _check_password(req.password, user.password_hash):
            raise HTTPException(401, "Wrong password for that username")

        # Reuse the existing token so signing in on a second device doesn't log out the first
        if not user.token:
            user.token = secrets.token_urlsafe(32)
        db.commit()
        return {"username": user.username, "token": user.token}
    finally:
        db.close()


def current_user(authorization: Optional[str] = Header(None)) -> str:
    """FastAPI dependency: 'Bearer <token>' -> username (used as user_id everywhere)."""
    token = (authorization or "").removeprefix("Bearer ").strip()
    if not token:
        raise HTTPException(401, "Not signed in")
    db = SessionLocal()
    try:
        user = db.query(User).filter(User.token == token).first()
        if not user:
            raise HTTPException(401, "Not signed in")
        return user.username
    finally:
        db.close()
