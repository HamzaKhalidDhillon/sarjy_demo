import httpx

from backend.core.config import settings
from backend.core.errors import ProviderError
from backend.core.http import request_with_retry
from backend.llm.base import LLMProvider


class OpenAILLMProvider(LLMProvider):
    name = "openai"

    async def complete(self, messages: list[dict], **kwargs) -> str:
        url = "https://api.openai.com/v1/chat/completions"
        headers = {"Authorization": f"Bearer {settings.openai_api_key}"}
        payload = {
            "model": kwargs.get("model", settings.openai_chat_model),
            "messages": messages,
            "max_tokens": kwargs.get("max_tokens", 300),
        }
        async with httpx.AsyncClient(timeout=httpx.Timeout(connect=5, read=45, write=10, pool=5)) as client:
            response = await request_with_retry(client, "POST", url, json=payload, headers=headers)
        if response.status_code >= 400:
            raise ProviderError(f"OpenAI chat completion failed: {response.status_code} {response.text}")
        data = response.json()
        return data["choices"][0]["message"]["content"].strip()
