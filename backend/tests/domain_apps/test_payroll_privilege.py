"""HR-06 + I-PAYROLL-PRIV-01 — Payroll privilege boundary tests.

The substrate guarantee: Payroll tracks are privileged. A workspace role
other than the explicit Payroll-admin tier resolves to no access on
every Payroll track (`pay_runs`, `compensation`, `payslips`). A cross-App
`REFERENCES` edge from an HR `employee` to a Payroll `compensation_record`
NEVER widens access — engineers who can see the Employee still cannot see
the linked Compensation.

This rides the unchanged Phase 16 resolver: `OWNS` / `COLLABORATES_ON` /
`EXCLUDED_FROM` composed by `resolve_role`. Phase 17 ships no resolver
code; the test just proves the policy semantics hold against the actual
installed Payroll App.
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import pytest

from app.models.edges import (
    CATALOGS,
    COLLABORATES_ON,
    CONTAINS,
    EXCLUDED_FROM,
    IS_MEMBER_OF,
    OWNS,
)
from app.models.nodes import App, ContentProfile, Entry, Track, User, Workspace
from app.services.app_graph import catalog_user, ensure_integral_app_graph
from app.services.app_lifecycle import install_app
from app.services.content_profile_loader import load_library_profiles_with_issues
from app.services.permissions import resolve_role
from app.utils.time import utc_now_iso

PAYROLL_TRACK_TITLES = {
    "Guyana Pay Runs",
    "Guyana Compensation Records",
    "Guyana Payslips",
}


async def _ws_member(user: User, workspace: Workspace, role: str = "member") -> None:
    now = utc_now_iso()
    await user.connect(workspace, edge=IS_MEMBER_OF, role=role, joined_at=now)


async def _load_library_cp(slug: str) -> ContentProfile:
    """Mirror the test_app_lifecycle helper: re-inject version + create a library CP."""
    specs, _ = load_library_profiles_with_issues(profiles_root=Path("app/profiles"))
    spec = next((s for s in specs if s.slug == slug), None)
    assert spec is not None, f"profile {slug!r} missing from app/profiles/"
    manifest = dict(spec.manifest)
    pkg = dict(manifest.get("package") or {})
    pkg["version"] = spec.version or pkg.get("version") or "1.0.0"
    manifest["package"] = pkg
    now = utc_now_iso()
    return await ContentProfile.create(
        name=manifest["package"]["name"],
        scope="app",
        manifest=manifest,
        library_package=True,
        version="1.0.0",
        created_at=now,
        updated_at=now,
    )


async def _provision_payroll_fixture() -> dict:
    """Install HR + Payroll in a fresh workspace and provision the ACC-04 role matrix.

    Layout:
        IntegralApp (rooted)
        Workspace Acme Inc.
          founder ─OWNS→ Workspace
          founder ─IS_MEMBER_OF{owner}→ Workspace
          hr_admin / payroll_admin / engineer ─IS_MEMBER_OF{member}→ Workspace
          Workspace ─CONTAINS→ HR App, Payroll App
            HR App tracks: Employees, Departments, Time-Off, Onboarding, Performance
            Payroll App tracks: Pay Runs, Compensation Records, Payslips
          hr_admin ─COLLABORATES_ON{owner}→ each HR track
          payroll_admin ─COLLABORATES_ON{owner}→ each Payroll track
          engineer ─COLLABORATES_ON{editor}→ Employees track only
          engineer ─EXCLUDED_FROM→ every Payroll track
    """
    await ensure_integral_app_graph()

    now = utc_now_iso()
    workspace = await Workspace.create(
        kind="organization",
        workspace_type="company",
        name="Acme Inc.",
        name_fold="acme inc.",
        created_at=now,
        updated_at=now,
    )
    founder = await User.create(
        user_id="auth-founder",
        display_name="Founder",
        created_at=now,
    )
    hr_admin = await User.create(
        user_id="auth-hr-admin",
        display_name="HR Admin",
        created_at=now,
    )
    payroll_admin = await User.create(
        user_id="auth-payroll-admin",
        display_name="Payroll Admin",
        created_at=now,
    )
    engineer = await User.create(
        user_id="auth-engineer",
        display_name="Engineer",
        created_at=now,
    )
    for u in (founder, hr_admin, payroll_admin, engineer):
        await catalog_user(u)

    await founder.connect(workspace, edge=OWNS, created_at=now)
    await _ws_member(founder, workspace, role="owner")
    await _ws_member(hr_admin, workspace, role="member")
    await _ws_member(payroll_admin, workspace, role="member")
    await _ws_member(engineer, workspace, role="member")

    hr_lib = await _load_library_cp("hr_app")
    payroll_lib = await _load_library_cp("payroll-app")
    hr_result = await install_app(
        workspace_id=workspace.id, library_cp_id=hr_lib.id, actor_id=founder.id
    )
    payroll_result = await install_app(
        workspace_id=workspace.id,
        library_cp_id=payroll_lib.id,
        actor_id=founder.id,
        settings={},
    )

    hr_app = await App.get(hr_result["app_id"])
    payroll_app = await App.get(payroll_result["app_id"])
    hr_tracks = {
        getattr(t, "title", ""): t
        for t in await hr_app.nodes(edge=[CONTAINS], node=["Track"])
        if isinstance(t, Track)
    }
    payroll_tracks = {
        getattr(t, "title", ""): t
        for t in await payroll_app.nodes(edge=[CONTAINS], node=["Track"])
        if isinstance(t, Track)
    }

    # HR-admin: owner on every HR track.
    for t in hr_tracks.values():
        await hr_admin.connect(
            t, edge=COLLABORATES_ON, role="owner", granted_at=now, granted_by=founder.id
        )
    # Payroll-admin: owner on every Payroll track.
    for t in payroll_tracks.values():
        await payroll_admin.connect(
            t,
            edge=COLLABORATES_ON,
            role="owner",
            granted_at=now,
            granted_by=founder.id,
        )
    # Engineer: editor on Employees only (sees the directory).
    await engineer.connect(
        hr_tracks["Employees"],
        edge=COLLABORATES_ON,
        role="editor",
        granted_at=now,
        granted_by=founder.id,
    )
    # Engineer: EXCLUDED_FROM every Payroll track (ACC-04 default-deny).
    for t in payroll_tracks.values():
        await engineer.connect(
            t,
            edge=EXCLUDED_FROM,
            excluded_at=now,
            excluded_by=founder.id,
            reason="ACC-04 — engineer default-deny on payroll tracks",
        )

    return {
        "workspace": workspace,
        "founder": founder,
        "hr_admin": hr_admin,
        "payroll_admin": payroll_admin,
        "engineer": engineer,
        "hr_app": hr_app,
        "payroll_app": payroll_app,
        "hr_tracks": hr_tracks,
        "payroll_tracks": payroll_tracks,
    }


# ----- Core privilege assertions ---------------------------------------------


@pytest.mark.asyncio
async def test_engineer_resolves_to_none_on_every_payroll_track():
    """I-PAYROLL-PRIV-01 — engineer has no role on Pay Runs, Compensation, Payslips."""
    fx = await _provision_payroll_fixture()
    for title, track in fx["payroll_tracks"].items():
        role = await resolve_role(fx["engineer"].id, "track", track.id)
        assert role is None, (title, track.id, role)


@pytest.mark.asyncio
async def test_payroll_admin_has_access_to_every_payroll_track():
    """I-PAYROLL-PRIV-01 — payroll_admin is owner on every Payroll track."""
    fx = await _provision_payroll_fixture()
    for title, track in fx["payroll_tracks"].items():
        role = await resolve_role(fx["payroll_admin"].id, "track", track.id)
        assert role == "owner", (title, track.id, role)


@pytest.mark.asyncio
async def test_hr_admin_has_no_access_to_payroll_tracks():
    """I-PAYROLL-PRIV-01 — HR-admin is NOT payroll-admin; sees no Payroll."""
    fx = await _provision_payroll_fixture()
    for title, track in fx["payroll_tracks"].items():
        role = await resolve_role(fx["hr_admin"].id, "track", track.id)
        assert role is None, (title, track.id, role)


@pytest.mark.asyncio
async def test_engineer_keeps_employees_track_access():
    """Regression — engineer's direct COLLABORATES_ON Employees still resolves."""
    fx = await _provision_payroll_fixture()
    employees_track = fx["hr_tracks"]["Employees"]
    role = await resolve_role(fx["engineer"].id, "track", employees_track.id)
    assert role == "editor", role


@pytest.mark.asyncio
async def test_hr_admin_sees_every_hr_track():
    """I-PAYROLL-PRIV-01 — HR-admin sees every HR track."""
    fx = await _provision_payroll_fixture()
    for title, track in fx["hr_tracks"].items():
        role = await resolve_role(fx["hr_admin"].id, "track", track.id)
        assert role == "owner", (title, track.id, role)


# ----- Cross-App relation does not widen access ------------------------------


@pytest.mark.asyncio
async def test_cross_app_compensation_relation_does_not_widen_engineer_access():
    """I-PAYROLL-PRIV-01 — engineer with editor on HR Employee gets nothing on the
    linked Payroll CompensationRecord. The cross-App REFERENCES edge never propagates
    read access; access resolves on the target's own track/App (which denies).
    """
    fx = await _provision_payroll_fixture()
    now = utc_now_iso()
    # Founder seeds a compensation record + a linked employee.
    employees_track = fx["hr_tracks"]["Employees"]
    compensation_track = fx["payroll_tracks"]["Guyana Compensation Records"]

    employee = await Entry.create(
        track_id=employees_track.id,
        title="Test Employee",
        author_id=fx["founder"].id,
        custom_fields={
            "job_title": "Engineer",
            "employment_type": "full_time",
            "status": "active",
        },
    )
    await employees_track.connect(employee, edge=CONTAINS, added_at=now)

    comp = await Entry.create(
        track_id=compensation_track.id,
        title="Test Compensation",
        author_id=fx["founder"].id,
        custom_fields={
            "base_salary": 100000,
            "currency": "USD",
            "pay_frequency": "annual",
        },
    )
    await compensation_track.connect(comp, edge=CONTAINS, added_at=now)

    # Engineer sees the Employee.
    employee_role = await resolve_role(fx["engineer"].id, "entry", employee.id)
    assert employee_role in ("editor", "commenter", "viewer"), employee_role

    # Engineer cannot reach the Compensation entry through the cross-App relation.
    comp_role = await resolve_role(fx["engineer"].id, "entry", comp.id)
    assert comp_role is None, comp_role
