"""Tests for friend request, accept, reject, list, and delete."""

from __future__ import annotations

import pytest
from httpx import AsyncClient

from tests.conftest import register_claw, make_friends


@pytest.mark.asyncio
async def test_full_friend_flow(client: AsyncClient):
    """Register two claws, send request, accept, verify friendship."""
    alice, bob = await make_friends(client, "Alice", "Bob")

    # Both should see each other in friend lists
    resp_a = await client.get(f"/v1/friends/{alice['claw_id']}")
    assert resp_a.status_code == 200
    friends_a = resp_a.json()
    assert len(friends_a) == 1
    assert friends_a[0]["claw_id"] == bob["claw_id"]

    resp_b = await client.get(f"/v1/friends/{bob['claw_id']}")
    friends_b = resp_b.json()
    assert len(friends_b) == 1
    assert friends_b[0]["claw_id"] == alice["claw_id"]


@pytest.mark.asyncio
async def test_reject_friend_request(client: AsyncClient):
    alice = await register_claw(client, "Alice")
    bob = await register_claw(client, "Bob")

    req_resp = await client.post("/v1/friends/request", json={
        "from_id": alice["claw_id"],
        "to_id": bob["claw_id"],
    })
    request_id = req_resp.json()["request_id"]

    reject_resp = await client.post("/v1/friends/reject", json={
        "request_id": request_id,
        "claw_id": bob["claw_id"],
    })
    assert reject_resp.status_code == 200
    assert reject_resp.json()["status"] == "rejected"

    # No friends
    resp = await client.get(f"/v1/friends/{alice['claw_id']}")
    assert resp.json() == []


@pytest.mark.asyncio
async def test_cannot_accept_own_request(client: AsyncClient):
    alice = await register_claw(client, "Alice")
    bob = await register_claw(client, "Bob")

    req_resp = await client.post("/v1/friends/request", json={
        "from_id": alice["claw_id"],
        "to_id": bob["claw_id"],
    })
    request_id = req_resp.json()["request_id"]

    # Alice (the sender) tries to accept — should fail
    resp = await client.post("/v1/friends/accept", json={
        "request_id": request_id,
        "claw_id": alice["claw_id"],
    })
    assert resp.status_code == 403


@pytest.mark.asyncio
async def test_duplicate_request_rejected(client: AsyncClient):
    alice = await register_claw(client, "Alice")
    bob = await register_claw(client, "Bob")

    await client.post("/v1/friends/request", json={
        "from_id": alice["claw_id"],
        "to_id": bob["claw_id"],
    })
    resp = await client.post("/v1/friends/request", json={
        "from_id": alice["claw_id"],
        "to_id": bob["claw_id"],
    })
    assert resp.status_code == 409


@pytest.mark.asyncio
async def test_self_friend_request_rejected(client: AsyncClient):
    alice = await register_claw(client, "Alice")
    resp = await client.post("/v1/friends/request", json={
        "from_id": alice["claw_id"],
        "to_id": alice["claw_id"],
    })
    assert resp.status_code == 400


@pytest.mark.asyncio
async def test_pending_requests_list(client: AsyncClient):
    alice = await register_claw(client, "Alice")
    bob = await register_claw(client, "Bob")

    await client.post("/v1/friends/request", json={
        "from_id": alice["claw_id"],
        "to_id": bob["claw_id"],
    })

    resp = await client.get(f"/v1/friends/{bob['claw_id']}/requests")
    assert resp.status_code == 200
    reqs = resp.json()
    assert len(reqs) == 1
    assert reqs[0]["from_id"] == alice["claw_id"]
    assert reqs[0]["status"] == "pending"


@pytest.mark.asyncio
async def test_delete_friend(client: AsyncClient):
    alice, bob = await make_friends(client, "Alice", "Bob")

    resp = await client.delete(f"/v1/friends/{alice['claw_id']}/{bob['claw_id']}")
    assert resp.status_code == 204

    # Verify no longer friends
    resp = await client.get(f"/v1/friends/{alice['claw_id']}")
    assert resp.json() == []


@pytest.mark.asyncio
async def test_delete_nonexistent_friendship(client: AsyncClient):
    alice = await register_claw(client, "Alice")
    bob = await register_claw(client, "Bob")

    resp = await client.delete(f"/v1/friends/{alice['claw_id']}/{bob['claw_id']}")
    assert resp.status_code == 404
