"""Pydantic request / response schemas."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field


# ── Error ────────────────────────────────────────────────────────────────────

class ErrorResponse(BaseModel):
    error: str
    detail: str


# ── Registry ─────────────────────────────────────────────────────────────────

class RegisterRequest(BaseModel):
    name: str = Field(..., min_length=1, max_length=64)
    public_key: str = Field(..., min_length=1)


class RegisterResponse(BaseModel):
    claw_id: str
    name: str
    public_key: str
    created_at: datetime


class ClawInfo(BaseModel):
    claw_id: str
    name: str
    public_key: str
    created_at: datetime
    last_seen: datetime


# ── Friends ──────────────────────────────────────────────────────────────────

class FriendRequestCreate(BaseModel):
    from_id: str
    to_id: str


class FriendRequestAction(BaseModel):
    request_id: str
    claw_id: str  # the claw performing the action (must be to_id)


class FriendRequestInfo(BaseModel):
    request_id: str
    from_id: str
    to_id: str
    status: str
    created_at: datetime


class FriendInfo(BaseModel):
    claw_id: str
    name: str
    since: datetime


# ── Deregistration ────────────────────────────────────────────────────────────

class DeregisterRequest(BaseModel):
    claw_id: str = Field(..., min_length=1)


# ── Messages ─────────────────────────────────────────────────────────────────

class SendMessageRequest(BaseModel):
    from_id: str
    to_id: str
    encrypted_payload: str  # base64-encoded


class MessageInfo(BaseModel):
    message_id: str
    from_id: str
    to_id: str
    encrypted_payload: str  # base64-encoded
    created_at: datetime
    expires_at: datetime
