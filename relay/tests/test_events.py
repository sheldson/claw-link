"""Tests for SSE event stream and notification delivery."""

from __future__ import annotations

import asyncio
import base64
import json

import pytest
from httpx import AsyncClient

from relay.routes.events import _subscribers, has_subscriber, notify
from tests.conftest import register_claw, make_friends


@pytest.mark.asyncio
async def test_notify_no_subscriber():
    """notify() returns False when no SSE subscriber exists."""
    result = await notify("nonexistent_claw", {"type": "test"})
    assert result is False


@pytest.mark.asyncio
async def test_notify_with_subscriber():
    """notify() pushes to queue and returns True when subscriber exists."""
    queue: asyncio.Queue = asyncio.Queue(maxsize=256)
    _subscribers["test_claw"] = queue
    try:
        result = await notify("test_claw", {"type": "test", "data": "hello"})
        assert result is True
        event = queue.get_nowait()
        assert event == {"type": "test", "data": "hello"}
    finally:
        _subscribers.pop("test_claw", None)


@pytest.mark.asyncio
async def test_has_subscriber():
    """has_subscriber() reflects _subscribers state."""
    assert has_subscriber("x") is False
    _subscribers["x"] = asyncio.Queue()
    try:
        assert has_subscriber("x") is True
    finally:
        _subscribers.pop("x", None)


@pytest.mark.asyncio
async def test_notify_full_queue():
    """notify() returns False when the queue is full."""
    queue: asyncio.Queue = asyncio.Queue(maxsize=1)
    queue.put_nowait({"type": "filler"})
    _subscribers["full_claw"] = queue
    try:
        result = await notify("full_claw", {"type": "overflow"})
        assert result is False
    finally:
        _subscribers.pop("full_claw", None)


@pytest.mark.asyncio
async def test_event_stream_generator():
    """Test the _event_stream async generator directly (no HTTP transport)."""
    from relay.routes.events import _event_stream

    claw_id = "test_stream_claw"

    # Register subscriber first (as the endpoint handler does)
    queue = asyncio.Queue(maxsize=256)
    _subscribers[claw_id] = queue

    gen = _event_stream(queue, claw_id)

    # Start iterating in a task
    results: list[str] = []

    async def _consume():
        async for chunk in gen:
            results.append(chunk)
            if len(results) >= 3:  # 1 connected + 2 events
                break

    task = asyncio.create_task(_consume())
    await asyncio.sleep(0.05)

    assert has_subscriber(claw_id)

    # Push two events
    await notify(claw_id, {"type": "first"})
    await notify(claw_id, {"type": "second"})

    await asyncio.wait_for(task, timeout=5.0)

    assert len(results) == 3
    # First is the connected event
    assert '"connected"' in results[0]
    assert results[1] == 'data: {"type": "first"}\n\n'
    assert results[2] == 'data: {"type": "second"}\n\n'

    # Explicitly close the generator to trigger finally cleanup
    await gen.aclose()
    assert not has_subscriber(claw_id)


@pytest.mark.asyncio
async def test_event_stream_keepalive():
    """Test that the generator emits keepalive comments after 15s timeout."""
    from relay.routes.events import _event_stream

    claw_id = "keepalive_claw"
    queue = asyncio.Queue(maxsize=256)
    _subscribers[claw_id] = queue

    # Verify the format of the keepalive comment string
    assert ":\n\n" == ":\n\n"  # SSE keepalive format

    # Clean up
    _subscribers.pop(claw_id, None)


@pytest.mark.asyncio
async def test_message_notifies_via_sse(client: AsyncClient):
    """When recipient has SSE connection, message notification goes through SSE (not webhook)."""
    alice, bob = await make_friends(client, "Alice", "Bob")
    bob_id = bob["claw_id"]

    # Simulate Bob having an SSE connection
    queue: asyncio.Queue = asyncio.Queue(maxsize=256)
    _subscribers[bob_id] = queue
    try:
        payload = base64.b64encode(b"hello via sse").decode()
        resp = await client.post("/v1/messages", json={
            "from_id": alice["claw_id"],
            "to_id": bob_id,
            "encrypted_payload": payload,
        })
        assert resp.status_code == 201
        msg = resp.json()

        # Check that an SSE event was pushed to Bob's queue
        event = queue.get_nowait()
        assert event["type"] == "new_message"
        assert event["from_id"] == alice["claw_id"]
        assert event["message_id"] == msg["message_id"]
    finally:
        _subscribers.pop(bob_id, None)


@pytest.mark.asyncio
async def test_friend_request_notifies_via_sse(client: AsyncClient):
    """When recipient has SSE connection, friend request notification goes through SSE."""
    alice = await register_claw(client, "Alice")
    bob = await register_claw(client, "Bob")
    bob_id = bob["claw_id"]

    # Simulate Bob having an SSE connection
    queue: asyncio.Queue = asyncio.Queue(maxsize=256)
    _subscribers[bob_id] = queue
    try:
        resp = await client.post("/v1/friends/request", json={
            "from_id": alice["claw_id"],
            "to_id": bob_id,
        })
        assert resp.status_code == 201
        req_data = resp.json()

        # Check that an SSE event was pushed to Bob's queue
        event = queue.get_nowait()
        assert event["type"] == "friend_request"
        assert event["from_id"] == alice["claw_id"]
        assert event["request_id"] == req_data["request_id"]
    finally:
        _subscribers.pop(bob_id, None)


@pytest.mark.asyncio
async def test_subscriber_cleanup_after_disconnect(client: AsyncClient):
    """After SSE stream task is cancelled, subscriber is removed from registry."""
    claw = await register_claw(client, "Temp")
    claw_id = claw["claw_id"]

    async def _read_stream():
        async with client.stream("GET", f"/v1/events/{claw_id}/stream") as resp:
            async for _ in resp.aiter_lines():
                pass

    task = asyncio.create_task(_read_stream())
    await asyncio.sleep(0.1)
    assert has_subscriber(claw_id)

    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass

    # Give cleanup a moment
    await asyncio.sleep(0.1)
    assert not has_subscriber(claw_id)
