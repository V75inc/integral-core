"""Schemas for agentive/api/channels.py — channel identity CRUD bodies."""

from __future__ import annotations

from typing import Any, Dict, Optional

from pydantic import BaseModel, Field


class CreateChannelIdentityRequest(BaseModel):
    """Body for POST /channels/identities — link a new channel identity."""

    channel: str = ""
    channel_user_id: str = ""
    preferences: Dict[str, Any] = Field(default_factory=dict)

    model_config = {"extra": "forbid"}


class ResolveIdentityRequest(BaseModel):
    """Body for POST /channels/identities/resolve — resolve channel id → Integral user."""

    channel: str = ""
    channel_user_id: str = ""
    workspace_id: Optional[str] = None

    model_config = {"extra": "forbid"}


class WhatsAppVerifyInitiateRequest(BaseModel):
    """Body for POST /channels/whatsapp/verify-initiate."""

    phone: str = ""

    model_config = {"extra": "forbid"}


class WhatsAppVerifyOtpRequest(BaseModel):
    """Body for POST /channels/whatsapp/verify-otp."""

    phone: str = ""
    otp_code: str = ""

    model_config = {"extra": "forbid"}


class VerifyLinkTokenRequest(BaseModel):
    """Body for POST /channels/identities/{id}/verify-link-token."""

    token: str = ""

    model_config = {"extra": "forbid"}
