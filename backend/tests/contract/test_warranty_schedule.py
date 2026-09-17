"""Contract: asset-register default_schedules materialize and respect app pause (WP-07)."""

from __future__ import annotations

import asyncio

import pytest

from app.models.nodes import App
from app.services.app_lifecycle import install_app, pause_app, resume_app
from app.services.routine_task_scheduler import _permission_gate, run_scheduler_pass
from app.utils.time import utc_now_iso
from tests.contract.asset_register_helpers import (
    ASSET_APP,
    seed_asset_register_library_cp,
)
from tests.fixtures.workspaces import make_org_workspace


@pytest.mark.contract
@pytest.mark.asyncio
async def test_asset_register_warranty_schedule_materialized(monkeypatch):
    assert ASSET_APP.is_dir()
    monkeypatch.setenv("INTEGRAL_PACKAGE_PATHS", str(ASSET_APP.parent))
    monkeypatch.setenv("INTEGRAL_CORE_ONLY", "0")
    monkeypatch.setattr(
        "app.agentive.services.uplink_registry._scheduler_available",
        lambda: True,
    )

    from app.agentive.nodes import RoutineTask

    ws = await make_org_workspace("ws-warranty-contract")
    actor_id = "u_contract_warranty"
    lib = await seed_asset_register_library_cp()

    installed = await install_app(
        workspace_id=ws.id,
        library_cp_id=lib.id,
        actor_id=actor_id,
        include_seed_data=False,
    )
    app_id = installed["app_id"]

    routines = await RoutineTask.find({"source_app_id": app_id})
    assert len(routines) == 1
    routine = routines[0]
    assert routine.source_schedule_key == "asset_admin:0"
    assert routine.cron == "0 8 * * *"
    assert routine.status == "active"
    assert "review_warranties" in (routine.instruction or "")

    paused = await pause_app(app_id=app_id, actor_id=actor_id)
    assert paused["status"] == "paused"
    routine_after_pause = await RoutineTask.get(routine.id)
    assert routine_after_pause is not None
    assert routine_after_pause.status == "paused"

    gate_reason = await _permission_gate(routine_after_pause)
    assert gate_reason is not None
    assert "not active" in gate_reason

    resumed = await resume_app(app_id=app_id, actor_id=actor_id)
    assert resumed["status"] == "active"
    routine_after_resume = await RoutineTask.get(routine.id)
    assert routine_after_resume is not None
    assert routine_after_resume.status == "active"

    app = await App.get(app_id)
    assert app is not None
    assert app.lifecycle_state == "active"
    assert await _permission_gate(routine_after_resume) is None


@pytest.mark.contract
@pytest.mark.asyncio
async def test_scheduler_pass_dispatches_due_routine_once(monkeypatch):
    assert ASSET_APP.is_dir()
    monkeypatch.setenv("INTEGRAL_PACKAGE_PATHS", str(ASSET_APP.parent))
    monkeypatch.setenv("INTEGRAL_CORE_ONLY", "0")
    monkeypatch.setattr(
        "app.agentive.services.uplink_registry._scheduler_available",
        lambda: True,
    )

    from app.agentive.nodes import RoutineTask
    from app.services import routine_task_scheduler as sched

    ws = await make_org_workspace("ws-warranty-dispatch")
    actor_id = "u_contract_dispatch"
    lib = await seed_asset_register_library_cp()
    installed = await install_app(
        workspace_id=ws.id,
        library_cp_id=lib.id,
        actor_id=actor_id,
        include_seed_data=False,
    )
    routines = await RoutineTask.find({"source_app_id": installed["app_id"]})
    routine = routines[0]
    routine.next_run_at = utc_now_iso()
    await routine.save()

    runs: list[str] = []

    async def _fake_run_one(task_id: str) -> None:
        runs.append(task_id)

    monkeypatch.setattr(sched, "_run_one", _fake_run_one)
    dispatched = await run_scheduler_pass()
    await asyncio.sleep(0.05)
    assert dispatched == 1
    assert runs == [routine.id]

    refreshed = await RoutineTask.get(routine.id)
    assert refreshed is not None
    assert refreshed.next_run_at > routine.next_run_at

    dispatched_again = await run_scheduler_pass()
    await asyncio.sleep(0.05)
    assert dispatched_again == 0
    assert len(runs) == 1
