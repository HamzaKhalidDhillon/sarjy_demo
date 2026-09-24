import httpx

from backend.core.config import settings
from backend.core.errors import ProviderError
from backend.core.http import request_with_retry
from backend.llm.base import LLMProvider


class GeminiLLMProvider(LLMProvider):
    name = "gemini"

    async def complete(self, messages: list[dict], **kwargs) -> str:
        prompt = "\n".join(f"{m['role']}: {m['content']}" for m in messages)
        model = kwargs.get("model", settings.gemini_model)
        url = (
            f"https://generativelanguage.googleapis.com/v1beta2/models/{model}:generate"
            f"?key={settings.gemini_api_key}"
        )
        payload = {"prompt": {"text": prompt}, "temperature": 0.2, "maxOutputTokens": 256}
        async with httpx.AsyncClient(timeout=httpx.Timeout(connect=5, read=30, write=10, pool=5)) as client:
            response = await request_with_retry(client, "POST", url, json=payload)
        if response.status_code >= 400:
            raise ProviderError(f"Gemini generation failed: {response.status_code} {response.text}")
        data = response.json()
        if data.get("candidates"):
            return (data["candidates"][0].get("output") or "").strip()
        raise ProviderError(f"Gemini response had no candidates: {data}")
