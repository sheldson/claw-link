"""Encrypted message relay — store and forward."""

from __future__ import annotations

import asyncio
import logging
import uuid
import base64
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select, or_, and_, delete
from sqlalchemy.ext.asyncio import AsyncSession

from relay.config import settings
from relay.database import get_session
from relay.models import Friendship, Claw, PendingMessage
from relay.schemas import SendMessageRequest, MessageInfo
from relay.webhook import fire_webhook

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/v1/messages", tags=["messages"])


async def _are_friends(db: AsyncSession, a: str, b: str) -> bool:
    stmt = select(Friendship).where(
        or_(
            and_(Friendship.claw_a_id == a, Friendship.claw_b_id == b),
            and_(Friendship.claw_a_id == b, Friendship.claw_b_id == a),
        )
    )
    result = await db.execute(stmt)
    return result.scalar_one_or_none() is not None


async def _cleanup_expired(db: AsyncSession) -> None:
    """Delete messages past their expiry."""
    now = datetime.now(timezone.utc)
    stmt = delete(PendingMessage).where(PendingMessage.expires_at < now)
    await db.execute(stmt)


@router.post("", response_model=MessageInfo, status_code=201)
async def send_message(
    body: SendMessageRequest, db: AsyncSession = Depends(get_session)
):
    """Store an encrypted message for later pickup. Both parties must be friends."""
    if body.from_id == body.to_id:
        raise HTTPException(status_code=400, detail="Cannot send a message to yourself")

    for lid in (body.from_id, body.to_id):
        if (await db.get(Claw, lid)) is None:
            raise HTTPException(status_code=404, detail=f"Claw {lid} not found")

    if not await _are_friends(db, body.from_id, body.to_id):
        raise HTTPException(status_code=403, detail="You must be friends to exchange messages")

    # Validate base64
    try:
        payload_bytes = base64.b64decode(body.encrypted_payload)
    except Exception:
        raise HTTPException(status_code=400, detail="encrypted_payload must be valid base64")

    # Check pending message count for recipient
    count_stmt = select(PendingMessage).where(PendingMessage.to_id == body.to_id)
    count_result = await db.execute(count_stmt)
    if len(count_result.scalars().all()) >= settings.max_pending_messages:
        raise HTTPException(status_code=429, detail="Recipient has too many pending messages")

    now = datetime.now(timezone.utc)
    expires = now + timedelta(days=settings.message_expire_days)

    msg = PendingMessage(
        id=str(uuid.uuid4()),
        from_id=body.from_id,
        to_id=body.to_id,
        encrypted_payload=payload_bytes,
        created_at=now,
        expires_at=expires,
    )
    db.add(msg)
    await db.commit()
    await db.refresh(msg)

    # Fire webhook notification to recipient (fire-and-forget)
    recipient = await db.get(Claw, body.to_id)
    if recipient and recipient.webhook_url:
        asyncio.create_task(
            fire_webhook(
                recipient.webhook_url,
                recipient.webhook_token,
                f"You have a new ClawLink message from {body.from_id}. Use claw_check_messages to read it.",
            )
        )

    return MessageInfo(
        message_id=msg.id,
        from_id=msg.from_id,
        to_id=msg.to_id,
        encrypted_payload=base64.b64encode(msg.encrypted_payload).decode(),
        created_at=msg.created_at,
        expires_at=msg.expires_at,
    )


@router.get("/{claw_id}/pending", response_model=list[MessageInfo])
async def get_pending_messages(
    claw_id: str, db: AsyncSession = Depends(get_session)
):
    """Fetch all pending (undelivered) messages for a claw."""
    if (await db.get(Claw, claw_id)) is None:
        raise HTTPException(status_code=404, detail="Claw not found")

    # Clean up expired messages first
    await _cleanup_expired(db)
    await db.commit()

    stmt = (
        select(PendingMessage)
        .where(PendingMessage.to_id == claw_id)
        .order_by(PendingMessage.created_at)
    )
    result = await db.execute(stmt)
    messages = result.scalars().all()

    # Update last_seen
    claw = await db.get(Claw, claw_id)
    claw.last_seen = datetime.now(timezone.utc)
    await db.commit()

    return [
        MessageInfo(
            message_id=m.id,
            from_id=m.from_id,
            to_id=m.to_id,
            encrypted_payload=base64.b64encode(m.encrypted_payload).decode(),
            created_at=m.created_at,
            expires_at=m.expires_at,
        )
        for m in messages
    ]


@router.delete("/{message_id}", status_code=204)
async def delete_message(message_id: str, db: AsyncSession = Depends(get_session)):
    """Confirm receipt by deleting a message."""
    msg = await db.get(PendingMessage, message_id)
    if msg is None:
        raise HTTPException(status_code=404, detail="Message not found")
    await db.delete(msg)
    await db.commit()
