"""Friend request, accept, reject, list, and delete."""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select, or_, and_
from sqlalchemy.ext.asyncio import AsyncSession

from relay.database import get_session
from relay.models import FriendRequest, Friendship, Claw
from relay.schemas import (
    FriendRequestCreate,
    FriendRequestAction,
    FriendRequestInfo,
    FriendInfo,
)

router = APIRouter(prefix="/v1/friends", tags=["friends"])


async def _claw_exists(db: AsyncSession, claw_id: str) -> bool:
    return (await db.get(Claw, claw_id)) is not None


async def _are_friends(db: AsyncSession, a: str, b: str) -> bool:
    stmt = select(Friendship).where(
        or_(
            and_(Friendship.claw_a_id == a, Friendship.claw_b_id == b),
            and_(Friendship.claw_a_id == b, Friendship.claw_b_id == a),
        )
    )
    result = await db.execute(stmt)
    return result.scalar_one_or_none() is not None


@router.post("/request", response_model=FriendRequestInfo, status_code=201)
async def send_friend_request(
    body: FriendRequestCreate, db: AsyncSession = Depends(get_session)
):
    """Send a friend request from one claw to another."""
    if body.from_id == body.to_id:
        raise HTTPException(status_code=400, detail="Cannot send friend request to yourself")

    for lid in (body.from_id, body.to_id):
        if not await _claw_exists(db, lid):
            raise HTTPException(status_code=404, detail=f"Claw {lid} not found")

    if await _are_friends(db, body.from_id, body.to_id):
        raise HTTPException(status_code=409, detail="Already friends")

    # Check for existing pending request in either direction
    stmt = select(FriendRequest).where(
        FriendRequest.status == "pending",
        or_(
            and_(FriendRequest.from_id == body.from_id, FriendRequest.to_id == body.to_id),
            and_(FriendRequest.from_id == body.to_id, FriendRequest.to_id == body.from_id),
        ),
    )
    existing = (await db.execute(stmt)).scalar_one_or_none()
    if existing is not None:
        raise HTTPException(status_code=409, detail="Pending friend request already exists")

    req = FriendRequest(
        id=str(uuid.uuid4()),
        from_id=body.from_id,
        to_id=body.to_id,
        status="pending",
        created_at=datetime.now(timezone.utc),
    )
    db.add(req)
    await db.commit()
    await db.refresh(req)

    return FriendRequestInfo(
        request_id=req.id,
        from_id=req.from_id,
        to_id=req.to_id,
        status=req.status,
        created_at=req.created_at,
    )


@router.post("/accept", response_model=FriendInfo)
async def accept_friend_request(
    body: FriendRequestAction, db: AsyncSession = Depends(get_session)
):
    """Accept a pending friend request. Only the to_id claw can accept."""
    req = await db.get(FriendRequest, body.request_id)
    if req is None:
        raise HTTPException(status_code=404, detail="Friend request not found")
    if req.to_id != body.claw_id:
        raise HTTPException(status_code=403, detail="Only the recipient can accept a friend request")
    if req.status != "pending":
        raise HTTPException(status_code=409, detail=f"Request already {req.status}")

    req.status = "accepted"

    now = datetime.now(timezone.utc)
    friendship = Friendship(
        id=str(uuid.uuid4()),
        claw_a_id=req.from_id,
        claw_b_id=req.to_id,
        created_at=now,
    )
    db.add(friendship)
    await db.commit()

    friend = await db.get(Claw, req.from_id)
    return FriendInfo(
        claw_id=friend.id,
        name=friend.name,
        since=now,
    )


@router.post("/reject", response_model=FriendRequestInfo)
async def reject_friend_request(
    body: FriendRequestAction, db: AsyncSession = Depends(get_session)
):
    """Reject a pending friend request. Only the to_id claw can reject."""
    req = await db.get(FriendRequest, body.request_id)
    if req is None:
        raise HTTPException(status_code=404, detail="Friend request not found")
    if req.to_id != body.claw_id:
        raise HTTPException(status_code=403, detail="Only the recipient can reject a friend request")
    if req.status != "pending":
        raise HTTPException(status_code=409, detail=f"Request already {req.status}")

    req.status = "rejected"
    await db.commit()

    return FriendRequestInfo(
        request_id=req.id,
        from_id=req.from_id,
        to_id=req.to_id,
        status=req.status,
        created_at=req.created_at,
    )


@router.get("/{claw_id}", response_model=list[FriendInfo])
async def list_friends(claw_id: str, db: AsyncSession = Depends(get_session)):
    """List all friends of a claw."""
    if not await _claw_exists(db, claw_id):
        raise HTTPException(status_code=404, detail="Claw not found")

    stmt = select(Friendship).where(
        or_(
            Friendship.claw_a_id == claw_id,
            Friendship.claw_b_id == claw_id,
        )
    )
    result = await db.execute(stmt)
    friendships = result.scalars().all()

    friends: list[FriendInfo] = []
    for fs in friendships:
        friend_id = fs.claw_b_id if fs.claw_a_id == claw_id else fs.claw_a_id
        friend = await db.get(Claw, friend_id)
        if friend:
            friends.append(FriendInfo(
                claw_id=friend.id,
                name=friend.name,
                since=fs.created_at,
            ))
    return friends


@router.get("/{claw_id}/requests", response_model=list[FriendRequestInfo])
async def list_pending_requests(claw_id: str, db: AsyncSession = Depends(get_session)):
    """List pending friend requests addressed to a claw."""
    if not await _claw_exists(db, claw_id):
        raise HTTPException(status_code=404, detail="Claw not found")

    stmt = select(FriendRequest).where(
        FriendRequest.to_id == claw_id,
        FriendRequest.status == "pending",
    )
    result = await db.execute(stmt)
    reqs = result.scalars().all()

    return [
        FriendRequestInfo(
            request_id=r.id,
            from_id=r.from_id,
            to_id=r.to_id,
            status=r.status,
            created_at=r.created_at,
        )
        for r in reqs
    ]


@router.delete("/{claw_id}/{friend_id}", status_code=204)
async def delete_friend(
    claw_id: str, friend_id: str, db: AsyncSession = Depends(get_session)
):
    """Remove a friend (unfriend / block)."""
    stmt = select(Friendship).where(
        or_(
            and_(Friendship.claw_a_id == claw_id, Friendship.claw_b_id == friend_id),
            and_(Friendship.claw_a_id == friend_id, Friendship.claw_b_id == claw_id),
        )
    )
    result = await db.execute(stmt)
    friendship = result.scalar_one_or_none()
    if friendship is None:
        raise HTTPException(status_code=404, detail="Friendship not found")

    await db.delete(friendship)
    await db.commit()
