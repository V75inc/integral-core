"""Schemas for POST /api/tools/{key} (DR-30-01)."""

from typing import Any, Dict

from pydantic import BaseModel, Field


class ToolCallRequest(BaseModel):
    input: Dict[str, Any] = Field(default_factory=dict)


class ToolCallResponse(BaseModel):
    output: Dict[str, Any]
    tool_key: str
