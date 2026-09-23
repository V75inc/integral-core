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

    # A process restart drops in-memory scheduler slots. The persisted
    # next_run_at still blocks a second dispatch of the same window.
    sched._in_flight.clear()
    sched._run_slots = None
    sched._run_slots_loop = None
    dispatched_after_restart = await run_scheduler_pass()
    await asyncio.sleep(0.05)
    assert dispatched_after_restart == 0
    assert len(runs) == 1


@pytest.mark.contract
@pytest.mark.asyncio
async def test_warranty_review_posts_one_notice_per_window():
    import importlib
    import sys
    from datetime import date, timedelta
    from types import SimpleNamespace

    from app.models.nodes import Notification, User
    from app.services.app_operations.context import OperationContext
    from tests.contract.asset_register_helpers import ASSET_APP

    bundle_root = str(ASSET_APP)
    sdk_root = str(ASSET_APP.parents[1] / "sdk" / "python")
    for module_name in list(sys.modules):
        if module_name == "tools" or module_name.startswith("tools."):
            del sys.modules[module_name]
    for package_root in (sdk_root, bundle_root):
        if package_root not in sys.path:
            sys.path.insert(0, package_root)
    asset_tools = importlib.import_module("tools.assets")

    owner = await User.create(display_name="warranty notice owner")
    today = date.today()
    asset = SimpleNamespace(
        id="asset-1",
        title="Pump",
        custom_fields={
            "asset_tag": "P-1",
            "warranty_end": (today + timedelta(days=5)).isoformat(),
        },
    )

    class _Ctx(OperationContext):
        async def find_entries_in_track_type(self, track_type, entry_type=None):
            return [asset]

    ctx = _Ctx(
        user_id=owner.id,
        workspace_id="ws-warranty-notice",
        scope="ws-warranty-notice",
        bundle_slug="asset-register",
        app_id="app-warranty",
        operation_key="review_warranties",
        idempotency_key="2026-09-23T08:00:00#0:0:",
    )
    first = await asset_tools.review_warranties({"horizon_days": 30}, ctx)
    second = await asset_tools.review_warranties({"horizon_days": 30}, ctx)
    assert first["count"] == 1
    assert first["notification_id"]
    assert second["notification_id"] == first["notification_id"]
    notes = await Notification.find({"user_id": owner.id})
    matching = [
        note
        for note in notes or []
        if (getattr(note, "metadata", None) or {}).get("dedupe_key")
        == "review_warranties:2026-09-23T08:00:00#0:0:"
    ]
    assert len(matching) == 1
