"""App factory + wiring only. All business logic lives in routers/, agent/, llm/, stt/, tts/,
tools/. Run with `uvicorn backend.main:app` from the repo root (see README).
"""
import time
import uuid
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import RedirectResponse
from fastapi.staticfiles import StaticFiles

from backend.core.config import settings
from backend.core.logging import configure_logging, logger, request_id_var
from backend.db import init_db
from backend.routers import auth, chat, health, memory, voice

configure_logging()
init_db()

app = FastAPI(title="Sarjy Backend")

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.allowed_origins_list,
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.middleware("http")
async def add_request_id(request: Request, call_next):
    token = request_id_var.set(uuid.uuid4().hex[:12])
    start = time.monotonic()
    try:
        response = await call_next(request)
    finally:
        elapsed_ms = (time.monotonic() - start) * 1000
        logger.info("path=%s ms=%.1f", request.url.path, elapsed_ms)
        request_id_var.reset(token)
    return response


REPO_ROOT = Path(__file__).resolve().parent.parent


@app.get("/", include_in_schema=False)
def root():
    return RedirectResponse("/frontend/")


app.mount("/frontend", StaticFiles(directory=str(REPO_ROOT / "frontend"), html=True), name="frontend")

app.include_router(health.router)
app.include_router(auth.router)
app.include_router(chat.router)
app.include_router(voice.router)
app.include_router(memory.router)
