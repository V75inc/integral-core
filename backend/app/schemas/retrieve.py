"""POST /api/retrieve Pydantic boundary types — RET-03 wire shape.

Per CONTEXT lock #5: defaults K=150 (over-fetch), top_n=20 (returned after
permission filter). Per CONTEXT §'Locked: permission filter at retrieval
time': ZERO new PolicyAction members (``entry.read`` already covers the path).

Boundary discipline mirrors Phase 3 ``schemas/policy.py`` — every model
uses ``model_config = {'extra': 'forbid'}`` so a request body with unknown
fields fails 422 at the Pydantic boundary.

Wave 2 contract (locked for Plan 04-03):
  from app.schemas.retrieve import (
      RetrieveRequest, RetrieveResponse, RetrievedEntry, RetrieveFilters,
      RetrieveMode,
  )
"""

from __future__ import annotations

import os
from typing import List, Literal, Optional

from pydantic import BaseModel, Field

from app.schemas.provenance import Provenance

RetrieveMode = Literal["graph", "semantic", "hybrid"]


class RetrieveFilters(BaseModel):
    """Filter projection — applied AFTER the permission filter, BEFORE the top_n cap.

    These are PROJECTIONS, not access decisions. The authorization gate is
    always ``policy_engine.evaluate(action='entry.read', ...)`` per
    invariant I-RET-01.
    """

    entry_types: List[str] = Field(default_factory=list)
    tags: List[str] = Field(default_factory=list)
    model_config = {"extra": "forbid"}


class RetrieveRequest(BaseModel):
    """Request body for ``POST /api/retrieve``.

    ``scope`` shapes:
      * ``"track:<id>"`` — enables SQL pre-filter on Plan 04-01's
        ``track_id`` column (perf optimization; the per-candidate policy
        filter ALWAYS still applies).
      * ``"workspace:<id>"`` — must match the request's validated workspace;
        graph membership and current permissions filter every candidate.
      * ``None`` — defaults to the request's validated workspace. It never
        widens retrieval to all workspaces the principal can read.

    A track scope must also belong to that validated workspace. Vector-store
    pre-filtering is advisory; candidate membership is checked independently.

    ``mode`` dispatches to the three retrieval channels:
      * ``"graph"`` — existing ``get_user_accessible_entries`` traversal
        (already permission-gated; no parallel filter needed).
      * ``"semantic"`` — vector lookup + per-candidate policy filter.
      * ``"hybrid"`` — both channels + RRF combine, then the union is
        still permission-safe (graph is already gated; semantic
        permission-gates inline).

    ``k`` is the vector-store over-fetch budget (default 150 — CONTEXT
    lock #5); ``top_n`` is the returned-to-caller cap (default 20 —
    CONTEXT lock #5). Both surface via env vars
    ``RETRIEVE_K_DEFAULT`` / ``RETRIEVE_TOP_N_DEFAULT`` and may be
    overridden on a per-request basis.
    """

    query: str = Field(max_length=8192)
    scope: Optional[str] = None
    filters: Optional[RetrieveFilters] = None
    mode: RetrieveMode = "hybrid"
    k: int = Field(
        default_factory=lambda: min(
            500, max(1, int(os.getenv("RETRIEVE_K_DEFAULT", "150")))
        ),
        ge=0,
        le=500,
    )
    top_n: int = Field(
        default_factory=lambda: min(
            50, max(1, int(os.getenv("RETRIEVE_TOP_N_DEFAULT", "20")))
        ),
        ge=0,
        le=50,
    )
    context_tokens: int = Field(default=1200, ge=64, le=8192)

    model_config = {"extra": "forbid"}


class RetrievedPassage(BaseModel):
    field: str
    start: int
    end: int
    text: str
    tokens: int


class RetrievedEntry(BaseModel):
    """Single result row.

    ``mode_origin`` lists which channel(s) surfaced this entry — used by
    callers for debuggability ("Did the graph traversal find it, or did
    semantic search, or both?"). ``provenance`` carries the Phase 2
    Provenance shape so callers can distinguish human / agent / connector
    / system origin without a second round-trip.
    """

    entry_id: str
    track_id: str
    score: float
    mode_origin: List[Literal["graph", "semantic"]]
    provenance: Optional[Provenance] = None
    snippet: Optional[str] = None
    title: Optional[str] = None
    source_revision: Optional[int] = None
    profile_id: Optional[str] = None
    action_url: Optional[str] = None
    passages: List[RetrievedPassage] = Field(default_factory=list)
    model_config = {"extra": "forbid"}


class RetrieveResponse(BaseModel):
    """Response body for ``POST /api/retrieve``.

    ``results`` is sorted by ``score`` descending and capped at
    ``top_n``. ``dropped_for_permission`` counts how many vector-store
    candidates were silently dropped by the per-candidate policy filter —
    surfaced for caller debuggability per RESEARCH Pitfall 3 (the
    response itself NEVER leaks which entries were dropped; only the
    aggregate count is observable).

    ``candidates_examined`` is the total number of candidates the engine
    looked at across all active channels — useful for tuning ``k``.

    ``requested_mode`` echoes the mode the caller asked for; ``mode``
    is the channel actually used. When the deployment has no vector
    store available (Wave 2 of the SaaS-deployment plan) semantic /
    hybrid requests transparently degrade to ``mode=graph`` and the
    response carries ``degraded=true`` so the caller can render an
    "indexing not available" affordance without inspecting the mode
    field.
    """

    results: List[RetrievedEntry]
    dropped_for_permission: int = 0
    dropped_for_boundary: int = 0
    boundary: Optional[dict] = None
    mode: RetrieveMode
    requested_mode: RetrieveMode
    degraded: bool = False
    candidates_examined: int = 0
    index: Optional[dict] = None
    model_config = {"extra": "forbid"}


__all__ = [
    "RetrieveMode",
    "RetrieveFilters",
    "RetrieveRequest",
    "RetrievedEntry",
    "RetrieveResponse",
]
