"""Request bodies for share-link mint and redeem endpoints."""

from __future__ import annotations

from typing import Any, Dict, Optional

from pydantic import BaseModel, Field


class MintShareLinkRequest(BaseModel):
    role: str = Field(default="viewer")
    expires_at: Optional[str] = None


class RedeemShareLinkRequest(BaseModel):
    token: str = Field(min_length=1)


class UpdatePublicTrackShareRequest(BaseModel):
    enabled: bool
    public_permissions: Dict[str, bool] = Field(default_factory=dict)


class PublicEntryCreateRequest(BaseModel):
    title: str
    type_id: str
    body: Optional[str] = ""
    custom_fields: Optional[Dict[str, Any]] = Field(default_factory=dict)


class PublicEntryUpdateRequest(BaseModel):
    title: Optional[str] = None
    body: Optional[str] = None
    custom_fields: Optional[Dict[str, Any]] = None
    status: Optional[str] = None


class PublicCommentCreateRequest(BaseModel):
    text: str


class PublicReactionCreateRequest(BaseModel):
    emoji: str
