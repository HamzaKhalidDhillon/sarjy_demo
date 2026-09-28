from typing import Optional

from fastapi import APIRouter, Depends

from backend.core.config import settings
from backend.core.errors import ProviderError
from backend.llm.gemini_provider import GeminiLLMProvider
from backend.routers.auth import current_user

router = APIRouter()


@router.post("/gemini_test")
async def gemini_test(prompt: Optional[str] = None, user_id: str = Depends(current_user)):
    # Behind sign-in: it spends real API credits and skips the guardrails
    if not settings.gemini_api_key:
        return {"ok": False, "error": "GEMINI_API_KEY not set in environment"}
    try:
        text = await GeminiLLMProvider().complete(
            [{"role": "user", "content": prompt or "Say hello in one sentence."}]
        )
        return {"ok": True, "text": text}
    except ProviderError as exc:
        return {"ok": False, "error": str(exc)}
