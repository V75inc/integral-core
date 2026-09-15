"""Real install-into-workspace verification for Guyana Payroll's merged
NIS/PAYE filings surface (formerly the standalone ``payroll_filings``
package — merged into ``payroll-app`` so filings install as part of
installing Payroll itself, not as a separate dependent app).

Exercises the actual ``install_app`` service (the same code path the real
HTTP install endpoint calls), not just manifest compilation.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict

import pytest

from app.models.edges import CONTAINS
from app.models.nodes import App, ContentProfile, Workspace
from app.services.app_lifecycle import install_app
from app.services.content_profile_loader import load_library_profiles_with_issues
from app.services.content_profile_plugins import (
    discover_and_register_plugins,
    reset_discovered_for_tests,
)
from app.utils.time import utc_now_iso
from app.views import content_profile_view_types as view_types
from tests.fixtures.workspaces import make_org_workspace


@pytest.fixture(scope="module", autouse=True)
def _payroll_view_types():
    """See test_payroll_filings_manifests.py — same registration requirement
    applies to installing the profile, not just compiling it."""
    view_types._REGISTRY.pop("editable_table", None)
    view_types._REGISTRY.pop("action_bar", None)
    reset_discovered_for_tests()
    discover_and_register_plugins()
    yield
    view_types._REGISTRY.pop("editable_table", None)
    view_types._REGISTRY.pop("action_bar", None)
    reset_discovered_for_tests()


async def _make_workspace(name: str = "WS Payroll Filings Test") -> Workspace:
    # make_org_workspace (tests/fixtures/workspaces.py) wires a real owner —
    # a bare Workspace.create() here left actor_id="u_1" unresolvable
    # against an ownerless workspace, tripping wire_app_owner's
    # AppOwnerWireError on any install reaching that step (harmless when a
    # test only expects an AppDependencyError before that point, real when
    # it doesn't — same pattern test_app_lifecycle.py already uses).
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
    specs, _ = load_library_profiles_with_issues(profiles_root=Path("app/profiles"))
    spec = next((s for s in specs if s.slug == slug), None)
    assert spec is not None, f"profile {slug!r} missing from app/profiles/"
    manifest = dict(spec.manifest)
    pkg = dict(manifest.get("package") or {})
    pkg["version"] = spec.version or pkg.get("version") or "1.0.0"
    manifest["package"] = pkg
    return await _make_library_cp(manifest)


@pytest.mark.asyncio
async def test_payroll_app_install_succeeds_without_hr_app():
    """requires_apps: hr_app is a soft (optional) dep — Payroll decoupled
    from HR, Compensation Records link against Payroll's own Payroll
    Employees track instead. Install must succeed with hr_app absent; the
    HR-mirroring "Linked HRM Employee" field on Guyana Payroll Employees simply
    won't resolve until the HR App is installed later."""
    ws = await _make_workspace()
    payroll_lib = await _load_library_cp("payroll-app")
    result = await install_app(
        workspace_id=ws.id, library_cp_id=payroll_lib.id, actor_id="u_1", settings={}
    )
    assert result["status"] == "active"

    app = await App.get(result["app_id"])
    tracks = await app.nodes(edge=[CONTAINS], node=["Track"])
    titles = {getattr(t, "title", "") for t in tracks}
    assert "Guyana Payroll Employees" in titles


@pytest.mark.asyncio
async def test_payroll_app_install_creates_filings_tracks_too():
    """Real install, real DB: hr_app -> payroll-app is now the WHOLE
    dependency chain — installing Payroll creates its NIS/PAYE Filings and
    Company Profile tracks in the same install, no separate payroll_filings
    app to install afterward."""
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

    app = await App.get(payroll_result["app_id"])
    assert app.lifecycle_state == "active"

    # Real install, real graph: the merged package's full track set,
    # including "Filings" (nis_schedule + paye_filing) and "Company
    # Profile" — formerly a separate payroll_filings app's tracks, now
    # created by installing Payroll alone.
    tracks = await app.nodes(edge=[CONTAINS], node=["Track"])
    titles = {getattr(t, "title", "") for t in tracks}
    assert {
        "Filings",
        "Guyana Company Profile",
        "Guyana Statutory Rates",
        "Guyana Pay Runs",
    } <= titles

    filings_track = next(t for t in tracks if getattr(t, "title", "") == "Filings")
    assert filings_track is not None
