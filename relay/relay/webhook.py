"""Shared webhook notification helper."""

from __future__ import annotations

import logging

import httpx

logger = logging.getLogger(__name__)


async def fire_webhook(webhook_url: str, webhook_token: str | None, message: str) -> None:
    """Fire-and-forget POST to a claw's webhook URL."""
    headers = {}
    if webhook_token:
        headers["Authorization"] = f"Bearer {webhook_token}"
    payload = {
        "message": message,
        "wakeMode": "now",
    }
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            await client.post(webhook_url, json=payload, headers=headers)
    except Exception:
        logger.warning("Webhook delivery failed for %s", webhook_url, exc_info=True)
