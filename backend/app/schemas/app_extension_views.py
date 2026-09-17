"""Schemas for app extension view host (ADR-011 WP-03)."""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field


class ExtensionViewDescriptor(BaseModel):
    key: str
    name: str
    description: str = ""
    entry: str
    scope: str = "track"


class ExtensionViewsListResponse(BaseModel):
    app_id: str
    package_version: Optional[str] = None
    views: List[ExtensionViewDescriptor]
    handshake_token: str
    protocol: str = "integral.extension.v1"


class ExtensionViewHandshakeResponse(BaseModel):
    app_id: str
    view_key: str
    package_version: Optional[str] = None
    handshake_token: str
    protocol: str = "integral.extension.v1"
    theme: Dict[str, Any] = Field(default_factory=dict)
