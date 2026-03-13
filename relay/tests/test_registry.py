"""Tests for claw registration and lookup."""

from __future__ import annotations

import base64
import json

import pytest
from httpx import AsyncClient

from tests.conftest import register_claw, make_friends


@pytest.mark.asyncio
async def test_register_claw(client: AsyncClient):
    data = await register_claw(client, "Pinchy")
    assert data["claw_id"].startswith("claw_")
    assert len(data["claw_id"]) == len("claw_") + 8
    assert data["name"] == "Pinchy"
    assert data["public_key"] == "pk_test_Pinchy"
    assert "created_at" in data


@pytest.mark.asyncio
async def test_register_multiple_unique_ids(client: AsyncClient):
    ids = set()
    for i in range(10):
        data = await register_claw(client, f"Claw{i}")
        ids.add(data["claw_id"])
    assert len(ids) == 10


@pytest.mark.asyncio
async def test_get_claw(client: AsyncClient):
    reg = await register_claw(client, "Larry")
    resp = await client.get(f"/v1/claws/{reg['claw_id']}")
    assert resp.status_code == 200
    info = resp.json()
    assert info["claw_id"] == reg["claw_id"]
    assert info["name"] == "Larry"
    assert info["public_key"] == "pk_test_Larry"


@pytest.mark.asyncio
async def test_get_nonexistent_claw(client: AsyncClient):
    resp = await client.get("/v1/claws/claw_notreal1")
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_register_empty_name_rejected(client: AsyncClient):
    resp = await client.post("/v1/register", json={"name": "", "public_key": "pk"})
    assert resp.status_code == 422


# ── Deregistration tests ────────────────────────────────────────


@pytest.mark.asyncio
async def test_deregister_claw(client: AsyncClient):
    """Deregistering a claw should return 204 and make it unfindable."""
    reg = await register_claw(client, "Ephemeral")
    lid = reg["claw_id"]

    resp = await client.delete(f"/v1/claws/{lid}")
    assert resp.status_code == 204

    # Should no longer exist
    resp = await client.get(f"/v1/claws/{lid}")
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_deregister_nonexistent_claw(client: AsyncClient):
    resp = await client.delete("/v1/claws/claw_notreal1")
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_deregister_sends_goodbye_to_friends(client: AsyncClient):
    """After deregistration, each friend should receive a goodbye message."""
    alice, bob = await make_friends(client, "Alice", "Bob")
    alice_id = alice["claw_id"]
    bob_id = bob["claw_id"]

    # Deregister Alice
    resp = await client.delete(f"/v1/claws/{alice_id}")
    assert resp.status_code == 204

    # Bob should have a pending goodbye message
    pending_resp = await client.get(f"/v1/messages/{bob_id}/pending")
    assert pending_resp.status_code == 200
    pending = pending_resp.json()
    assert len(pending) == 1

    msg = pending[0]
    assert msg["from_id"] == alice_id
    assert msg["to_id"] == bob_id

    # Decode the goodbye payload (it's base64-encoded plain JSON, not encrypted)
    payload = json.loads(base64.b64decode(msg["encrypted_payload"]).decode("utf-8"))
    assert payload["type"] == "goodbye"
    assert payload["claw_id"] == alice_id
    assert payload["name"] == "Alice"


@pytest.mark.asyncio
async def test_deregister_clears_friendships(client: AsyncClient):
    """After deregistration, the remaining friend's friend list should be empty."""
    alice, bob = await make_friends(client, "Alice", "Bob")
    alice_id = alice["claw_id"]
    bob_id = bob["claw_id"]

    await client.delete(f"/v1/claws/{alice_id}")

    # Bob's friend list should be empty
    resp = await client.get(f"/v1/friends/{bob_id}")
    assert resp.status_code == 200
    assert resp.json() == []


@pytest.mark.asyncio
async def test_deregister_clears_friend_requests(client: AsyncClient):
    """Pending friend requests involving the deregistered claw are removed."""
    alice = await register_claw(client, "Alice")
    bob = await register_claw(client, "Bob")
    alice_id = alice["claw_id"]
    bob_id = bob["claw_id"]

    # Send a friend request from Alice to Bob (don't accept)
    await client.post("/v1/friends/request", json={
        "from_id": alice_id,
        "to_id": bob_id,
    })

    # Deregister Alice
    await client.delete(f"/v1/claws/{alice_id}")

    # Bob should have no pending requests
    resp = await client.get(f"/v1/friends/{bob_id}/requests")
    assert resp.status_code == 200
    assert resp.json() == []


@pytest.mark.asyncio
async def test_deregister_multiple_friends_all_get_goodbye(client: AsyncClient):
    """When a claw with multiple friends deregisters, all friends get goodbye."""
    alice = await register_claw(client, "Alice")
    bob = await register_claw(client, "Bob")
    carol = await register_claw(client, "Carol")

    alice_id = alice["claw_id"]
    bob_id = bob["claw_id"]
    carol_id = carol["claw_id"]

    # Make Alice friends with both Bob and Carol
    req1 = await client.post("/v1/friends/request", json={
        "from_id": alice_id, "to_id": bob_id,
    })
    await client.post("/v1/friends/accept", json={
        "request_id": req1.json()["request_id"], "claw_id": bob_id,
    })

    req2 = await client.post("/v1/friends/request", json={
        "from_id": alice_id, "to_id": carol_id,
    })
    await client.post("/v1/friends/accept", json={
        "request_id": req2.json()["request_id"], "claw_id": carol_id,
    })

    # Deregister Alice
    resp = await client.delete(f"/v1/claws/{alice_id}")
    assert resp.status_code == 204

    # Both Bob and Carol should have goodbye messages
    for friend_id in (bob_id, carol_id):
        pending_resp = await client.get(f"/v1/messages/{friend_id}/pending")
        pending = pending_resp.json()
        assert len(pending) == 1
        payload = json.loads(base64.b64decode(pending[0]["encrypted_payload"]).decode("utf-8"))
        assert payload["type"] == "goodbye"
        assert payload["claw_id"] == alice_id


@pytest.mark.asyncio
async def test_deregister_no_friends_ok(client: AsyncClient):
    """Deregistering a claw with no friends should succeed cleanly."""
    reg = await register_claw(client, "Loner")
    resp = await client.delete(f"/v1/claws/{reg['claw_id']}")
    assert resp.status_code == 204
