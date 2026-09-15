"""Schemas for POST /api/entries/{id}/transform (DR-30-02)."""

from typing import Optional

from pydantic import BaseModel


class TransformRequest(BaseModel):
    to_track: Optional[str] = None  # target track id (when match needs it)
    hook_key: Optional[str] = None  # disambiguate on multi-binding
    override: bool = False  # bypass override_flag gate


class TransformResponse(BaseModel):
    new_entry_id: str
    hook_key: str
