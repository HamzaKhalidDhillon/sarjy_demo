import uuid

import httpx
import pytest

from backend.main import app


def _client():
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test")


async def _token(c, username, password="secret"):
    r = await c.post("/login", json={"username": username, "password": password})
    return {"Authorization": f"Bearer {r.json()['token']}"}


@pytest.mark.asyncio
async def test_first_login_creates_account_and_second_login_needs_same_password():
    name = f"user-{uuid.uuid4().hex[:8]}"
    async with _client() as c:
        first = await c.post("/login", json={"username": name, "password": "secret"})
        assert first.status_code == 200
        again = await c.post("/login", json={"username": name, "password": "secret"})
        assert again.json()["token"] == first.json()["token"]
        wrong = await c.post("/login", json={"username": name, "password": "wrong-password"})
        assert wrong.status_code == 401


@pytest.mark.asyncio
async def test_endpoints_require_sign_in():
    async with _client() as c:
        assert (await c.post("/message", json={"message": "hi"})).status_code == 401
        bad = await c.post("/message", json={"message": "hi"}, headers={"Authorization": "Bearer nope"})
        assert bad.status_code == 401


@pytest.mark.asyncio
async def test_cannot_read_someone_elses_conversation():
    async with _client() as c:
        alice = await _token(c, f"alice-{uuid.uuid4().hex[:8]}")
        bob = await _token(c, f"bob-{uuid.uuid4().hex[:8]}")

        conv_id = (await c.post("/message", json={"message": "hello"}, headers=alice)).json()["conversation_id"]
        assert (await c.get(f"/history?conversation_id={conv_id}", headers=alice)).status_code == 200
        assert (await c.get(f"/history?conversation_id={conv_id}", headers=bob)).status_code == 404

        # Bob passing Alice's conversation id just gets a new conversation of his own
        r = await c.post("/message", json={"conversation_id": conv_id, "message": "hi"}, headers=bob)
        assert r.json()["conversation_id"] != conv_id
