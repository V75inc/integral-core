"""Schemas for agentive/api/chat.py — typed chat-turn boundary (AGT-03)."""

from __future__ import annotations

from typing import List, Optional

from pydantic import BaseModel, Field

from app.agentive.types import AgentType
from app.schemas.chat_entity_refs import EntityRef


class ChatTurnRequest(BaseModel):
    """Authenticated user chat turn.

    ``email`` is NEVER on this body — it comes from request.state.user.
    Client-supplied user identifiers are ignored.
    """

    message: str = Field(..., min_length=1, max_length=8000)
    session_id: Optional[str] = Field(default=None, max_length=128)
    focused_track_id: Optional[str] = None
    focused_space_id: Optional[str] = None
    entity_refs: Optional[List[EntityRef]] = None

    model_config = {"extra": "forbid"}


class ChatTurnResponse(BaseModel):
    """Vendor-neutral chat-turn response.

    Same shape on success and connector-error. Connector errors render as
    ``ok=False`` with HTTP 200 — gateway 4xx/5xx only for auth/gating/dispatch.
    ``agent_type`` echoes which connector handled the turn (D-09 observability).
    """

    ok: bool
    message: str = ""
    session_id: str = ""
    agent_user_id: str = ""
    agent_type: AgentType  # D-09 Literal — observability for the host
    error: Optional[str] = None
