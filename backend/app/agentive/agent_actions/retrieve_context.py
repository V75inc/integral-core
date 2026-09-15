"""``retrieve_context`` — RET-04 agent action wrapping ``POST /api/retrieve``.

Per Plan 04-02 downstream_handoff: this wrapper uses DIRECT service-function
invocation (NOT an in-process HTTP client) — it imports the mode-dispatch
handlers from ``app.api.retrieve`` and calls them with a synthesized
``RetrieveRequest`` + ``Subject(kind='agent', id=<AgentConfig.id>)``.

The direct-invocation discipline matters for two reasons:

  1. **No double JWT cost.** The agent already authenticated at the agent
     loop entry; routing a request back through the HTTP boundary would
     issue another JWT verification + ServiceAuth header walk.
  2. **Subject flow.** The per-candidate permission filter inside
     ``_retrieve_semantic`` evaluates against the Subject we pass in. By
     constructing ``Subject(kind='agent', id=agent_id)`` here, the
     ``policy_engine.evaluate(action='entry.read', ...)`` calls inside the
     retrieve handler run under the AGENT's Policy graph (Phase 3 POL-02
     narrower-than-human contract) — NOT the human user's Policy graph.
     This is the explicit MEM-02 contract: agents read under their own
     policy, NOT under the user's policy.

Per CONTEXT lock #11: this module lives inside the AGENTIVE_ENABLED
conditional load path (``backend/app/agentive/``) — harness-tier. The
core override map (``MCP_TOOL_NAME_OVERRIDES``) lives in
``backend/app/agentive/tooling/name_overrides.py`` which is always loaded; the
``mcp_adapter_legacy`` shim has been deleted — nothing imported it.
MCP-01.

Per CONTEXT lock #12: ZERO new ``PolicyAction`` / ``ChangeEventAction`` /
``ActorKind`` members. The ``"entry.read"`` PolicyAction (Phase 3
``schemas/policy.py:124``) covers every retrieval path; the ``"agent"``
ActorKind (single source of truth in ``schemas/provenance.py``) is reused.

The action signature mirrors ``RetrieveRequest`` exactly per RESEARCH §Q6
("mirror the REST shape for the MCP-First-Surface invariant"). No
flattening; no agent-specific field reshape — the agent sees the same
contract as any other MCP client.
"""

from __future__ import annotations

import logging
from typing import Literal, Optional

from app.api.retrieve import (
    _default_k,
    _default_top_n,
    _dispatch_retrieve_modes,
    _warn_degraded_once,
)
from app.schemas.policy import Subject
from app.schemas.retrieve import (
    RetrieveFilters,
    RetrieveRequest,
    RetrieveResponse,
)
from app.services.retrieval import semantic_retrieval_available

logger = logging.getLogger(__name__)


async def retrieve_context(
    *,
    agent_id: str,
    query: str,
    scope: Optional[str] = None,
    filters: Optional[RetrieveFilters] = None,
    mode: Literal["graph", "semantic", "hybrid"] = "hybrid",
    k: Optional[int] = None,
    top_n: Optional[int] = None,
) -> RetrieveResponse:
    """Agent-invoked retrieval — wraps ``POST /api/retrieve``.

    Mirrors the ``RetrieveRequest`` body exactly. Permission filter runs at
    retrieval time inside the mode handlers against the AGENT's policy
    (Subject.kind='agent') — narrower than human callers per Phase 3
    POL-02 / POL-03 contract.

    Parameters
    ----------
    agent_id
        ``AgentConfig.id`` — used as ``Subject.id`` for the per-candidate
        permission filter. Must be non-empty.
    query
        Search query string. Forwarded to the embedding model for semantic
        + hybrid modes; used as the traversal anchor for graph mode.
    scope
        Optional scope string. Shapes per ``RetrieveRequest.scope``:
        ``"track:<id>"`` enables an SQL pre-filter in the embedding store;
        ``"workspace:<id>"`` or ``None`` runs universe-wide search.
    filters
        Optional ``RetrieveFilters`` projection (entry_types + tags),
        applied AFTER the permission filter.
    mode
        ``"graph" | "semantic" | "hybrid"``. Defaults to ``"hybrid"``.
    k
        Vector-store over-fetch budget. ``None`` falls through to
        ``_default_k()`` (env-tunable via ``RETRIEVE_K_DEFAULT``).
    top_n
        Returned-to-caller cap. ``None`` falls through to
        ``_default_top_n()`` (env-tunable via ``RETRIEVE_TOP_N_DEFAULT``).

    Returns
    -------
    RetrieveResponse
        The same wire shape ``POST /api/retrieve`` returns — results
        (sorted descending by score, capped at ``top_n``), aggregate
        ``dropped_for_permission`` count, mode echo, and
        ``candidates_examined`` total.

    Raises
    ------
    ValueError
        When ``agent_id`` is empty. Other validation surfaces (mode-Literal,
        K bounds) are enforced by the ``RetrieveRequest`` Pydantic
        boundary.
    """
    if not agent_id:
        raise ValueError("retrieve_context requires a non-empty agent_id")

    subject = Subject(kind="agent", id=agent_id)

    resolved_k = k if (k is not None and k > 0) else _default_k()
    resolved_top_n = top_n if (top_n is not None and top_n > 0) else _default_top_n()

    payload = RetrieveRequest(
        query=query,
        scope=scope,
        filters=filters,
        mode=mode,
        k=resolved_k,
        top_n=resolved_top_n,
    )

    logger.debug(
        "retrieve_context: agent_id=%s mode=%s scope=%s k=%d top_n=%d",
        agent_id,
        mode,
        scope,
        resolved_k,
        resolved_top_n,
    )

    # Graceful fallback mirrors POST /api/retrieve (Wave 2 of the
    # SaaS-deployment plan): semantic / hybrid degrade to graph when the
    # deployment has no real vector store. Agents see the same envelope
    # the HTTP endpoint emits, including ``degraded`` / ``requested_mode``.
    requested_mode = mode
    effective_mode = mode
    degraded = False
    if mode in ("semantic", "hybrid") and not semantic_retrieval_available():
        _warn_degraded_once(mode)
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
        k=resolved_k,
        top_n=resolved_top_n,
    )

    return RetrieveResponse(
        results=results,
        dropped_for_permission=dropped,
        mode=effective_mode,
        requested_mode=requested_mode,
        degraded=degraded,
        candidates_examined=examined,
    )


__all__ = ["retrieve_context"]
