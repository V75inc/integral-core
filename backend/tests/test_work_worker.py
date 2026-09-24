"""Static work worker handlers and supervised heartbeat (Task 6)."""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.agentive.services import work_items, work_worker
from app.agentive.work_models import WorkItem
from app.schemas.agentive.work import WorkError


@pytest.fixture(autouse=True)
def _clear_handlers():
    work_worker.clear_test_handlers()
    work_worker.set_crash_point(None)
    yield
    work_worker.clear_test_handlers()
    work_worker.set_crash_point(None)


@pytest.mark.asyncio
async def test_unknown_kind_fails_permanently() -> None:
    item = await work_items.enqueue_work_item(
        kind="capability",
        origin="http",
        principal_id="ww-u-1",
        workspace_id="ww-ws-1",
        idempotency_key="ww-unk",
        input_payload={},
    )
    claimed = await work_items.claim_due_candidate(
        worker_id="w1", work_item_id=item.work_item_id, lease_seconds=30
    )
    assert claimed is not None
    claimed.kind = "not_a_kind"
    await claimed.save()
    done = await work_worker.execute_claimed_work(claimed, worker_id="w1")
    assert done.status == "failed"
    assert done.failure is not None
    assert done.failure["code"] == "work.unknown_kind"


@pytest.mark.asyncio
async def test_capability_requires_principal_and_workspace() -> None:
    item = await work_items.enqueue_work_item(
        kind="capability",
        origin="http",
        principal_id="ww-u-2",
        workspace_id="ww-ws-2",
        idempotency_key="ww-scope",
        input_payload={"capability_key": "integral_list_entries"},
    )
    claimed = await work_items.claim_due_candidate(
        worker_id="w1", work_item_id=item.work_item_id, lease_seconds=30
    )
    assert claimed is not None
    claimed.principal_id = ""
    await claimed.save()
    done = await work_worker.execute_claimed_work(claimed, worker_id="w1")
    assert done.status == "failed"
    assert done.failure["code"] == "work.policy_denied"


@pytest.mark.asyncio
async def test_routine_turn_runs_test_handler_under_lease() -> None:
    seen = {"n": 0}

    async def _handler(item, ctx) -> None:
        seen["n"] += 1
        assert ctx.lease_token == item.lease_token

    item = await work_items.enqueue_work_item(
        kind="routine_turn",
        origin="scheduler",
        principal_id="ww-u-3",
        workspace_id="ww-ws-3",
        idempotency_key="ww-rt",
        input_payload={"routine_id": "r1"},
    )
    work_worker.register_test_handler(item.work_item_id, _handler)
    done = await work_worker.process_one_due_item(
        worker_id="w1", work_item_id=item.work_item_id, lease_seconds=30
    )
    assert done is not None
    assert done.status == "succeeded"
    assert seen["n"] == 1


@pytest.mark.asyncio
async def test_supervised_heartbeat_during_long_handler(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    heartbeats = {"n": 0}
    real_hb = work_items.heartbeat_lease

    async def _counting_hb(*args, **kwargs):
        heartbeats["n"] += 1
        return await real_hb(*args, **kwargs)

    async def _slow(item, ctx) -> None:
        await asyncio.sleep(0.35)

    item = await work_items.enqueue_work_item(
        kind="routine_turn",
        origin="scheduler",
        principal_id="ww-u-4",
        workspace_id="ww-ws-4",
        idempotency_key="ww-hb",
        input_payload={},
    )
    work_worker.register_test_handler(item.work_item_id, _slow)
    # lease=0.6 → heartbeat interval 0.2; sleep 0.35 → at least one beat
    monkeypatch.setattr(work_items, "heartbeat_lease", _counting_hb)
    done = await work_worker.process_one_due_item(
        worker_id="w1",
        work_item_id=item.work_item_id,
        lease_seconds=0.6,
    )
    assert done is not None
    assert done.status == "succeeded"
    assert heartbeats["n"] >= 1


@pytest.mark.asyncio
async def test_lease_loss_blocks_completion() -> None:
    async def _steal(item, ctx) -> None:
        await work_items.force_expire_lease_for_tests(item.work_item_id)
        await work_items.reclaim_expired_lease(
            item.work_item_id, worker_id="thief", lease_seconds=30
        )

    item = await work_items.enqueue_work_item(
        kind="routine_turn",
        origin="scheduler",
        principal_id="ww-u-5",
        workspace_id="ww-ws-5",
        idempotency_key="ww-steal",
        input_payload={},
    )
    work_worker.register_test_handler(item.work_item_id, _steal)
    claimed = await work_items.claim_due_candidate(
        worker_id="victim", work_item_id=item.work_item_id, lease_seconds=30
    )
    assert claimed is not None
    with pytest.raises(WorkError) as exc:
        await work_worker.execute_claimed_work(
            claimed, worker_id="victim", lease_seconds=30
        )
    assert exc.value.code == "work.lease_lost"
    loaded = await WorkItem.get(f"o.WorkItem.{item.work_item_id}")
    assert loaded is not None
    assert loaded.status == "running"
    assert loaded.lease_owner == "thief"


@pytest.mark.asyncio
async def test_migration_work_runs_through_the_leased_worker() -> None:
    """A schema migration is no longer an untracked event-loop task."""
    item = await work_items.enqueue_work_item(
        kind="migration",
        origin="operational_model",
        principal_id="ww-migration-user",
        workspace_id="ww-migration-workspace",
        idempotency_key="ww-migration",
        input_payload={
            "operational_model_id": "n.OperationalModel.migration",
            "manifest_fingerprint": "manifest-fingerprint",
        },
    )
    profile = MagicMock()
    profile.workspace_id = "ww-migration-workspace"
    profile.app_id = ""

    with (
        patch(
            "app.models.nodes.OperationalModel.get",
            new=AsyncMock(return_value=profile),
        ),
        patch(
            "app.services.migrations.runner.execute_migration_work_item",
            new=AsyncMock(return_value={"status": "complete"}),
        ) as execute,
        patch(
            "app.services.migrations.runner.resolve_migration_work_scope",
            new=AsyncMock(return_value=("ww-migration-workspace", "", "")),
        ),
    ):
        done = await work_worker.process_one_due_item(
            worker_id="migration-worker",
            work_item_id=item.work_item_id,
            lease_seconds=30,
        )

    assert done is not None
    assert done.status == "succeeded"
    assert done.result_refs == ["operational_model:n.OperationalModel.migration"]
    execute.assert_awaited_once_with(
        published_cp=profile,
        expected_manifest_fingerprint="manifest-fingerprint",
        actor_id="ww-migration-user",
    )


@pytest.mark.asyncio
async def test_migration_work_uses_graph_scope_for_track_attached_profile() -> None:
    """A graph-attached profile may omit the denormalized workspace cache."""
    item = await work_items.enqueue_work_item(
        kind="migration",
        origin="operational_model",
        principal_id="ww-track-profile-user",
        workspace_id="ww-track-profile-workspace",
        idempotency_key="ww-track-profile-migration",
        input_payload={
            "operational_model_id": "n.OperationalModel.track-profile",
            "manifest_fingerprint": "manifest-fingerprint",
        },
    )
    profile = MagicMock()
    profile.workspace_id = ""
    profile.app_id = ""

    with (
        patch(
            "app.models.nodes.OperationalModel.get", new=AsyncMock(return_value=profile)
        ),
        patch(
            "app.services.migrations.runner.resolve_migration_work_scope",
            new=AsyncMock(return_value=("ww-track-profile-workspace", "", "")),
        ),
        patch(
            "app.services.migrations.runner.execute_migration_work_item",
            new=AsyncMock(return_value={"status": "complete"}),
        ) as execute,
    ):
        done = await work_worker.process_one_due_item(
            worker_id="migration-worker",
            work_item_id=item.work_item_id,
            lease_seconds=30,
        )

    assert done is not None
    assert done.status == "succeeded"
    execute.assert_awaited_once()


@pytest.mark.asyncio
async def test_failed_migration_records_recovery_obligation() -> None:
    """Partial migration outcomes remain durable and actionable."""
    item = await work_items.enqueue_work_item(
        kind="migration",
        origin="operational_model",
        principal_id="ww-migration-failure-user",
        workspace_id="ww-migration-failure-workspace",
        idempotency_key="ww-migration-failure",
        input_payload={
            "operational_model_id": "n.OperationalModel.failed-migration",
            "manifest_fingerprint": "manifest-fingerprint",
        },
    )
    profile = MagicMock()
    profile.workspace_id = "ww-migration-failure-workspace"
    profile.app_id = ""

    with (
        patch(
            "app.models.nodes.OperationalModel.get",
            new=AsyncMock(return_value=profile),
        ),
        patch(
            "app.services.migrations.runner.execute_migration_work_item",
            new=AsyncMock(return_value={"status": "failed"}),
        ),
    ):
        done = await work_worker.process_one_due_item(
            worker_id="migration-worker",
            work_item_id=item.work_item_id,
            lease_seconds=30,
        )

    assert done is not None
    assert done.status == "failed"
    assert done.failure is not None
    assert done.failure["code"] == "work.migration_failed"
    assert done.remaining_obligations == [
        {
            "kind": "migration_recovery",
            "operational_model_id": "n.OperationalModel.failed-migration",
            "explanation": "One or more migration targets failed; inspect and retry the migration.",
        }
    ]


@pytest.mark.asyncio
async def test_lifecycle_install_runs_through_the_leased_worker() -> None:
    item = await work_items.enqueue_work_item(
        kind="app_lifecycle",
        origin="app_lifecycle",
        principal_id="ww-lifecycle-user",
        workspace_id="ww-lifecycle-workspace",
        idempotency_key="ww-lifecycle-install",
        input_payload={
            "action": "install",
            "library_cp_id": "n.OperationalModel.package",
        },
    )
    with patch(
        "app.services.app_lifecycle.install_app",
        new=AsyncMock(return_value={"status": "active", "app_id": "n.App.installed"}),
    ) as install:
        done = await work_worker.process_one_due_item(
            worker_id="lifecycle-worker",
            work_item_id=item.work_item_id,
            lease_seconds=30,
        )
    assert done is not None and done.status == "succeeded"
    assert done.result_refs == [
        "app_lifecycle:install",
        "app:n.App.installed",
    ]
    install.assert_awaited_once()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("action", "payload", "service_name", "expected_kwargs"),
    [
        (
            "upgrade",
            {"version": "2.0.0"},
            "update_app_from_library",
            {
                "app_id": "ww-lifecycle-app",
                "actor_id": "ww-lifecycle-user",
                "version": "2.0.0",
            },
        ),
        (
            "pause",
            {},
            "pause_app",
            {"app_id": "ww-lifecycle-app", "actor_id": "ww-lifecycle-user"},
        ),
        (
            "resume",
            {},
            "resume_app",
            {"app_id": "ww-lifecycle-app", "actor_id": "ww-lifecycle-user"},
        ),
        (
            "uninstall",
            {"archive": False},
            "uninstall_app",
            {
                "app_id": "ww-lifecycle-app",
                "actor_id": "ww-lifecycle-user",
                "archive": False,
            },
        ),
        (
            "finalize_install",
            {"install_token": "install-token", "settings": {"region": "GY"}},
            "finalize_install",
            {
                "app_id": "ww-lifecycle-app",
                "actor_id": "ww-lifecycle-user",
                "install_token": "install-token",
                "settings": {"region": "GY"},
            },
        ),
    ],
)
async def test_lifecycle_actions_run_only_through_the_leased_worker(
    action: str,
    payload: dict,
    service_name: str,
    expected_kwargs: dict,
) -> None:
    """Every App lifecycle transition uses the one recoverable worker path."""
    # The definition binding is independently tested by work_items. This
    # dispatch test isolates the worker's action routing with an already-bound
    # durable item, exactly as it would receive after enqueue.
    with patch(
        "app.agentive.services.work_items.resolve_active_definition_binding",
        new=AsyncMock(return_value="n.ApplicationDefinition.lifecycle"),
    ):
        item = await work_items.enqueue_work_item(
            kind="app_lifecycle",
            origin="app_lifecycle",
            principal_id="ww-lifecycle-user",
            workspace_id="ww-lifecycle-workspace",
            app_id="ww-lifecycle-app",
            idempotency_key=f"ww-lifecycle-{action}",
            input_payload={"action": action, **payload},
        )
    handler = AsyncMock(return_value={"app_id": "ww-lifecycle-app"})
    with patch(f"app.services.app_lifecycle.{service_name}", new=handler):
        done = await work_worker.process_one_due_item(
            worker_id="lifecycle-worker",
            work_item_id=item.work_item_id,
            lease_seconds=30,
        )

    assert done is not None and done.status == "succeeded"
    assert done.result_refs == [
        f"app_lifecycle:{action}",
        "app:ww-lifecycle-app",
    ]
    handler.assert_awaited_once_with(**expected_kwargs)


@pytest.mark.asyncio
async def test_interrupted_lifecycle_work_is_reclaimed_and_completes_once() -> None:
    """A restart reclaims an install rather than minting a second App effect."""
    from app.agentive.services.work_recovery import run_recovery_pass

    with patch(
        "app.agentive.services.work_items.resolve_active_definition_binding",
        new=AsyncMock(return_value="n.ApplicationDefinition.lifecycle"),
    ):
        item = await work_items.enqueue_work_item(
            kind="app_lifecycle",
            origin="app_lifecycle",
            principal_id="ww-recovery-user",
            workspace_id="ww-recovery-workspace",
            app_id="ww-recovery-app",
            idempotency_key="ww-recovery-pause",
            input_payload={"action": "pause"},
        )
    claimed = await work_items.claim_due_candidate(
        worker_id="crashed-worker", work_item_id=item.work_item_id, lease_seconds=30
    )
    assert claimed is not None and claimed.status == "running"
    claimed.lease_expires_at = "2000-01-01T00:00:00+00:00"
    await claimed.save()

    pause = AsyncMock(return_value={"app_id": "ww-recovery-app"})
    with patch("app.services.app_lifecycle.pause_app", new=pause):
        recovery = await run_recovery_pass(reclaim_worker_id="restart-recovery")

    assert recovery.reclaimed == 1
    pause.assert_awaited_once_with(
        app_id="ww-recovery-app", actor_id="ww-recovery-user"
    )
    done = await WorkItem.get(f"o.WorkItem.{item.work_item_id}")
    assert done is not None and done.status == "succeeded"


@pytest.mark.asyncio
async def test_uninstall_blocked_terminalizes_failed() -> None:
    """AppUninstallBlockedError must fail the WorkItem, not leave it running."""
    from app.exceptions import AppUninstallBlockedError

    with patch(
        "app.agentive.services.work_items.resolve_active_definition_binding",
        new=AsyncMock(return_value="n.ApplicationDefinition.lifecycle"),
    ):
        item = await work_items.enqueue_work_item(
            kind="app_lifecycle",
            origin="app_lifecycle",
            principal_id="ww-block-user",
            workspace_id="ww-block-workspace",
            app_id="ww-block-app",
            idempotency_key="ww-uninstall-blocked",
            input_payload={"action": "uninstall", "archive": True},
        )
    claimed = await work_items.claim_due_candidate(
        worker_id="w1", work_item_id=item.work_item_id, lease_seconds=30
    )
    assert claimed is not None

    blocked = AppUninstallBlockedError(
        message="App uninstall blocked — 1 dependent App(s)",
        details={"blocking_dependents": [{"app_id": "dep", "app_name": "Sales"}]},
    )
    with patch(
        "app.services.app_lifecycle.uninstall_app",
        new=AsyncMock(side_effect=blocked),
    ):
        done = await work_worker.execute_claimed_work(claimed, worker_id="w1")

    assert done.status == "failed"
    assert done.failure is not None
    assert done.failure["code"] == "app_uninstall_blocked"
    assert done.failure.get("retryable") is False
