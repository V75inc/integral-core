"""Schemas for /api/entries/{id}/public-share and /api/public-share/{token}."""

from typing import Any, Dict, Optional

from pydantic import BaseModel


class MintPublicShareRequest(BaseModel):
    expires_at: Optional[str] = None
    hook_key: Optional[str] = None


class MintPublicShareResponse(BaseModel):
    token: str
    share_link_id: str
    hook_key: str


class PublicShareReadResponse(BaseModel):
    hook_key: str
    projection: Dict[str, Any]
    shared_at: str
