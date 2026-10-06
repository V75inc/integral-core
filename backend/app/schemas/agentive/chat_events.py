"""Public normalized-event replay contracts for durable native chat."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from app.schemas.agentive.work import WorkStatus


class ChatEventReplayPage(BaseModel):
    """Authorized durable events and cursor state for one native chat turn."""

    events: list[dict[str, Any]] = Field(default_factory=list)
    committed_through: int = Field(default=0, ge=0)
    next_after_sequence: int = Field(default=0, ge=0)
    has_more: bool = False
    gap: bool = False
    work_status: WorkStatus

    model_config = ConfigDict(extra="forbid", frozen=True)


class ChatTurnCancellationReceipt(BaseModel):
    """Safe result for cancelling an authenticated owner's native chat turn."""

    work_item_id: str
    status: WorkStatus
    cancel_requested: bool

    model_config = ConfigDict(extra="forbid", frozen=True)
