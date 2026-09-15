"""Template-var resolver registry for ContentProfile manifest filter rules.

Phase 3.1 ANC-07 — extensible resolver registry. v1 resolvers:
  :entry_id        → context-resolved current entry id (sibling-track pattern)
  :anchored_track  → ANCHORS edge target of current entry (anchor pattern)
  :current_user    → resolve_principal_id(request)

Resolvers compose; manifests reference them as literal strings in ``filter.value``
slots (e.g., ``{ field: "assignee", op: "eq", value: ":current_user" }``).

Registration hygiene mirrors Phase 1 D-07: tokens MUST start with ``':'`` and
duplicate registration raises ``ValueError`` (single-source-of-truth). Unknown
tokens at resolve time return ``None`` (fail-soft so callers can skip the rule
without crashing the render pipeline).
"""

from __future__ import annotations

import logging
from typing import Any, Awaitable, Callable, Dict, List, Optional

from starlette.requests import Request

logger = logging.getLogger(__name__)

ResolverFn = Callable[[Request, Dict[str, Any]], Awaitable[Optional[str]]]

_REGISTRY: Dict[str, ResolverFn] = {}


def register_resolver(token: str, fn: ResolverFn) -> None:
    """Register a template-var resolver.

    Tokens MUST start with ':'. Duplicate registration raises ``ValueError`` —
    enforces single-source-of-truth (mirror of Phase 1 D-07 registry hygiene).
    """
    if not isinstance(token, str) or not token.startswith(":"):
        raise ValueError(f"Resolver token must start with ':'; got {token!r}")
    if token in _REGISTRY:
        raise ValueError(f"Resolver {token!r} already registered")
    _REGISTRY[token] = fn


def get_registered_tokens() -> List[str]:
    """Return the list of registered resolver tokens (deterministic order)."""
    return sorted(_REGISTRY.keys())


async def resolve_template_var(
    token: str,
    request: Optional[Request],
    context: Dict[str, Any],
) -> Optional[str]:
    """Resolve a template-var token.

    Unknown tokens return ``None`` (fail-soft). Resolver exceptions are
    logged and also return ``None`` — callers can safely treat None as
    "skip this rule" without crashing the filter pipeline.
    """
    fn = _REGISTRY.get(token)
    if fn is None:
        return None
    try:
        return await fn(request, context)  # type: ignore[arg-type]
    except Exception as e:
        logger.warning("Resolver %r raised: %s", token, e)
        return None


# ---------------------------------------------------------------------------
# v1 resolvers — registered at import time
# ---------------------------------------------------------------------------


async def _resolve_current_user(
    request: Optional[Request], _ctx: Dict[str, Any]
) -> Optional[str]:
    """Return the authenticated principal id via app.api.utils.resolve_principal_id."""
    from app.api.utils import resolve_principal_id

    return resolve_principal_id(request)


async def _resolve_entry_id(
    _req: Optional[Request], ctx: Dict[str, Any]
) -> Optional[str]:
    """Return ``ctx['entry_id']`` as a string, or None if missing/empty."""
    val = ctx.get("entry_id")
    return str(val) if val else None


async def _resolve_anchored_track(
    _req: Optional[Request], ctx: Dict[str, Any]
) -> Optional[str]:
    """Walk the ANCHORS edge from ``ctx['entry_id']``; return first target Track id.

    Read-only edge traversal — does NOT write ANCHORS edges (the write path
    gate is enforced in ``content_profile_graph._sync_anchor_edges``).
    """
    from app.models.nodes import Entry

    eid = ctx.get("entry_id")
    if not eid:
        return None
    entry = await Entry.get(str(eid))
    if entry is None:
        return None
    tracks = await entry.nodes(edge=["ANCHORS"], direction="out", node=["Track"])
    if not tracks:
        return None
    return str(tracks[0].id)


register_resolver(":current_user", _resolve_current_user)
register_resolver(":entry_id", _resolve_entry_id)
register_resolver(":anchored_track", _resolve_anchored_track)
