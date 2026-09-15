"""POST /api/retrieve — unified hybrid retrieval endpoint (RET-03).

Per CONTEXT §'Locked: permission filter at retrieval time': every result
returned from semantic / hybrid mode is permission-checked via
``policy_engine.evaluate(action='entry.read')`` BEFORE inclusion. Graph
mode reuses the existing ``services/permissions.py`` traversal which is
already permission-gated (POL-03 parity).

Per CONTEXT lock #5: ``K=150`` over-fetch + ``top_n=20`` return defaults.
Both surface via env vars (``RETRIEVE_K_DEFAULT`` /
``RETRIEVE_TOP_N_DEFAULT``) and accept per-request body overrides.

Per CONTEXT lock #6: ``scope='track:<id>'`` enables an SQL pre-filter on
Plan 04-01's ``track_id`` column on ``entry_embedding_meta``. The
pre-filter is a PERF optimization; the per-candidate policy filter is
ALWAYS applied (invariant I-RET-01 — no-index-bypass).

Per CONTEXT lock #12: ZERO new PolicyAction members — ``entry.read``
already covers every retrieval path.

This endpoint is auto-discovered by Phase 3's
``mcp_adapter.build_tool_catalogue`` walk and surfaces in
``GET /api/mcp/tools`` as ``integral_create_retrieve`` by default. Plan
04-03 will register the ``integral_query`` tool-name override.
"""

from __future__ import annotations

import asyncio
import logging
import os
from typing import Any, Dict, List, Literal, Optional, Tuple

from fastapi import Request
from fastapi.responses import JSONResponse
from jvspatial.api import endpoint
from pydantic import ValidationError

from app.api.errors import (
    BadRequestError,
    MissingAuthenticationError,
)
from app.api.utils import resolve_principal_id
from app.models.nodes import Entry
from app.schemas.policy import Resource, Subject
from app.schemas.retrieve import (
    RetrievedEntry,
    RetrieveFilters,
    RetrieveMode,
    RetrieveRequest,
    RetrieveResponse,
)
from app.services.policy_engine import evaluate as policy_evaluate
from app.services.retrieval import (
    disable_semantic_retrieval_runtime,
    get_embedding_store,
    semantic_retrieval_available,
)
from app.services.retrieval.embedding_model import embed_query_text
from app.services.retrieval.keyword_match import (
    keyword_overlap,
    matches_keywords,
    tokenize,
)
from app.services.retrieval.rank_fusion import reciprocal_rank_fusion

logger = logging.getLogger(__name__)

# One-shot WARNING when fallback dispatch fires for the first time in this
# process — operators see the degraded state without log spam.
_DEGRADED_WARNED = False


def _warn_degraded_once(requested: str) -> None:
    """Emit the degrade WARNING once per process (avoids per-request log spam)."""
    global _DEGRADED_WARNED
    if _DEGRADED_WARNED:
        return
    logger.warning(
        "retrieval: semantic store unavailable; degrading mode=%s to mode=graph "
        "for the remainder of this process. Future fallback events are not logged.",
        requested,
    )
    _DEGRADED_WARNED = True


def _default_k() -> int:
    """Read default ``k`` from env at call time (NOT module import time).

    Per-call read so tests can monkeypatch ``os.environ`` without having
    to re-import the module.
    """
    return int(os.environ.get("RETRIEVE_K_DEFAULT", "150"))


def _default_top_n() -> int:
    """Read default ``top_n`` from env at call time. See ``_default_k``."""
    return int(os.environ.get("RETRIEVE_TOP_N_DEFAULT", "20"))


# ---------------------------------------------------------------------------
# Endpoint
# ---------------------------------------------------------------------------


@endpoint("/retrieve", methods=["POST"], auth=True, tags=["Retrieve"])
async def retrieve(request: Request) -> Any:
    """Hybrid retrieval — graph / semantic / hybrid mode dispatch.

    Permission filter runs at retrieval time (NOT in the index). Denied
    candidates are silently dropped per CONTEXT (no leakage of
    "X exists but you can't see it"; only the aggregate
    ``dropped_for_permission`` count is observable).
    """
    user_id = resolve_principal_id(request)
    if not user_id:
        raise MissingAuthenticationError(message="Authentication required")

    # Parse + validate body via Pydantic boundary directly so the
    # ``extra='forbid'`` model_config is enforced on the request body
    # ROOT (jvspatial's @endpoint parameter-model wrapper would otherwise
    # bury our schema as a nested field and the extra-forbid gate would
    # not apply at the top level).
    try:
        raw_body = await request.json()
    except Exception as exc:  # noqa: BLE001 — malformed JSON → 400
        raise BadRequestError(message=f"Invalid JSON body: {exc}")
    if not isinstance(raw_body, dict):
        raise BadRequestError(message="Request body must be a JSON object")
    try:
        payload = RetrieveRequest.model_validate(raw_body)
    except ValidationError as exc:
        # Return 422 directly. We cannot raise RequestValidationError because
        # the agentive-installed handler (main.py L492 / install_agentive_error_handlers)
        # only handles /api/agentive/* paths and re-raises elsewhere — FastAPI
        # only dispatches to one handler per exception type, so the re-raise
        # bubbles out as a 500 instead of reaching the core handler.
        # Emit the canonical 5-key envelope inline (mirrors main.py L255).
        return JSONResponse(
            status_code=422,
            content={
                "error_code": "VALIDATION_ERROR",
                "message": "Request validation failed",
                "details": exc.errors(include_url=False, include_context=False),
            },
        )

    # Reuse the cached actor_kind set by the auth middleware when present
    # (service-auth agent paths flow through here too). Default to
    # "human" for the JWT path which is the only path currently exercised
    # by the user-facing client.
    actor_kind = getattr(request.state, "actor_kind", None) or "human"
    if actor_kind not in ("human", "agent", "connector", "system"):
        actor_kind = "human"
    subject = Subject(kind=actor_kind, id=user_id)  # type: ignore[arg-type]

    k = payload.k if payload.k and payload.k > 0 else _default_k()
    top_n = payload.top_n if payload.top_n and payload.top_n > 0 else _default_top_n()

    # Graceful fallback (Wave 2 of the SaaS-deployment plan): when the
    # deployment has no real vector store (the ``null`` driver is
    # active — typical for graph-only / non-Mongo dev), degrade
    # ``semantic`` / ``hybrid`` to ``graph`` so the request still
    # returns something useful. The per-candidate policy filter is
    # untouched (I-RET-01) — graph mode already enforces permissions
    # via ``get_user_accessible_entries``.
    requested_mode = payload.mode
    effective_mode = requested_mode
    degraded = False
    if requested_mode in ("semantic", "hybrid") and not semantic_retrieval_available():
        _warn_degraded_once(requested_mode)
        effective_mode = "graph"
        degraded = True

    (
        results,
        dropped,
        examined,
        effective_mode,
        degraded,
    ) = await _dispatch_retrieve_modes(
        subject,
        payload,
        requested_mode=requested_mode,
        effective_mode=effective_mode,
        degraded=degraded,
        k=k,
        top_n=top_n,
    )

    response = RetrieveResponse(
        results=results,
        dropped_for_permission=dropped,
        mode=effective_mode,
        requested_mode=requested_mode,
        degraded=degraded,
        candidates_examined=examined,
    )
    return response.model_dump(mode="json")


# ---------------------------------------------------------------------------
# Mode dispatch + runtime degrade
# ---------------------------------------------------------------------------


def _is_vector_store_unavailable(exc: BaseException) -> bool:
    """True when Mongo lacks Atlas Search / Vector Search (local dev Mongo)."""

    try:
        from pymongo.errors import OperationFailure
    except ImportError:
        return False
    if not isinstance(exc, OperationFailure):
        return False
    code = getattr(exc, "code", None)
    if code == 31082:  # SearchNotEnabled
        return True
    msg = str(exc).lower()
    return "vectorsearch" in msg or "$search" in msg or "searchnotenabled" in msg


async def _dispatch_retrieve_modes(
    subject: Subject,
    payload: RetrieveRequest,
    *,
    requested_mode: RetrieveMode,
    effective_mode: RetrieveMode,
    degraded: bool,
    k: int,
    top_n: int,
) -> Tuple[List[RetrievedEntry], int, int, RetrieveMode, bool]:
    """Run the requested retrieval channel with runtime vector-search fallback."""

    try:
        if effective_mode == "graph":
            results, dropped, examined = await _retrieve_graph(
                subject, payload, k=k, top_n=top_n
            )
        elif effective_mode == "semantic":
            results, dropped, examined = await _retrieve_semantic(
                subject, payload, k=k, top_n=top_n
            )
        else:  # hybrid
            results, dropped, examined = await _retrieve_hybrid(
                subject, payload, k=k, top_n=top_n
            )
        return (results, dropped, examined, effective_mode, degraded)
    except Exception as exc:
        if requested_mode not in (
            "semantic",
            "hybrid",
        ) or not _is_vector_store_unavailable(exc):
            raise
        disable_semantic_retrieval_runtime(str(exc)[:240])
        _warn_degraded_once(requested_mode)
        results, dropped, examined = await _retrieve_graph(
            subject, payload, k=k, top_n=top_n
        )
        return (results, dropped, examined, "graph", True)


# ---------------------------------------------------------------------------
# Mode handlers
# ---------------------------------------------------------------------------


async def _retrieve_graph(
    subject: Subject,
    payload: RetrieveRequest,
    *,
    k: int,
    top_n: int,
) -> Tuple[List[RetrievedEntry], int, int]:
    """Graph-only retrieval via paginated DB listing (permission-gated).

    Uses ``fetch_accessible_entries_page`` so examined count scales with
    ``max(k, top_n)`` rather than the full workspace entry set.
    """
    from app.services.entry_listing import fetch_accessible_entries_page

    track_id = _scope_to_track_id(payload.scope)
    workspace_id = _scope_to_workspace_id(payload.scope)
    fetch_limit = max(k, top_n)

    entries, _ = await fetch_accessible_entries_page(
        subject.id,
        track_id=track_id,
        workspace_id=workspace_id,
        q=payload.query,
        limit=fetch_limit,
        include_total=False,
    )
    examined = len(entries)
    entries = _apply_filters(entries, payload.filters)
    terms = tokenize(payload.query or "")
    if terms:
        matched = [e for e in entries if matches_keywords(_entry_search_text(e), terms)]
        entries = sorted(
            matched,
            key=lambda e: keyword_overlap(_entry_search_text(e), terms),
            reverse=True,
        )
    trimmed = entries[:top_n]
    results: List[RetrievedEntry] = []
    for i, entry in enumerate(trimmed):
        snippet = (getattr(entry, "title", None) or "")[:200] or None
        results.append(
            RetrievedEntry(
                entry_id=entry.id,
                track_id=getattr(entry, "track_id", "") or "",
                score=1.0 / (i + 1),
                mode_origin=["graph"],
                provenance=getattr(entry, "provenance", None),
                snippet=snippet,
            )
        )
    return (results, 0, examined)


async def _retrieve_semantic(
    subject: Subject,
    payload: RetrieveRequest,
    *,
    k: int,
    top_n: int,
) -> Tuple[List[RetrievedEntry], int, int]:
    """Vector lookup + per-candidate policy filter.

    CRITICAL — every candidate from ``embedding_store.search`` MUST be
    permission-checked via ``policy_engine.evaluate(action='entry.read',
    ...)`` before being returned. Invariant I-RET-01 (
    permission-filter-at-retrieval) and I-RET-02 (no-index-bypass).
    """
    query_vec = await embed_query_text(payload.query)
    store = get_embedding_store()
    # CONTEXT lock #6 — the scope param is forwarded verbatim. The
    # store applies the SQL pre-filter for ``"track:<id>"`` and skips
    # it for ``"workspace:<id>"`` / ``None``. The per-candidate policy
    # filter ALWAYS still runs — the pre-filter is purely a perf
    # optimization.
    candidates = await store.search(query_vec, k=k, scope=payload.scope)
    examined = len(candidates)

    seen_entry_ids: set[str] = set()
    unique_candidates: List[Tuple[str, float]] = []
    for cand_id, score in candidates:
        if cand_id in seen_entry_ids:
            continue
        seen_entry_ids.add(cand_id)
        unique_candidates.append((cand_id, score))

    async def _evaluate_candidate(
        cand_id: str, score: float
    ) -> Optional[Tuple[str, float, Any]]:
        entry = await Entry.get(cand_id)
        if entry is None:
            return None
        entry_status = getattr(entry, "status", None)
        if entry_status in ("deleted", "archived"):
            return None
        track_id = getattr(entry, "track_id", "") or ""
        decision = await policy_evaluate(
            subject=subject,
            action="entry.read",
            resource=Resource(
                kind="entry",
                id=cand_id,
                scope=f"track:{track_id}",
            ),
        )
        if not decision.allowed:
            return ("dropped", score, None)
        if not _entry_matches_filters(entry, payload.filters):
            return None
        return (cand_id, float(score), entry)

    eval_results = await asyncio.gather(
        *(_evaluate_candidate(cand_id, score) for cand_id, score in unique_candidates)
    )

    survivors: List[Tuple[str, float, Any]] = []
    dropped = 0
    for result in eval_results:
        if result is None:
            continue
        if result[0] == "dropped":
            dropped += 1
            continue
        survivors.append(result)

    trimmed = survivors[:top_n]
    results: List[RetrievedEntry] = []
    for cand_id, score, entry in trimmed:
        track_id = getattr(entry, "track_id", "") or ""
        snippet = (getattr(entry, "title", None) or "")[:200] or None
        results.append(
            RetrievedEntry(
                entry_id=cand_id,
                track_id=track_id,
                score=score,
                mode_origin=["semantic"],
                provenance=getattr(entry, "provenance", None),
                snippet=snippet,
            )
        )
    return (results, dropped, examined)


async def _retrieve_hybrid(
    subject: Subject,
    payload: RetrieveRequest,
    *,
    k: int,
    top_n: int,
) -> Tuple[List[RetrievedEntry], int, int]:
    """Run graph + semantic, fuse via RRF, then return the union (de-duped).

    Permission safety: graph is already gated, semantic gates inline,
    so the union is also permission-safe. RRF combines ranks without
    per-channel score normalization (parameter-free; see
    ``rank_fusion.reciprocal_rank_fusion``).
    """
    # Over-fetch each channel at the caller's ``k`` so RRF has a wider
    # candidate pool than the eventual ``top_n`` cap.
    graph_results, _graph_dropped, graph_examined = await _retrieve_graph(
        subject, payload, k=k, top_n=k
    )
    sem_results, sem_dropped, sem_examined = await _retrieve_semantic(
        subject, payload, k=k, top_n=k
    )

    # Build per-channel rankings for RRF. Score is IGNORED by the RRF
    # helper (rank position only); the (id, score) shape is kept so
    # callers can pass raw search output without re-shaping.
    graph_ranking = [(r.entry_id, r.score) for r in graph_results]
    sem_ranking = [(r.entry_id, r.score) for r in sem_results]
    fused = reciprocal_rank_fusion([graph_ranking, sem_ranking], k=60, top_n=top_n)

    graph_ids = {r.entry_id for r in graph_results}
    sem_ids = {r.entry_id for r in sem_results}
    # Prefer the graph result for hydration when an entry surfaced in
    # both channels (its provenance/snippet are populated identically,
    # but the graph path doesn't depend on the embedding model's
    # text-composition cap).
    by_id: Dict[str, RetrievedEntry] = {}
    for r in sem_results:
        by_id[r.entry_id] = r
    for r in graph_results:
        by_id[r.entry_id] = r

    results: List[RetrievedEntry] = []
    for entry_id, rrf_score in fused:
        base = by_id.get(entry_id)
        if base is None:
            continue
        modes: List[Literal["graph", "semantic"]] = []
        if entry_id in graph_ids:
            modes.append("graph")
        if entry_id in sem_ids:
            modes.append("semantic")
        results.append(
            RetrievedEntry(
                entry_id=entry_id,
                track_id=base.track_id,
                score=rrf_score,
                mode_origin=modes,
                provenance=base.provenance,
                snippet=base.snippet,
            )
        )
    # ``dropped_for_permission`` carries the semantic-channel drop count.
    # Graph mode reports zero by contract (already gated upstream).
    return (results, sem_dropped, graph_examined + sem_examined)


# ---------------------------------------------------------------------------
# Helper utilities
# ---------------------------------------------------------------------------


def _entry_search_text(entry: Any) -> str:
    """Concatenate the graph-mode searchable surface for an entry.

    Title + body + entry-type token. Used by both the keyword match gate
    and the overlap-based ranking so they stay in sync.
    """
    title = getattr(entry, "title", None) or ""
    body = getattr(entry, "body", None) or ""
    entry_type = (
        getattr(entry, "type", None) or getattr(entry, "entry_type_key", None) or ""
    )
    return f"{title} {body} {entry_type}"


def _entry_matches_query(entry: Any, query: str) -> bool:
    """Keyword-term match on title/body/type (graph-mode degraded search).

    Tokenizes the query into significant terms (stopwords / boolean noise /
    short tokens dropped) and matches the entry when ANY term appears in its
    title/body/type. An empty / all-stopword query yields no terms → match
    all (preserves the legacy "empty query → every entry" behaviour). This
    lets a natural-language or ``"A OR B OR C"`` query keyword-match even
    when semantic search is OFF, instead of literal-substring matching the
    whole string (which matched nothing).
    """
    terms = tokenize(query)
    if not terms:
        return True
    return matches_keywords(_entry_search_text(entry), terms, require="any")


def _scope_to_track_id(scope: Optional[str]) -> Optional[str]:
    """Extract the track id from a ``"track:<id>"`` scope string.

    Returns ``None`` for ``None`` / ``"workspace:<id>"`` / any other
    shape. Used by graph mode to narrow the traversal and by semantic
    mode's helper layer (the embedding store reads the raw scope string
    so it can choose to apply or skip the SQL pre-filter).
    """
    if scope and scope.startswith("track:"):
        return scope.split(":", 1)[1]
    return None


def _scope_to_workspace_id(scope: Optional[str]) -> Optional[str]:
    """Extract workspace id from ``"workspace:<id>"`` scope string."""
    if scope and scope.startswith("workspace:"):
        return scope.split(":", 1)[1]
    return None


def _entry_matches_filters(entry: Any, filters: Optional[RetrieveFilters]) -> bool:
    """Apply ``RetrieveFilters.entry_types`` + ``.tags`` to a single Entry.

    Filters are PROJECTIONS, not access decisions — they run AFTER the
    permission filter. A filter miss does NOT increment
    ``dropped_for_permission``.
    """
    if filters is None:
        return True
    if filters.entry_types:
        type_id = getattr(entry, "type_id", None) or ""
        entry_type_key = getattr(entry, "entry_type_key", None)
        if (
            type_id not in filters.entry_types
            and entry_type_key not in filters.entry_types
        ):
            return False
    if filters.tags:
        tagset = set(filters.tags)
        if not tagset.intersection(set(getattr(entry, "tags", None) or [])):
            return False
    return True


def _apply_filters(entries: List[Any], filters: Optional[RetrieveFilters]) -> List[Any]:
    """Apply ``RetrieveFilters`` to a pre-filtered candidate list (graph mode)."""
    if filters is None:
        return entries
    return [e for e in entries if _entry_matches_filters(e, filters)]


__all__ = [
    "retrieve",
    "_dispatch_retrieve_modes",
    "_default_k",
    "_default_top_n",
    "_retrieve_graph",
    "_retrieve_semantic",
    "_retrieve_hybrid",
    "_warn_degraded_once",
    "_entry_matches_query",
    "_entry_search_text",
]
