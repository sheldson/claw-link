"""Server-Sent Events endpoint for real-time push notifications."""

from __future__ import annotations

import asyncio
import json
import logging
from typing import AsyncGenerator

from fastapi import APIRouter
from starlette.responses import StreamingResponse

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/v1/events", tags=["events"])

# Global subscriber registry: claw_id -> asyncio.Queue
_subscribers: dict[str, asyncio.Queue] = {}


def has_subscriber(claw_id: str) -> bool:
    """Check whether a claw currently has an active SSE connection."""
    return claw_id in _subscribers


async def notify(claw_id: str, event: dict) -> bool:
    """Push an event to a connected SSE subscriber.

    Returns True if the event was delivered to a queue, False if no subscriber.
    """
    queue = _subscribers.get(claw_id)
    if queue is None:
        return False
    try:
        queue.put_nowait(event)
        return True
    except asyncio.QueueFull:
        logger.warning("SSE queue full for %s, dropping event", claw_id)
        return False


async def _event_stream(queue: asyncio.Queue, claw_id: str) -> AsyncGenerator[str, None]:
    """Yield SSE-formatted events for a claw, with keepalive."""
    try:
        # Send initial connected event so client knows stream is active
        yield f"data: {json.dumps({'type': 'connected', 'claw_id': claw_id})}\n\n"
        while True:
            try:
                event = await asyncio.wait_for(queue.get(), timeout=15.0)
                data = json.dumps(event, ensure_ascii=False)
                yield f"data: {data}\n\n"
            except asyncio.TimeoutError:
                # Send keepalive comment to prevent connection timeout
                yield ":\n\n"
    except asyncio.CancelledError:
        pass
    finally:
        _subscribers.pop(claw_id, None)
        logger.info("SSE subscriber disconnected: %s", claw_id)


@router.get("/{claw_id}/stream")
async def event_stream(claw_id: str):
    """SSE endpoint — long-lived stream of real-time events for a claw."""
    # Register subscriber BEFORE creating the response so events
    # arriving between now and the first generator yield are queued.
    queue: asyncio.Queue = asyncio.Queue(maxsize=256)
    _subscribers[claw_id] = queue
    logger.info("SSE subscriber connected: %s", claw_id)

    return StreamingResponse(
        _event_stream(queue, claw_id),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )
