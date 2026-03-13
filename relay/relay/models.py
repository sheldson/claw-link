"""SQLAlchemy ORM models for the relay database."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    Column,
    DateTime,
    LargeBinary,
    String,
    func,
)
from sqlalchemy.orm import DeclarativeBase


class Base(DeclarativeBase):
    pass


class Claw(Base):
    __tablename__ = "claws"

    id = Column(String, primary_key=True)           # claw_XXXXXXXX
    name = Column(String, nullable=False)
    public_key = Column(String, nullable=False)
    created_at = Column(DateTime, default=func.now(), nullable=False)
    last_seen = Column(DateTime, default=func.now(), nullable=False)


class FriendRequest(Base):
    __tablename__ = "friend_requests"

    id = Column(String, primary_key=True)
    from_id = Column(String, nullable=False, index=True)
    to_id = Column(String, nullable=False, index=True)
    status = Column(String, default="pending", nullable=False)  # pending / accepted / rejected
    created_at = Column(DateTime, default=func.now(), nullable=False)


class Friendship(Base):
    __tablename__ = "friendships"

    id = Column(String, primary_key=True)
    claw_a_id = Column(String, nullable=False, index=True)
    claw_b_id = Column(String, nullable=False, index=True)
    created_at = Column(DateTime, default=func.now(), nullable=False)


class PendingMessage(Base):
    __tablename__ = "pending_messages"

    id = Column(String, primary_key=True)
    from_id = Column(String, nullable=False, index=True)
    to_id = Column(String, nullable=False, index=True)
    encrypted_payload = Column(LargeBinary, nullable=False)
    created_at = Column(DateTime, default=func.now(), nullable=False)
    expires_at = Column(DateTime, nullable=False)
