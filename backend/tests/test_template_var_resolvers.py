"""Template-var resolver registry tests — Phase 3.1 Plan 03.1-04 Task 1 (ANC-07).

Covers:
  - register_resolver token-prefix check + duplicate rejection (Phase 1 D-07 hygiene)
  - v1 resolvers (:current_user, :entry_id, :anchored_track) round-trip
  - Unknown token returns None (fail-soft)
  - get_registered_tokens enumerates the v1 set

No ``app.main`` import — test_auth middleware is gitignored per
deferred-items.md; the resolver registry is exercised directly.
"""

from __future__ import annotations

from typing import Any, Dict, Optional
from unittest.mock import MagicMock

import pytest

from app.models.edges import ANCHORS
from app.models.nodes import Entry, Track
from app.services.template_var_resolvers import (
    get_registered_tokens,
    register_resolver,
    resolve_template_var,
)

# ===== Registration hygiene =====


def test_register_resolver_requires_colon_prefix():
    """Tokens must start with ':' — mirrors Phase 1 D-07 hygiene."""

    async def fn(req: Any, ctx: Dict[str, Any]) -> Optional[str]:
        return None

    with pytest.raises(ValueError) as ei:
        register_resolver("bogus", fn)
    assert "':'" in str(ei.value)


def test_register_resolver_rejects_duplicates():
    """Duplicate registration raises ValueError (single-source-of-truth)."""

    async def fn(req: Any, ctx: Dict[str, Any]) -> Optional[str]:
        return None

    register_resolver(":test_dup_a", fn)
    with pytest.raises(ValueError) as ei:
        register_resolver(":test_dup_a", fn)
    assert "already registered" in str(ei.value)


def test_get_registered_tokens_includes_v1():
    """v1 resolvers registered at import time."""
    tokens = set(get_registered_tokens())
    assert {":current_user", ":entry_id", ":anchored_track"}.issubset(tokens)


# ===== Resolution =====


@pytest.mark.asyncio
async def test_resolve_current_user():
    """``:current_user`` returns request.state.user.id via resolve_principal_id."""
    req = MagicMock()
    req.state.user.id = "u-current-user-1"
    # resolve_principal_id() will fall through to get_user_id_from_request,
    # which reads request.state.user.id.
    result = await resolve_template_var(":current_user", req, {})
    assert result == "u-current-user-1"


@pytest.mark.asyncio
async def test_resolve_entry_id():
    """``:entry_id`` returns context['entry_id'] as string."""
    req = MagicMock()
    result = await resolve_template_var(":entry_id", req, {"entry_id": "e-tvr-1"})
    assert result == "e-tvr-1"


@pytest.mark.asyncio
async def test_resolve_entry_id_missing_returns_none():
    """Missing context['entry_id'] yields None (fail-soft)."""
    req = MagicMock()
    result = await resolve_template_var(":entry_id", req, {})
    assert result is None


@pytest.mark.asyncio
async def test_resolve_anchored_track():
    """``:anchored_track`` walks ANCHORS edge from ctx entry to its target Track."""
    track = await Track.create(
        title="Detail TVR",
        owner_id="tvr-owner",
        workspace_id="ws-tvr",
    )
    entry = await Entry.create(
        track_id="tvr-parent-t",
        title="Parent TVR",
        author_id="tvr-owner",
    )
    await entry.connect(track, edge=ANCHORS, field_key="d", role="detail")

    req = MagicMock()
    result = await resolve_template_var(":anchored_track", req, {"entry_id": entry.id})
    assert result == track.id


@pytest.mark.asyncio
async def test_resolve_anchored_track_no_anchor_returns_none():
    """Entry without ANCHORS edge yields None."""
    entry = await Entry.create(
        track_id="tvr-parent-no-anchor",
        title="Parent NoAnchor",
        author_id="tvr-owner",
    )
    req = MagicMock()
    result = await resolve_template_var(":anchored_track", req, {"entry_id": entry.id})
    assert result is None


@pytest.mark.asyncio
async def test_resolve_unknown_token_returns_none():
    """Unknown tokens yield None (fail-soft — caller skips the rule)."""
    req = MagicMock()
    result = await resolve_template_var(":nonexistent_token_xyz", req, {})
    assert result is None


@pytest.mark.asyncio
async def test_resolve_resolver_exception_returns_none(monkeypatch):
    """Resolver raising → logged + returns None (fail-soft)."""

    async def _boom(req: Any, ctx: Dict[str, Any]) -> Optional[str]:
        raise RuntimeError("boom")

    register_resolver(":boom_xyz", _boom)
    req = MagicMock()
    result = await resolve_template_var(":boom_xyz", req, {})
    assert result is None
