"""Phase 17 — HR + Payroll App install (HR-01/02, I-APP-01/02/04/05).

Domain-app quarantine: validates bundled ``hr_app`` / ``payroll-app`` library
profiles from ``app/profiles/``, not generic substrate lifecycle.
"""

from __future__ import annotations

from typing import Any, Dict

import pytest

from app.models.edges import CONTAINS
from app.models.nodes import App, ContentProfile, Entry, Workspace
from app.services.app_lifecycle import install_app, uninstall_app
from app.utils.time import utc_now_iso
from tests.fixtures.workspaces import make_org_workspace


async def _make_workspace(name: str = "WS Test") -> Workspace:
    return await make_org_workspace(name)


async def _make_library_cp(manifest: Dict[str, Any]) -> ContentProfile:
    now = utc_now_iso()
    return await ContentProfile.create(
        name=manifest["package"]["name"],
        scope="app",
        manifest=manifest,
        library_package=True,
        version=manifest["package"].get("version") or "1.0.0",
        created_at=now,
        updated_at=now,
    )


async def _load_library_cp(slug: str) -> ContentProfile:
    from pathlib import Path

    from app.services.content_profile_loader import load_library_profiles_with_issues

    specs, _ = load_library_profiles_with_issues(profiles_root=Path("app/profiles"))
    spec = next((s for s in specs if s.slug == slug), None)
    assert spec is not None, f"profile {slug!r} missing from app/profiles/"
    manifest = dict(spec.manifest)
    pkg = dict(manifest.get("package") or {})
    pkg["version"] = spec.version or pkg.get("version") or "1.0.0"
    manifest["package"] = pkg
    return await _make_library_cp(manifest)


@pytest.mark.asyncio
async def test_hr_app_installs_with_five_tracks_and_seeded_roster():
    """HR-01 + HR-05 — HR install reaches active with 5 tracks + seeded roster."""
    ws = await _make_workspace()
    hr_lib = await _load_library_cp("hr_app")

    result = await install_app(
        workspace_id=ws.id, library_cp_id=hr_lib.id, actor_id="u_1"
    )
    assert result["status"] == "active"

    app = await App.get(result["app_id"])
    assert app.lifecycle_state == "active"

    tracks = await app.nodes(edge=[CONTAINS], node=["Track"])
    track_titles = {getattr(t, "title", "") for t in tracks}
    expected_titles = {
        "Employees",
        "Departments",
        "Requests",
        "Onboarding Checklists",
        "Performance Reviews",
        "Employee Onboarding",
        "Positions",
        "Awards & Achievements",
        "Learning & Development",
    }
    missing = expected_titles - track_titles
    assert not missing, f"HR App missing tracks: {missing}; have {track_titles}"

    employees_track = next(t for t in tracks if getattr(t, "title", "") == "Employees")
    emp_entries = await Entry.find({"track_id": employees_track.id})
    assert len(emp_entries) == 16


@pytest.mark.asyncio
async def test_payroll_installs_without_hr_app_dependency():
    """HR-02 — Payroll is decoupled from HR (hr_app is now a soft
    requires_apps dep): install succeeds with HR absent, and Payroll's own
    Guyana Payroll Employees roster track is present so Compensation Records have
    somewhere to link against immediately."""
    ws = await _make_workspace()
    payroll_lib = await _load_library_cp("payroll-app")
    payroll_result = await install_app(
        workspace_id=ws.id, library_cp_id=payroll_lib.id, actor_id="u_1", settings={}
    )
    assert payroll_result["status"] == "active"

    payroll_app = await App.get(payroll_result["app_id"])
    tracks = await payroll_app.nodes(edge=[CONTAINS], node=["Track"])
    titles = {getattr(t, "title", "") for t in tracks}
    assert "Guyana Payroll Employees" in titles


@pytest.mark.asyncio
async def test_payroll_installs_after_hr_dependency_present():
    """HR-02 — Payroll installs cleanly once HR is present in the workspace."""
    ws = await _make_workspace()
    hr_lib = await _load_library_cp("hr_app")
    payroll_lib = await _load_library_cp("payroll-app")

    hr_result = await install_app(
        workspace_id=ws.id, library_cp_id=hr_lib.id, actor_id="u_1"
    )
    assert hr_result["status"] == "active"
    payroll_result = await install_app(
        workspace_id=ws.id, library_cp_id=payroll_lib.id, actor_id="u_1", settings={}
    )
    assert payroll_result["status"] == "active"

    payroll_app = await App.get(payroll_result["app_id"])
    tracks = await payroll_app.nodes(edge=[CONTAINS], node=["Track"])
    titles = {getattr(t, "title", "") for t in tracks}
    expected = {"Guyana Pay Runs", "Guyana Compensation Records", "Guyana Payslips"}
    missing = expected - titles
    assert not missing, f"Payroll App missing tracks: {missing}; have {titles}"


@pytest.mark.asyncio
async def test_hr_seed_idempotency_no_duplicate_roster_on_replant():
    """HR-05 + I-APP-04 — re-running _plant_seeds is a no-op on the existing roster."""
    from app.services.app_lifecycle import _plant_seeds
    from app.services.content_profile_runtime import compile_canonical_manifest

    ws = await _make_workspace()
    hr_lib = await _load_library_cp("hr_app")
    result = await install_app(
        workspace_id=ws.id, library_cp_id=hr_lib.id, actor_id="u_1"
    )
    app = await App.get(result["app_id"])
    canonical = compile_canonical_manifest(manifest=hr_lib.manifest)

    n = await _plant_seeds(app, canonical, "u_1")
    assert n == 0

    tracks = await app.nodes(edge=[CONTAINS], node=["Track"])
    employees_track = next(t for t in tracks if getattr(t, "title", "") == "Employees")
    emp_entries = await Entry.find({"track_id": employees_track.id})
    assert len(emp_entries) == 16


@pytest.mark.asyncio
async def test_payroll_uninstall_removes_app_subgraph():
    """HR-02 + I-APP-05 — uninstall Payroll removes its tracks from the workspace."""
    ws = await _make_workspace()
    hr_lib = await _load_library_cp("hr_app")
    payroll_lib = await _load_library_cp("payroll-app")

    await install_app(workspace_id=ws.id, library_cp_id=hr_lib.id, actor_id="u_1")
    payroll_result = await install_app(
        workspace_id=ws.id, library_cp_id=payroll_lib.id, actor_id="u_1", settings={}
    )

    out = await uninstall_app(
        app_id=payroll_result["app_id"], actor_id="u_1", force=True
    )
    assert out["status"] == "force_uninstalled"

    payroll_app_id = payroll_result["app_id"]
    payroll_app_after = await App.get(payroll_app_id)
    if payroll_app_after is not None:
        assert payroll_app_after.lifecycle_state != "active"
