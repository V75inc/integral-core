"""Contract: asset-register default_schedules materialize and respect app pause (WP-07)."""

from __future__ import annotations

from pathlib import Path

import pytest

from app.models.nodes import App, ContentProfile
from app.services.app_lifecycle import install_app, pause_app, resume_app
from app.services.content_profile_loader import load_library_profiles_with_issues
from app.services.routine_task_scheduler import _permission_gate
from app.utils.time import utc_now_iso
from tests.fixtures.workspaces import make_org_workspace

REPO = Path(__file__).resolve().parents[3]
ASSET_APP = REPO / "examples" / "asset-register"


async def _seed_asset_register_library_cp() -> ContentProfile:
    specs, _ = load_library_profiles_with_issues(
        package_paths=[str(ASSET_APP.parent)],
        core_only=False,
        verify_signatures=False,
    )
    spec = next(s for s in specs if s.slug == "asset-register")
    now = utc_now_iso()
    return await ContentProfile.create(
        name=spec.name or "Asset Register",
        scope="app",
        manifest=spec.manifest,
        library_package=True,
        version=spec.version or "1.0.0",
        metadata={
            "slug": spec.slug,
            "bundle_fingerprint": getattr(spec, "bundle_fingerprint", "") or "test-fp",
            "package_class": spec.package_class,
            "bundle_dir": str(spec.bundle_dir) if spec.bundle_dir else str(ASSET_APP),
        },
        created_at=now,
        updated_at=now,
    )


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
    lib = await _seed_asset_register_library_cp()

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
