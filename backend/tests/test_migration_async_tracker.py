"""Phase 5 Plan 05-02 — async per-Entry migration runner + CP rollup tests.

Covers MIG-02:
  - publish marks all affected Entries 'pending' SYNCHRONOUSLY before
    the runner spawns (visible to immediate-after-HTTP polling).
  - async runner walks each Entry and transitions
    pending → running → complete (or failed).
  - per-Entry failure isolation — one Entry's op raise marks ONLY that
    Entry 'failed'; siblings continue 'complete'.
  - OperationalModel.migration_status rollup: 'failed' wins over 'in_progress'
    wins over 'complete'.
  - single ``migration.run`` ChangeEvent emitted on runner completion
    (locked decision #5).

These tests use stubbed Track/Entry/OperationalModel objects so they exercise
the runner orchestration logic without requiring the full graph DB. The
companion DB-backed integration test lives at
``test_migration_reject_gate.py::test_publish_endpoint_*`` (which spins up
the full FastAPI client).
"""

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.services.migrations import rollup_cp_status
from app.services.migrations.runner import (
    _async_migration_runner,
    gather_affected_entries,
    mark_entries_pending,
    migration_status_snapshot,
    reconcile_orphaned_migrations,
    run_migration_async,
)


def _make_stub_cp(cp_id="cp-1", scope="track"):
    cp = MagicMock()
    cp.id = cp_id
    cp.scope = scope
    cp.migration_status = "complete"
    cp.save = AsyncMock()
    return cp


def _make_stub_entry(entry_id, track_id="track-1", type_id="et-1"):
    e = MagicMock()
    e.id = entry_id
    e.track_id = track_id
    e.type_id = type_id
    e.migration_status = "complete"
    e.migration_error = None
    e.custom_fields = {}
    e.save = AsyncMock()
    return e


def _make_stub_track(track_id, entries):
    t = MagicMock()
    t.id = track_id

    async def _nodes(edge=None, direction=None, node=None):
        labels = node or []
        if "Entry" in labels:
            return entries
        return []

    t.nodes = _nodes
    return t


# ---------------------------------------------------------------------------
# rollup_cp_status — pure rollup table
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_rollup_all_complete():
    cp = _make_stub_cp()
    entries = [_make_stub_entry("e1"), _make_stub_entry("e2")]
    for e in entries:
        e.migration_status = "complete"
    status = await rollup_cp_status(published_cp=cp, affected_entries=entries)
    assert status == "complete"
    assert cp.migration_status == "complete"


@pytest.mark.asyncio
async def test_rollup_any_pending_is_in_progress():
    cp = _make_stub_cp()
    entries = [_make_stub_entry("e1"), _make_stub_entry("e2")]
    entries[0].migration_status = "complete"
    entries[1].migration_status = "pending"
    status = await rollup_cp_status(published_cp=cp, affected_entries=entries)
    assert status == "in_progress"
    assert cp.migration_status == "in_progress"


@pytest.mark.asyncio
async def test_rollup_any_running_is_in_progress():
    cp = _make_stub_cp()
    entries = [_make_stub_entry("e1"), _make_stub_entry("e2")]
    entries[0].migration_status = "running"
    entries[1].migration_status = "complete"
    status = await rollup_cp_status(published_cp=cp, affected_entries=entries)
    assert status == "in_progress"


@pytest.mark.asyncio
async def test_rollup_any_failed_is_failed():
    """Failed wins over in_progress wins over complete (locked decision)."""
    cp = _make_stub_cp()
    entries = [
        _make_stub_entry("e1"),
        _make_stub_entry("e2"),
        _make_stub_entry("e3"),
    ]
    entries[0].migration_status = "complete"
    entries[1].migration_status = "running"
    entries[2].migration_status = "failed"
    status = await rollup_cp_status(published_cp=cp, affected_entries=entries)
    assert status == "failed"
    assert cp.migration_status == "failed"


@pytest.mark.asyncio
async def test_rollup_empty_is_complete():
    cp = _make_stub_cp()
    status = await rollup_cp_status(published_cp=cp, affected_entries=[])
    assert status == "complete"


@pytest.mark.asyncio
async def test_rollup_treats_missing_field_as_complete():
    """Rollup tolerates Entry rows pre-dating the Plan 05-01 field addition."""
    cp = _make_stub_cp()
    e = MagicMock()
    e.id = "legacy"
    # no migration_status attribute at all
    del e.migration_status
    status = await rollup_cp_status(published_cp=cp, affected_entries=[e])
    assert status == "complete"


# ---------------------------------------------------------------------------
# mark_entries_pending — synchronous pre-mark
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_mark_entries_pending_sets_all_pending():
    entries = [_make_stub_entry("e1"), _make_stub_entry("e2")]
    await mark_entries_pending(entries)
    assert entries[0].migration_status == "pending"
    assert entries[1].migration_status == "pending"
    assert entries[0].migration_error is None
    entries[0].save.assert_awaited()
    entries[1].save.assert_awaited()


@pytest.mark.asyncio
async def test_mark_entries_pending_clears_prior_error():
    entries = [_make_stub_entry("e1")]
    entries[0].migration_status = "failed"
    entries[0].migration_error = "prior error string"
    await mark_entries_pending(entries)
    assert entries[0].migration_status == "pending"
    assert entries[0].migration_error is None


@pytest.mark.asyncio
async def test_status_snapshot_reports_counts_and_failed_diagnostics():
    cp = _make_stub_cp()
    entries = [_make_stub_entry("e1"), _make_stub_entry("e2")]
    entries[0].migration_status = "failed"
    entries[0].migration_error = "cannot coerce estimate"
    entries[1].migration_status = "complete"
    track = _make_stub_track("track-1", entries)

    with patch(
        "app.services.migrations.runner._affected_tracks",
        new=AsyncMock(return_value=[track]),
    ):
        snapshot = await migration_status_snapshot(cp)

    assert snapshot["counts"] == {
        "pending": 0,
        "running": 0,
        "complete": 1,
        "failed": 1,
    }
    assert snapshot["failed_entries"] == [
        {
            "entry_id": "e1",
            "track_id": "track-1",
            "error": "cannot coerce estimate",
        }
    ]


@pytest.mark.asyncio
async def test_restart_reconciliation_marks_orphaned_entries_retryable():
    cp = _make_stub_cp()
    cp.migration_status = "in_progress"
    entries = [_make_stub_entry("e1"), _make_stub_entry("e2")]
    entries[0].migration_status = "pending"
    entries[1].migration_status = "running"
    track = _make_stub_track("track-1", entries)

    with (
        patch(
            "app.services.migrations.runner.OperationalModel.find",
            new=AsyncMock(return_value=[cp]),
        ),
        patch(
            "app.services.migrations.runner._affected_tracks",
            new=AsyncMock(return_value=[track]),
        ),
    ):
        result = await reconcile_orphaned_migrations()

    assert result == {"profiles": 1, "entries": 2}
    assert cp.migration_status == "failed"
    assert all(e.migration_status == "failed" for e in entries)
    assert all("process restart" in e.migration_error for e in entries)


@pytest.mark.asyncio
async def test_restart_reconciliation_leaves_durable_migration_work_to_kernel():
    """Startup recovery must not race the leased WorkItem authority."""
    cp = _make_stub_cp()
    cp.migration_status = "in_progress"
    work = MagicMock()
    work.status = "running"
    work.input_payload = {"operational_model_id": cp.id}

    with (
        patch(
            "app.agentive.work_models.WorkItem.find",
            new=AsyncMock(return_value=[work]),
        ),
        patch(
            "app.services.migrations.runner.OperationalModel.find",
            new=AsyncMock(return_value=[cp]),
        ),
        patch(
            "app.services.migrations.runner.gather_affected_entries",
            new=AsyncMock(),
        ) as gather,
    ):
        result = await reconcile_orphaned_migrations()

    assert result == {"profiles": 0, "entries": 0}
    assert cp.migration_status == "in_progress"
    gather.assert_not_awaited()


# ---------------------------------------------------------------------------
# _async_migration_runner — walks entries, marks status, emits ChangeEvent
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_runner_marks_all_entries_complete_when_no_ops_declared():
    """Runner with empty migrations list still transitions entries to complete."""
    cp = _make_stub_cp()
    entries = [_make_stub_entry("e1"), _make_stub_entry("e2")]
    track = _make_stub_track("track-1", entries)

    with (
        patch(
            "app.services.migrations.runner._affected_tracks",
            new=AsyncMock(return_value=[track]),
        ),
        patch(
            "app.services.migrations.runner.emit_change_event",
            new=AsyncMock(),
        ) as emit_mock,
    ):
        result = await _async_migration_runner(
            published_cp=cp,
            compiled_manifest={"migrations": []},
            affected_entries=entries,
        )

    assert result["status"] == "complete"
    assert result["failed_entry_count"] == 0
    assert result["affected_entry_count"] == 2
    assert entries[0].migration_status == "complete"
    assert entries[1].migration_status == "complete"
    # Single ChangeEvent emitted, action=migration.run, state=complete
    emit_mock.assert_awaited_once()
    kwargs = emit_mock.await_args.kwargs
    assert kwargs["action"] == "migration.run"
    assert kwargs["details"]["state"] == "complete"
    assert kwargs["details"]["affected_entry_count"] == 2


@pytest.mark.asyncio
async def test_runner_isolates_failed_track_without_replaying_track_wide_op():
    """A Track-wide handler runs once and failure marks only that Track's rows."""
    cp = _make_stub_cp()
    entries = [
        _make_stub_entry("e-ok-1"),
        _make_stub_entry("e-fail"),
        _make_stub_entry("e-ok-2"),
    ]
    track = _make_stub_track("track-1", entries)

    call_count = {"n": 0}

    async def fake_handler(*, track, op, log):
        # A Track-wide handler is called exactly once, not once per entry.
        call_count["n"] += 1
        log["errors"].append({"reason": "synthetic op failure"})

    with (
        patch(
            "app.services.migrations.runner._affected_tracks",
            new=AsyncMock(return_value=[track]),
        ),
        patch.dict(
            "app.services.migrations.runner._OP_HANDLERS",
            {"rename_field": fake_handler},
            clear=False,
        ),
        patch(
            "app.services.migrations.runner.emit_change_event",
            new=AsyncMock(),
        ) as emit_mock,
    ):
        result = await _async_migration_runner(
            published_cp=cp,
            compiled_manifest={
                "migrations": [
                    {
                        "ops": [
                            {
                                "op": "rename_field",
                                "entry_type": "t",
                                "from": "a",
                                "to": "b",
                            },
                        ]
                    }
                ]
            },
            affected_entries=entries,
        )

    assert result["status"] == "failed"
    assert result["failed_entry_count"] == 3
    statuses = [e.migration_status for e in entries]
    assert statuses.count("failed") == 3
    assert call_count["n"] == 1
    # ChangeEvent state reflects failure
    kwargs = emit_mock.await_args.kwargs
    assert kwargs["details"]["state"] == "failed"
    assert kwargs["details"]["failed_entry_count"] == 3
    assert all("synthetic" in e.migration_error for e in entries)


@pytest.mark.asyncio
async def test_runner_executes_each_track_operation_once_and_counts_unique_mutations():
    cp = _make_stub_cp()
    entries = [_make_stub_entry("e1"), _make_stub_entry("e2")]
    track = _make_stub_track("track-1", entries)
    calls = {"count": 0}

    async def fake_handler(*, track, op, log):
        calls["count"] += 1
        log["mutated_entries"].extend(["e1", "e2", "e1"])

    with (
        patch(
            "app.services.migrations.runner._affected_tracks",
            new=AsyncMock(return_value=[track]),
        ),
        patch.dict(
            "app.services.migrations.runner._OP_HANDLERS",
            {"rename_field": fake_handler},
            clear=False,
        ),
        patch("app.services.migrations.runner.emit_change_event", new=AsyncMock()),
    ):
        result = await _async_migration_runner(
            published_cp=cp,
            compiled_manifest={"migrations": [{"ops": [{"op": "rename_field"}]}]},
            affected_entries=entries,
        )

    assert calls["count"] == 1
    assert result["mutated_entry_count"] == 2
    assert all(entry.migration_status == "complete" for entry in entries)


@pytest.mark.asyncio
async def test_runner_emits_single_change_event():
    """Locked decision #5 — exactly ONE migration.run ChangeEvent per runner."""
    cp = _make_stub_cp()
    entries = [_make_stub_entry(f"e{i}") for i in range(5)]
    track = _make_stub_track("track-1", entries)
    with (
        patch(
            "app.services.migrations.runner._affected_tracks",
            new=AsyncMock(return_value=[track]),
        ),
        patch(
            "app.services.migrations.runner.emit_change_event",
            new=AsyncMock(),
        ) as emit_mock,
    ):
        await _async_migration_runner(
            published_cp=cp,
            compiled_manifest={"migrations": []},
            affected_entries=entries,
        )
    assert emit_mock.await_count == 1


@pytest.mark.asyncio
async def test_runner_change_event_details_carry_ops_applied():
    cp = _make_stub_cp()
    entries = [_make_stub_entry("e1")]
    track = _make_stub_track("track-1", entries)

    async def fake_handler(*, track, op, log):
        return None

    with (
        patch(
            "app.services.migrations.runner._affected_tracks",
            new=AsyncMock(return_value=[track]),
        ),
        patch.dict(
            "app.services.migrations.runner._OP_HANDLERS",
            {
                "rename_field": fake_handler,
                "default_fill": fake_handler,
            },
            clear=False,
        ),
        patch(
            "app.services.migrations.runner.emit_change_event",
            new=AsyncMock(),
        ) as emit_mock,
    ):
        await _async_migration_runner(
            published_cp=cp,
            compiled_manifest={
                "migrations": [
                    {
                        "ops": [
                            {"op": "rename_field"},
                            {"op": "default_fill"},
                        ]
                    }
                ]
            },
            affected_entries=entries,
        )
    kwargs = emit_mock.await_args.kwargs
    assert set(kwargs["details"]["ops_applied"]) == {"rename_field", "default_fill"}


# ---------------------------------------------------------------------------
# run_migration_async — public entry point (pre-mark + spawn)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_run_migration_async_premarks_entries_before_spawn():
    """All affected entries reach 'pending' synchronously before runner spawns."""
    cp = _make_stub_cp()
    entries = [_make_stub_entry("e1"), _make_stub_entry("e2")]
    track = _make_stub_track("track-1", entries)

    with (
        patch(
            "app.services.migrations.runner._affected_tracks",
            new=AsyncMock(return_value=[track]),
        ),
        patch(
            "app.services.migrations.runner.emit_change_event",
            new=AsyncMock(),
        ),
    ):
        result = await run_migration_async(
            published_cp=cp,
            compiled_manifest={"migrations": []},
            await_runner=True,  # await so we observe terminal state
        )

    # After await_runner=True the terminal state is 'complete' for the cp
    # rollup. The pre-mark contract is observable via the cp's intermediate
    # state being set to 'in_progress' before the runner ran.
    assert result["affected_entry_count"] == 2
    # Final cp state after runner await is 'complete' (rollup)
    assert cp.migration_status == "complete"
    # Entries reached terminal state
    assert entries[0].migration_status == "complete"
    assert entries[1].migration_status == "complete"


@pytest.mark.asyncio
async def test_run_migration_async_sets_cp_in_progress_pre_spawn():
    """CP.migration_status='in_progress' is set BEFORE the runner spawns."""
    cp = _make_stub_cp()
    track = _make_stub_track("track-1", [])

    pre_spawn_status = {}

    async def capture_status(*args, **kwargs):
        # Capture cp.migration_status the moment the runner is invoked.
        pre_spawn_status["value"] = cp.migration_status
        return {
            "status": "complete",
            "mutated_entry_count": 0,
            "failed_entry_count": 0,
            "affected_entry_count": 0,
        }

    with (
        patch(
            "app.services.migrations.runner._affected_tracks",
            new=AsyncMock(return_value=[track]),
        ),
        patch(
            "app.services.migrations.runner._async_migration_runner",
            new=capture_status,
        ),
    ):
        await run_migration_async(
            published_cp=cp,
            compiled_manifest={"migrations": []},
            await_runner=True,
        )

    assert pre_spawn_status["value"] == "in_progress"


@pytest.mark.asyncio
async def test_run_migration_async_enqueues_durable_work_in_production():
    """Normal publication never relies on an event-loop-only migration task."""
    cp = _make_stub_cp()
    cp.workspace_id = "ws-migration"
    queued = {
        "status": "queued",
        "work_item_id": "migration-work-1",
        "affected_entry_count": None,
    }

    with patch(
        "app.services.migrations.runner.enqueue_migration_work",
        new=AsyncMock(return_value=queued),
    ) as enqueue:
        result = await run_migration_async(
            published_cp=cp,
            compiled_manifest={"migrations": []},
            actor_id="user-1",
        )

    assert result == queued
    enqueue.assert_awaited_once_with(
        published_cp=cp,
        compiled_manifest={"migrations": []},
        actor_id="user-1",
    )


@pytest.mark.asyncio
async def test_retry_enqueue_creates_a_new_child_work_identity():
    """A failed WorkItem is evidence, never the item a recovery tries to claim."""
    from app.services.migrations.runner import enqueue_migration_work

    cp = _make_stub_cp()
    cp.workspace_id = "ws-migration"
    captured = {}
    queued_work = MagicMock()
    queued_work.status = "queued"
    queued_work.work_item_id = "migration-attempt-2"

    async def capture_enqueue(**kwargs):
        captured.update(kwargs)
        return queued_work

    with (
        patch(
            "app.services.migrations.runner.resolve_migration_work_scope",
            new=AsyncMock(return_value=("ws-migration", "app-1", "")),
        ),
        patch(
            "app.agentive.services.work_items.enqueue_work_item",
            new=capture_enqueue,
        ),
    ):
        result = await enqueue_migration_work(
            published_cp=cp,
            compiled_manifest={"migrations": []},
            actor_id="user-1",
            retry_of_work_item_id="migration-attempt-1",
        )

    assert result["work_item_id"] == "migration-attempt-2"
    assert captured["parent_work_item_id"] == "migration-attempt-1"
    assert captured["idempotency_key"].endswith(":retry:migration-attempt-1")


@pytest.mark.asyncio
async def test_migration_work_scope_uses_attached_track_workspace():
    """Track profiles derive their durable work scope from their owner Track."""
    from app.services.migrations.runner import _migration_work_scope

    cp = _make_stub_cp()
    cp.id = "cp-track-attached"
    cp.workspace_id = None
    cp.app_id = ""
    track = SimpleNamespace(
        workspace_id="ws-track",
        nodes=AsyncMock(return_value=[]),
    )

    with patch("app.models.nodes.Track.find", new=AsyncMock(return_value=[track])):
        assert await _migration_work_scope(cp) == ("ws-track", "", "")


@pytest.mark.asyncio
async def test_migration_work_rejects_a_profile_changed_since_enqueue():
    """A worker cannot run an old migration against a newly edited profile."""
    from app.schemas.agentive.work import WorkError
    from app.services.migrations.runner import execute_migration_work_item

    cp = _make_stub_cp()
    cp.manifest = {"migrations": [{"from_version": "1", "to_version": "2"}]}

    with pytest.raises(WorkError, match="changed after it was queued"):
        await execute_migration_work_item(
            published_cp=cp,
            expected_manifest_fingerprint="outdated",
            actor_id="user-1",
        )
    assert cp.migration_status == "failed"
    cp.save.assert_awaited_once()


# ---------------------------------------------------------------------------
# gather_affected_entries — track walk
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_gather_affected_entries_collects_across_tracks():
    cp = _make_stub_cp()
    track_a = _make_stub_track("ta", [_make_stub_entry("e1"), _make_stub_entry("e2")])
    track_b = _make_stub_track("tb", [_make_stub_entry("e3")])
    with patch(
        "app.services.migrations.runner._affected_tracks",
        new=AsyncMock(return_value=[track_a, track_b]),
    ):
        entries = await gather_affected_entries(cp)
    assert {e.id for e in entries} == {"e1", "e2", "e3"}
