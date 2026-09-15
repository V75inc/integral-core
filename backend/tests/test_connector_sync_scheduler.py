"""Phase 5 Plan 05-03 — sync_loop scheduler regression suite.

Covers:
- TESTING gate prevents spawn under PYTEST_CURRENT_TEST (Pitfall 6)
- Per-connector dispatch when sync_interval_seconds elapsed
- Skip dispatch when interval not yet elapsed
- Empty registry / no connectors → no-op tick (I-SYNC-02 AGENTIVE-independence)
- Tick failures swallowed (transient DB hiccup must not kill the loop)
"""

from __future__ import annotations

import asyncio
import os
from datetime import datetime, timedelta, timezone
from typing import List
from unittest.mock import AsyncMock, patch

import pytest

from app.services.connectors.sync_scheduler import (
    _maybe_dispatch_one,
    _tick_interval_seconds,
    _tick_once,
)

# ---------------------------------------------------------------------------
# Stub Connector — minimal duck-typed object (the scheduler reads only
# `last_synced_at` + `sync_interval_seconds`).
# ---------------------------------------------------------------------------


class _StubConnector:
    def __init__(
        self,
        *,
        cid: str = "c-1",
        last_synced_at=None,
        sync_interval_seconds: int = 300,
    ):
        self.id = cid
        self.last_synced_at = last_synced_at
        self.sync_interval_seconds = sync_interval_seconds


# ---------------------------------------------------------------------------
# Test 1 — TESTING gate at main.py:_startup prevents sync_loop spawn
# ---------------------------------------------------------------------------


def test_testing_gate_short_circuits_startup_before_sync_loop_spawn():
    """The function-scope TESTING gate at main.py:_startup runs FIRST.

    Under pytest (``PYTEST_CURRENT_TEST`` is set), ``_startup`` returns
    immediately at L137 before reaching the sync_loop spawn site (which sits
    next to the ttl_reclaim_loop spawn at L243-247+). Inspect the source to
    prove the gate is positioned earlier than the spawn line.
    """
    import inspect

    from app.main import _startup

    src = inspect.getsource(_startup)
    # Find the gate line + the spawn line.
    gate_idx = src.find('os.getenv("PYTEST_CURRENT_TEST")')
    spawn_idx = src.find("sync_loop")
    assert gate_idx > -1, "TESTING gate not found in _startup"
    assert spawn_idx > -1, "sync_loop spawn not found in _startup"
    # Gate must appear before the spawn site so test runs short-circuit out.
    assert gate_idx < spawn_idx


def test_pytest_env_is_set_during_tests():
    """Confirm the env var the gate reads is actually set in the test process."""
    assert os.getenv("PYTEST_CURRENT_TEST") or os.getenv("TESTING")


# ---------------------------------------------------------------------------
# Test 2 — empty registry / no connectors → no-op tick
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_empty_registry_returns_zero_dispatches():
    """I-SYNC-02 (AGENTIVE_ENABLED-independence): no connectors → 0 dispatches."""
    # Patch Connector.find inside the scheduler's lazy-import path.
    with patch(
        "app.agentive.nodes.Connector.find",
        new=AsyncMock(return_value=[]),
    ):
        dispatched = await _tick_once()
    assert dispatched == 0


# ---------------------------------------------------------------------------
# Test 3 — per-connector dispatch when interval elapsed
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_dispatch_when_interval_elapsed():
    """Connector with last_synced_at 1h ago + 30s interval → dispatches."""
    old = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()
    connector = _StubConnector(
        cid="c-eligible",
        last_synced_at=old,
        sync_interval_seconds=30,
    )

    # Stub sync_one_connector so we never actually call out + can count.
    sync_called: List[str] = []

    async def _fake_sync(c):
        sync_called.append(c.id)
        return {}

    with patch(
        "app.services.connectors.sync_runtime.sync_one_connector",
        side_effect=_fake_sync,
    ):
        dispatched = await _maybe_dispatch_one(connector)
        # Let the fire-and-forget task run.
        await asyncio.sleep(0.01)

    assert dispatched is True
    assert sync_called == ["c-eligible"]


# ---------------------------------------------------------------------------
# Test 4 — skip dispatch when interval not yet elapsed
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_skip_when_interval_not_elapsed():
    """Connector with last_synced_at 5s ago + 300s interval → no dispatch."""
    recent = (datetime.now(timezone.utc) - timedelta(seconds=5)).isoformat()
    connector = _StubConnector(
        cid="c-too-soon",
        last_synced_at=recent,
        sync_interval_seconds=300,
    )

    sync_called: List[str] = []

    async def _fake_sync(c):
        sync_called.append(c.id)
        return {}

    with patch(
        "app.services.connectors.sync_runtime.sync_one_connector",
        side_effect=_fake_sync,
    ):
        dispatched = await _maybe_dispatch_one(connector)
        await asyncio.sleep(0.01)

    assert dispatched is False
    assert sync_called == []


# ---------------------------------------------------------------------------
# Test 5 — first-tick eligibility (last_synced_at is None)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_first_tick_dispatches_when_never_synced():
    """Connector that has never synced (last_synced_at=None) is always eligible."""
    connector = _StubConnector(cid="c-fresh", last_synced_at=None)

    sync_called: List[str] = []

    async def _fake_sync(c):
        sync_called.append(c.id)
        return {}

    with patch(
        "app.services.connectors.sync_runtime.sync_one_connector",
        side_effect=_fake_sync,
    ):
        dispatched = await _maybe_dispatch_one(connector)
        await asyncio.sleep(0.01)

    assert dispatched is True
    assert sync_called == ["c-fresh"]


# ---------------------------------------------------------------------------
# Test 6 — tick failure swallowed (no kill the loop)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_tick_failure_is_swallowed():
    """_tick_once must not raise — a failing Connector.find logs + returns 0."""
    with patch(
        "app.agentive.nodes.Connector.find",
        new=AsyncMock(side_effect=RuntimeError("DB hiccup")),
    ):
        dispatched = await _tick_once()
    assert dispatched == 0


# ---------------------------------------------------------------------------
# Test 7 — tick interval default
# ---------------------------------------------------------------------------


def test_tick_interval_default_60s(monkeypatch):
    monkeypatch.delenv("CONNECTOR_SYNC_TICK_SECONDS", raising=False)
    assert _tick_interval_seconds() == 60.0


def test_tick_interval_env_override(monkeypatch):
    monkeypatch.setenv("CONNECTOR_SYNC_TICK_SECONDS", "10")
    assert _tick_interval_seconds() == 10.0


def test_tick_interval_invalid_falls_back_to_default(monkeypatch):
    monkeypatch.setenv("CONNECTOR_SYNC_TICK_SECONDS", "not-a-number")
    assert _tick_interval_seconds() == 60.0


# ---------------------------------------------------------------------------
# Test 8 — Conflict + Connector routers registered in core main.py
# ---------------------------------------------------------------------------


def test_connectors_sync_router_registered_in_core():
    """Locked decision #12 — connector sync + conflicts routers ALWAYS-ON."""
    from app.main import app

    # Enumerate registered routes via the OpenAPI schema rather than
    # ``app.routes``: fastapi 0.136+ wraps included routers in ``_IncludedRouter``
    # (no ``.path``), so direct iteration is version-fragile. The schema is the
    # canonical, version-stable view of what's registered.
    paths = set(app.openapi().get("paths", {}).keys())
    # FastAPI normalizes path parameters; check the prefix shape.
    has_sync = any("/api/connectors/" in p and "/sync" in p for p in paths)
    has_conflicts_list = "/api/conflicts" in paths
    has_conflicts_resolve = any(
        "/api/conflicts/" in p and "/resolve" in p for p in paths
    )
    assert has_sync, f"missing /api/connectors/{{id}}/sync (paths={paths})"
    assert has_conflicts_list, f"missing /api/conflicts (paths={paths})"
    assert has_conflicts_resolve, f"missing /api/conflicts/{{id}}/resolve"
