import asyncio
import os
import sys
import pytest

# ensure repo root is on path so `backend` package can be imported
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))
from backend import mcp_adapter


@pytest.mark.asyncio
async def test_query_llm_offline():
    # Ensure that without OPENAI_API_KEY set, adapter returns offline echo
    prev = os.environ.pop('OPENAI_API_KEY', None)
    try:
        res = await mcp_adapter.query_llm('u1', 'hello world')
        assert '(offline)' in res or 'hello' in res.lower()
    finally:
        if prev:
            os.environ['OPENAI_API_KEY'] = prev


@pytest.mark.asyncio
async def test_tts_generate_offline():
    prev = os.environ.pop('OPENAI_API_KEY', None)
    try:
        out = await mcp_adapter.tts_generate('hello')
        assert out is None
    finally:
        if prev:
            os.environ['OPENAI_API_KEY'] = prev
