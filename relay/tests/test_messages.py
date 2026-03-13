"""Tests for encrypted message send, pending retrieval, and deletion."""

from __future__ import annotations

import base64

import pytest
from httpx import AsyncClient

from tests.conftest import register_claw, make_friends


@pytest.mark.asyncio
async def test_send_and_receive_message(client: AsyncClient):
    alice, bob = await make_friends(client, "Alice", "Bob")

    payload = base64.b64encode(b"encrypted hello").decode()
    send_resp = await client.post("/v1/messages", json={
        "from_id": alice["claw_id"],
        "to_id": bob["claw_id"],
        "encrypted_payload": payload,
    })
    assert send_resp.status_code == 201
    msg = send_resp.json()
    assert msg["from_id"] == alice["claw_id"]
    assert msg["to_id"] == bob["claw_id"]
    assert msg["encrypted_payload"] == payload

    # Bob fetches pending
    pending_resp = await client.get(f"/v1/messages/{bob['claw_id']}/pending")
    assert pending_resp.status_code == 200
    pending = pending_resp.json()
    assert len(pending) == 1
    assert pending[0]["message_id"] == msg["message_id"]
    assert pending[0]["encrypted_payload"] == payload


@pytest.mark.asyncio
async def test_delete_message_after_receipt(client: AsyncClient):
    alice, bob = await make_friends(client, "Alice", "Bob")

    payload = base64.b64encode(b"secret").decode()
    send_resp = await client.post("/v1/messages", json={
        "from_id": alice["claw_id"],
        "to_id": bob["claw_id"],
        "encrypted_payload": payload,
    })
    msg_id = send_resp.json()["message_id"]

    # Delete (confirm receipt)
    del_resp = await client.delete(f"/v1/messages/{msg_id}")
    assert del_resp.status_code == 204

    # Pending should be empty now
    pending_resp = await client.get(f"/v1/messages/{bob['claw_id']}/pending")
    assert pending_resp.json() == []


@pytest.mark.asyncio
async def test_cannot_send_to_non_friend(client: AsyncClient):
    alice = await register_claw(client, "Alice")
    bob = await register_claw(client, "Bob")

    payload = base64.b64encode(b"hi").decode()
    resp = await client.post("/v1/messages", json={
        "from_id": alice["claw_id"],
        "to_id": bob["claw_id"],
        "encrypted_payload": payload,
    })
    assert resp.status_code == 403


@pytest.mark.asyncio
async def test_cannot_send_to_self(client: AsyncClient):
    alice = await register_claw(client, "Alice")
    payload = base64.b64encode(b"echo").decode()
    resp = await client.post("/v1/messages", json={
        "from_id": alice["claw_id"],
        "to_id": alice["claw_id"],
        "encrypted_payload": payload,
    })
    assert resp.status_code == 400


@pytest.mark.asyncio
async def test_invalid_base64_rejected(client: AsyncClient):
    alice, bob = await make_friends(client, "Alice", "Bob")

    resp = await client.post("/v1/messages", json={
        "from_id": alice["claw_id"],
        "to_id": bob["claw_id"],
        "encrypted_payload": "not-valid-base64!!!",
    })
    assert resp.status_code == 400


@pytest.mark.asyncio
async def test_delete_nonexistent_message(client: AsyncClient):
    resp = await client.delete("/v1/messages/nonexistent-id")
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_multiple_messages_ordered(client: AsyncClient):
    alice, bob = await make_friends(client, "Alice", "Bob")

    for i in range(3):
        payload = base64.b64encode(f"msg_{i}".encode()).decode()
        await client.post("/v1/messages", json={
            "from_id": alice["claw_id"],
            "to_id": bob["claw_id"],
            "encrypted_payload": payload,
        })

    pending_resp = await client.get(f"/v1/messages/{bob['claw_id']}/pending")
    pending = pending_resp.json()
    assert len(pending) == 3

    # Verify order
    for i, m in enumerate(pending):
        decoded = base64.b64decode(m["encrypted_payload"]).decode()
        assert decoded == f"msg_{i}"


@pytest.mark.asyncio
async def test_sender_does_not_see_own_messages(client: AsyncClient):
    alice, bob = await make_friends(client, "Alice", "Bob")

    payload = base64.b64encode(b"hello bob").decode()
    await client.post("/v1/messages", json={
        "from_id": alice["claw_id"],
        "to_id": bob["claw_id"],
        "encrypted_payload": payload,
    })

    # Alice should have no pending messages
    resp = await client.get(f"/v1/messages/{alice['claw_id']}/pending")
    assert resp.json() == []
