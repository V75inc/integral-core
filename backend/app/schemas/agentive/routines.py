"""Schemas for agentive/api/routines.py — RoutineTask management REST surface.

Thin wire boundary over ``app.agentive.services.routine_tasks``. ``write_scope``
is readable on responses but intentionally omitted from ``RoutineUpdateRequest``
so the management UI cannot widen the grant without going through staging bless.
"""

from __future__ import annotations

from typing import Dict, List, Literal, Optional

from pydantic import BaseModel, Field

RoutineUpdateStatus = Literal["active", "paused"]


class RoutineResponse(BaseModel):
    """Wire shape for a single RoutineTask row."""

    id: str
    instruction: str
    cron: str
    timezone: str
    status: str
    write_scope: List[Dict[str, str]] = Field(default_factory=list)
    next_run_at: Optional[str] = None
    last_run_at: Optional[str] = None
    last_run_status: Optional[str] = None
    last_run_error: Optional[str] = None
    consecutive_failures: int = 0
    max_runs: Optional[int] = None
    run_count: int = 0
    thread_id: str = ""
    workspace_id: str = ""
    created_at: Optional[str] = None
    updated_at: Optional[str] = None
    running: bool = False

    model_config = {"extra": "forbid"}


class RoutineListResponse(BaseModel):
    """List shape for GET /agentive/routines."""

    routines: List[RoutineResponse]
    total: int

    model_config = {"extra": "forbid"}


class RoutineUpdateRequest(BaseModel):
    """Body for PATCH /agentive/routines/{id}.

    ``write_scope`` is intentionally absent — display-only on the wire so
    widening cannot bypass staging bless.
    """

    status: Optional[RoutineUpdateStatus] = None
    instruction: Optional[str] = None
    cron: Optional[str] = None
    timezone: Optional[str] = None
    max_runs: Optional[int] = None
    clear_max_runs: bool = False

    model_config = {"extra": "forbid"}


class RoutineCancelResponse(BaseModel):
    """Wire shape for POST /agentive/routines/{id}/cancel."""

    id: str
    status: Literal["cancelled"] = "cancelled"

    model_config = {"extra": "forbid"}


class RoutineDeleteResponse(BaseModel):
    """Wire shape for DELETE /agentive/routines/{id}."""

    id: str
    deleted: Literal[True] = True

    model_config = {"extra": "forbid"}


class RoutineActivityLink(BaseModel):
    """Navigable resource mentioned by a routine run."""

    kind: str  # track | entry | app | other
    id: str
    label: str
    href: str

    model_config = {"extra": "forbid"}


class RoutineActivityEvent(BaseModel):
    """A scheduler change-event or chat turn attributed to this routine."""

    id: str
    kind: str  # run_event | message
    ts: Optional[str] = None
    action: Optional[str] = None
    status: Optional[str] = None
    summary: str
    reason: Optional[str] = None
    message_id: Optional[str] = None
    role: Optional[str] = None
    links: List[RoutineActivityLink] = Field(default_factory=list)

    model_config = {"extra": "forbid"}


class RoutineActivityResponse(BaseModel):
    """Activity feed for GET /agentive/routines/{id}/activity."""

    routine_id: str
    thread_id: str
    events: List[RoutineActivityEvent]
    write_scope_links: List[RoutineActivityLink] = Field(default_factory=list)

    model_config = {"extra": "forbid"}
