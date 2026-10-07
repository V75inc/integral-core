"""Schemas for per-user saved signatures."""

from __future__ import annotations

from typing import Optional

from pydantic import BaseModel, Field


class UserSignatureSaveRequest(BaseModel):
    signature_png: str = Field(..., description="PNG bytes or data URL")
    label: str = "Default"


class UserSignatureResponse(BaseModel):
    id: str
    filename: str = ""
    mime_type: str = "image/png"
    size: int = 0
    created_at: Optional[str] = None
    label: str = "Default"
