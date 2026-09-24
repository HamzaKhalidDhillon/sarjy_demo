"""Backward-compat shim over the new llm/ and tts/ packages, kept so existing imports
(`from backend import mcp_adapter`) and tests keep working unchanged.
"""
from backend.llm.factory import get_llm_provider
from backend.tts.chain import TtsChain


async def query_llm(user_id: str, message: str) -> str:
    provider = get_llm_provider()
    return await provider.complete([{"role": "user", "content": message}])


async def tts_generate(text: str) -> bytes | None:
    return await TtsChain().run(text)
