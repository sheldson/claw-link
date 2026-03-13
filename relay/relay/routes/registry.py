"""Claw registration and lookup."""

from __future__ import annotations

import json
import string
import secrets
import uuid
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select, or_
from sqlalchemy.ext.asyncio import AsyncSession

from relay.config import settings
from relay.database import get_session
from relay.models import FriendRequest, Friendship, Claw, PendingMessage
from relay.schemas import RegisterRequest, RegisterResponse, ClawInfo

router = APIRouter(prefix="/v1", tags=["registry"])

ALPHABET = string.ascii_lowercase + string.digits


def _generate_claw_id() -> str:
    suffix = "".join(secrets.choice(ALPHABET) for _ in range(8))
    return f"claw_{suffix}"


@router.post("/register", response_model=RegisterResponse, status_code=201)
async def register(body: RegisterRequest, db: AsyncSession = Depends(get_session)):
    """Register a new claw. Returns the assigned claw_id."""
    claw_id = _generate_claw_id()

    # Ensure no collision (extremely unlikely but let's be safe)
    while (await db.get(Claw, claw_id)) is not None:
        claw_id = _generate_claw_id()

    now = datetime.now(timezone.utc)
    claw = Claw(
        id=claw_id,
        name=body.name,
        public_key=body.public_key,
        created_at=now,
        last_seen=now,
    )
    db.add(claw)
    await db.commit()
    await db.refresh(claw)

    return RegisterResponse(
        claw_id=claw.id,
        name=claw.name,
        public_key=claw.public_key,
        created_at=claw.created_at,
    )


@router.get("/claws/{claw_id}", response_model=ClawInfo)
async def get_claw(claw_id: str, db: AsyncSession = Depends(get_session)):
    """Get public info for a claw."""
    claw = await db.get(Claw, claw_id)
    if claw is None:
        raise HTTPException(status_code=404, detail="Claw not found")

    return ClawInfo(
        claw_id=claw.id,
        name=claw.name,
        public_key=claw.public_key,
        created_at=claw.created_at,
        last_seen=claw.last_seen,
    )


@router.delete("/claws/{claw_id}", status_code=204)
async def deregister(claw_id: str, db: AsyncSession = Depends(get_session)):
    """Permanently deregister a claw.

    Steps:
    1. Find all friends and send each a goodbye notification message.
    2. Delete all friendships involving this claw.
    3. Delete all friend requests involving this claw.
    4. Delete all pending messages involving this claw.
    5. Delete the claw record.
    """
    claw = await db.get(Claw, claw_id)
    if claw is None:
        raise HTTPException(status_code=404, detail="Claw not found")

    claw_name = claw.name

    # Find all friends
    stmt = select(Friendship).where(
        or_(
            Friendship.claw_a_id == claw_id,
            Friendship.claw_b_id == claw_id,
        )
    )
    result = await db.execute(stmt)
    friendships = result.scalars().all()

    # Send goodbye message to each friend
    now = datetime.now(timezone.utc)
    expires = now + timedelta(days=settings.message_expire_days)
    goodbye_payload = json.dumps({
        "type": "goodbye",
        "claw_id": claw_id,
        "name": claw_name,
    }, ensure_ascii=False)
    goodbye_bytes = goodbye_payload.encode("utf-8")

    for fs in friendships:
        friend_id = fs.claw_b_id if fs.claw_a_id == claw_id else fs.claw_a_id
        msg = PendingMessage(
            id=str(uuid.uuid4()),
            from_id=claw_id,
            to_id=friend_id,
            encrypted_payload=goodbye_bytes,
            created_at=now,
            expires_at=expires,
        )
        db.add(msg)

    # Delete all friendships
    for fs in friendships:
        await db.delete(fs)

    # Delete all friend requests involving this claw
    stmt_req = select(FriendRequest).where(
        or_(
            FriendRequest.from_id == claw_id,
            FriendRequest.to_id == claw_id,
        )
    )
    result_req = await db.execute(stmt_req)
    for req in result_req.scalars().all():
        await db.delete(req)

    # Delete all pending messages sent by this claw (not goodbye ones just created)
    stmt_msg = select(PendingMessage).where(
        PendingMessage.from_id == claw_id,
        PendingMessage.created_at < now,
    )
    result_msg = await db.execute(stmt_msg)
    for m in result_msg.scalars().all():
        await db.delete(m)

    # Delete the claw itself
    await db.delete(claw)
    await db.commit()
