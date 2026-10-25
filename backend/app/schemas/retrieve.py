"""Typed POST /api/retrieve boundary and bounded passage response.

Unknown request fields are refused. Deployment defaults supply k/top_n when
omitted; context_tokens bounds passage text using the embedding tokenizer, not
the full provider prompt. See docs/backend/semantic-indexing.md for scope,
backend-specific ranking, fallback and coverage semantics.
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
    """Request scoped to the host-validated workspace.

    scope accepts workspace:<id>, app:<id> or track:<id>; omission selects the
    active workspace. Narrower scopes must belong to it. Current membership,
    source and policy checks apply independently of vector prefilters.

    graph uses canonical keyword reads; PostgreSQL semantic selects cosine
    candidates from profile-isolated passages and hybrid adds lexical ranks.
    Bounded overfetch and diversity discounts keep repeated wording from
    occupying the evidence window; scores are ranks, not confidence.
    Other adapters retain their own channel semantics. k bounds candidates and
    top_n bounds returned records. Both honor deployment defaults; the passage
    text context_tokens budget excludes envelope, schemas and model history.
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
    context_only: bool = Field(
        default=False,
        description="Supporting text from current selected fields, not a ranked match.",
    )


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
