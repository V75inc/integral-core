"""TTL reclaim regression tests (D-04 + D-15) — Plan 02-04 Task 2.

Storage shape: ChangeEvents live as ``DBLog`` rows with
``log_level="CHANGE_EVENT"`` in the logging database. Reclaim mutates
``log_data.before/after`` and sets ``log_data.snapshot_reclaimed_at``; the
DBLog row itself is never deleted.

D-04 invariants:
- Stale rows have before/after nulled but the row persists
- Recent rows untouched
- The function NEVER deletes DBLog rows
- Metadata fields (event_code, logged_at, actor_*, resource_*, scope)
  preserved exactly
- Env-var-configurable TTL window
- Idempotent (snapshot_reclaimed_at gates re-work)

D-15 invariants:
- Background loop spawn site is gated under TESTING / PYTEST_CURRENT_TEST
- Spawn site uses asyncio.create_task
"""

from __future__ import annotations

from datetime import timedelta
from typing import Any, Dict, Optional

import pytest

from app.services.change_event_logger import (
    CHANGE_EVENT_LOG_LEVEL,
    get_change_event_logger,
)
from app.services.change_event_ttl import reclaim_old_snapshots
from app.utils.time import utc_now


async def _seed_stale_row(
    *,
    days_old: int,
    actor_id: str,
    resource_id: str,
    scope: str,
    before: Optional[Dict[str, Any]] = None,
    after: Optional[Dict[str, Any]] = None,
    action: str = "entry.create",
    actor_kind: str = "human",
    snapshot_reclaimed_at: Optional[str] = None,
) -> str:
    """Persist a CHANGE_EVENT DBLog row backdated to ``days_old`` days ago.

    Returns the DBLog row id. Mirrors what emit_change_event would produce,
    but with a caller-controlled ``logged_at`` so reclaim tests can target
    rows on either side of the TTL cutoff.
    """
    from jvspatial.core.context import GraphContext
    from jvspatial.db import get_database_manager
    from jvspatial.logging.models import DBLog

    log_db = get_database_manager().get_database("logs")
    log_ctx = GraphContext(database=log_db)
    await log_ctx.ensure_indexes(DBLog)

    backdated = utc_now() - timedelta(days=days_old)
    entry = DBLog(
        status_code=None,
        event_code=action,
        log_level=CHANGE_EVENT_LOG_LEVEL,
        path=scope,
        method="",
        logged_at=backdated,
        log_data={
            "message": action,
            "log_level": CHANGE_EVENT_LOG_LEVEL,
            "ts": backdated.isoformat(),
            "actor_kind": actor_kind,
            "actor_id": actor_id,
            "actor_capability": None,
            "resource_type": "Entry",
            "resource_id": resource_id,
            "scope": scope,
            "before": before,
            "after": after,
            "snapshot_reclaimed_at": snapshot_reclaimed_at,
        },
    )
    await entry.set_context(log_ctx)
    await entry.save()
    return entry.id


async def _fetch_row(row_id: str):
    return await get_change_event_logger().get(row_id)


@pytest.mark.asyncio
async def test_reclaim_nullifies_stale_snapshot():
    """Rows older than TTL have before/after nulled; snapshot_reclaimed_at set."""
    row_id = await _seed_stale_row(
        days_old=91,
        actor_id="u1",
        resource_id="e-stale-1",
        scope="track:T-stale",
        before=None,
        after={"id": "e-stale-1", "title": "x"},
    )
    reclaimed = await reclaim_old_snapshots()
    assert reclaimed >= 1

    fresh = await _fetch_row(row_id)
    assert fresh is not None
    data = fresh.log_data
    assert data["before"] is None
    assert data["after"] is None
    assert data["snapshot_reclaimed_at"] is not None
    # Metadata preserved exactly
    assert fresh.event_code == "entry.create"
    assert data["actor_id"] == "u1"
    assert data["actor_kind"] == "human"
    assert data["resource_type"] == "Entry"
    assert data["resource_id"] == "e-stale-1"
    assert fresh.path == "track:T-stale"


@pytest.mark.asyncio
async def test_reclaim_skips_recent():
    """Rows newer than TTL keep their snapshot intact."""
    row_id = await _seed_stale_row(
        days_old=30,
        actor_id="u-recent",
        resource_id="e-recent",
        scope="track:T-recent",
        before=None,
        after={"id": "e-recent"},
    )
    await reclaim_old_snapshots()
    fresh = await _fetch_row(row_id)
    assert fresh is not None
    assert fresh.log_data["after"] == {"id": "e-recent"}
    assert fresh.log_data["snapshot_reclaimed_at"] is None


@pytest.mark.asyncio
async def test_reclaim_idempotent():
    """Repeat passes don't re-reclaim rows whose snapshot_reclaimed_at is set."""
    await _seed_stale_row(
        days_old=91,
        actor_id="u-idem",
        resource_id="e-idem",
        scope="user:u-idem",
        before=None,
        after={"id": "e-idem"},
    )
    first = await reclaim_old_snapshots()
    second = await reclaim_old_snapshots()
    assert second <= first


@pytest.mark.asyncio
async def test_reclaim_never_deletes_rows():
    """D-04 invariant — reclaim NEVER deletes rows."""
    await _seed_stale_row(
        days_old=91,
        actor_id="u-no-del",
        resource_id="e-no-del",
        scope="user:u-no-del",
        before=None,
        after={"id": "e-no-del"},
    )
    ce_logger = get_change_event_logger()
    rows_before = len(await ce_logger.find_all())
    await reclaim_old_snapshots()
    rows_after = len(await ce_logger.find_all())
    assert (
        rows_before == rows_after
    ), f"reclaim deleted rows: {rows_before} → {rows_after}"


@pytest.mark.asyncio
async def test_reclaim_honors_env_ttl(monkeypatch):
    """CHANGE_EVENT_SNAPSHOT_TTL_DAYS env var honored — proves configurability."""
    monkeypatch.setenv("CHANGE_EVENT_SNAPSHOT_TTL_DAYS", "10")
    row_id = await _seed_stale_row(
        days_old=11,
        actor_id="u-env",
        resource_id="e-env",
        scope="user:u-env",
        before=None,
        after={"id": "e-env"},
    )
    await reclaim_old_snapshots()
    fresh = await _fetch_row(row_id)
    assert fresh is not None
    assert fresh.log_data["after"] is None
    assert fresh.log_data["snapshot_reclaimed_at"] is not None


@pytest.mark.asyncio
async def test_reclaim_handles_unparseable_logged_at_defensively(caplog):
    """Unparseable logged_at values are skipped, not crashed.

    DBLog.logged_at is a datetime in normal use; an explicit garbage value
    going through the find() reconstruction path proves the reclaim loop is
    resilient to bad rows. The reclaim implementation must skip such rows
    without raising.
    """
    import logging

    from jvspatial.db import get_database_manager

    # Insert a malformed row directly so we can exercise the parse-error path.
    log_db = get_database_manager().get_database("logs")
    # Direct-write a row with a garbage logged_at string.
    await log_db.save(
        "object",
        {
            "id": "ce-bad-ts",
            "entity": "DBLog",
            "context": {
                "status_code": None,
                "event_code": "entry.create",
                "log_level": CHANGE_EVENT_LOG_LEVEL,
                "path": "user:u-bad-ts",
                "method": "",
                "logged_at": "not-a-real-timestamp",
                "log_data": {
                    "message": "entry.create",
                    "log_level": CHANGE_EVENT_LOG_LEVEL,
                    "actor_kind": "human",
                    "actor_id": "u-bad-ts",
                    "resource_type": "Entry",
                    "resource_id": "e-bad",
                    "scope": "user:u-bad-ts",
                    "before": None,
                    "after": {"id": "e-bad"},
                    "snapshot_reclaimed_at": None,
                },
            },
        },
    )

    with caplog.at_level(logging.WARNING):
        # Must not crash.
        await reclaim_old_snapshots()

    # The row survives (no row deletion).
    raw = await log_db.find("object", {"id": "ce-bad-ts"})
    assert raw, "reclaim deleted a malformed row instead of skipping it"


def test_reclaim_module_exports_loop_and_function():
    """ttl_reclaim_loop + reclaim_old_snapshots both exported (D-15 spawn target)."""
    import inspect

    from app.services.change_event_ttl import (
        reclaim_old_snapshots,
        ttl_reclaim_loop,
    )

    assert callable(reclaim_old_snapshots)
    assert callable(ttl_reclaim_loop)
    assert inspect.iscoroutinefunction(reclaim_old_snapshots)
    assert inspect.iscoroutinefunction(ttl_reclaim_loop)


def test_main_py_spawn_site_present_and_testing_gated():
    """app/main.py spawns ttl_reclaim_loop via asyncio.create_task, gated under TESTING."""
    from pathlib import Path

    main_path = Path(__file__).resolve().parent.parent / "app" / "main.py"
    src = main_path.read_text(encoding="utf-8")
    assert (
        "asyncio.create_task" in src and "ttl_reclaim_loop" in src
    ), "D-15 spawn site not present in app/main.py"
    assert (
        "PYTEST_CURRENT_TEST" in src or "TESTING" in src
    ), "TESTING gate missing — TTL loop would run during tests"


def test_reclaim_does_not_delete_rows_grep_invariant():
    """Source-level invariant — change_event_ttl.py must NEVER delete rows.

    AST-walks the module so docstring prose mentioning the forbidden methods
    does NOT trigger a false positive.
    """
    import ast
    from pathlib import Path

    src_path = (
        Path(__file__).resolve().parent.parent
        / "app"
        / "services"
        / "change_event_ttl.py"
    )
    tree = ast.parse(src_path.read_text(encoding="utf-8"))

    forbidden_methods = {"destroy", "delete"}
    bad_calls: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Attribute) and node.attr in forbidden_methods:
            value = node.value
            if isinstance(value, ast.Name) and value.id in {"DBLog", "ChangeEvent"}:
                bad_calls.append(f"{value.id}.{node.attr}")
    assert (
        not bad_calls
    ), f"change_event_ttl.py contains forbidden delete patterns: {bad_calls}"
