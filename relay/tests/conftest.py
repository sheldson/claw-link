"""Shared test fixtures — in-memory SQLite database and async client."""

from __future__ import annotations

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from relay.models import Base
from relay.database import get_session
from relay.main import app

# In-memory database for tests
TEST_DB_URL = "sqlite+aiosqlite:///:memory:"


@pytest_asyncio.fixture
async def client():
    """Yield an httpx AsyncClient wired to a fresh in-memory database."""
    engine = create_async_engine(TEST_DB_URL, echo=False)
    testing_session = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    async def _override_get_session():
        async with testing_session() as session:
            yield session

    app.dependency_overrides[get_session] = _override_get_session

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac

    app.dependency_overrides.clear()
    await engine.dispose()


async def register_claw(client: AsyncClient, name: str = "TestClaw") -> dict:
    """Helper — register a claw and return the response JSON."""
    resp = await client.post("/v1/register", json={"name": name, "public_key": "pk_test_" + name})
    assert resp.status_code == 201
    return resp.json()


async def make_friends(client: AsyncClient, a_name: str = "Alice", b_name: str = "Bob") -> tuple[dict, dict]:
    """Helper — register two claws and make them friends. Returns (a, b) registration data."""
    a = await register_claw(client, a_name)
    b = await register_claw(client, b_name)

    # Send request
    req_resp = await client.post("/v1/friends/request", json={
        "from_id": a["claw_id"],
        "to_id": b["claw_id"],
    })
    assert req_resp.status_code == 201
    request_id = req_resp.json()["request_id"]

    # Accept
    accept_resp = await client.post("/v1/friends/accept", json={
        "request_id": request_id,
        "claw_id": b["claw_id"],
    })
    assert accept_resp.status_code == 200

    return a, b
