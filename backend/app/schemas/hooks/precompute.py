"""Schemas for POST /api/entries/{id}/precompute (DR-30-02)."""

from typing import Any, Dict

from pydantic import BaseModel


class PrecomputeRequest(BaseModel):
    hook_key: str  # required — entry.precompute always disambiguates


class PrecomputeResponse(BaseModel):
    patch: Dict[str, Any]
