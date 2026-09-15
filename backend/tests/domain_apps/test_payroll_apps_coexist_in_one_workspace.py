"""Proves Guyana Payroll (``payroll-app``) and Aruba Payroll
(``aruba-payroll``) can be installed in the SAME workspace without cross-
contamination.

Both apps' substrate reads (``find_entries_in_track_type`` /
``find_track_id_by_title``) match by TRACK TITLE, workspace-wide — NOT
scoped per installed App (see ``ToolContext._tracks_by_title`` in
``app/services/hooks/registry.py``). Early Aruba Payroll drafts reused
Guyana Payroll's THEN-unprefixed track titles ("Pay Runs", "Compensation
Records", "Payslips", "Statutory Rates", "Company Profile") verbatim —
installed together, each app's tools would have silently pulled in the
OTHER app's records (e.g. Aruba's wage-tax calc reading Guyana's
``paye_band`` pages, since its field-shape classifier happens to
alias-match them; Aruba's payslip generator iterating Guyana's
Compensation Records). Fixed by giving each payroll app's own tracks a
country-prefixed title ("Guyana " / "Aruba "), including each app's own
Payroll Employees roster — Compensation Records link against that, never
hr_app's Employees track directly, now that Payroll is decoupled from HR
(see ``tools/hrm_sync.py``). hr_app's shared ``Employees`` track is still
the one intentional workspace-wide exception: a hire made there mirrors
into EVERY installed payroll app's own roster, each keyed to its own
country-prefixed track.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from app.models.edges import CONTAINS, IS_MEMBER_OF
from app.models.nodes import App, ContentProfile, Track, User, Workspace
from app.services.app_graph import catalog_user
from app.services.app_lifecycle import install_app
from app.services.content_profile_loader import load_library_profiles_with_issues
from app.services.content_profile_plugins import (
    discover_and_register_plugins,
    reset_discovered_for_tests,
)
from app.services.hooks.registry import ToolContext
from app.utils.time import utc_now_iso


@pytest.fixture(scope="module", autouse=True)
def _view_types():
    reset_discovered_for_tests()
    discover_and_register_plugins()
    yield
    reset_discovered_for_tests()


async def _make_workspace() -> tuple[Workspace, str]:
    now = utc_now_iso()
    ws = await Workspace.create(
        kind="organization",
        workspace_type="company",
        name="Two Country Payroll Test",
        name_fold="two country payroll test",
        created_at=now,
        updated_at=now,
    )
    owner = await User.create(name="Two Country Payroll Test owner")
    await owner.connect(ws, edge=IS_MEMBER_OF, role="owner", joined_at=now)
    return ws, owner.id


async def _load_library_cp(slug: str) -> ContentProfile:
    specs, _ = load_library_profiles_with_issues(profiles_root=Path("app/profiles"))
    spec = next(s for s in specs if s.slug == slug)
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
        version=manifest["package"]["version"],
        created_at=now,
        updated_at=now,
    )


async def _track_by_title(app_node: App, title: str) -> Track:
    tracks = await app_node.nodes(edge=[CONTAINS], node=["Track"])
    return next(t for t in tracks if getattr(t, "title", "") == title)


async def _mirror_id(
    ctx: ToolContext, roster_track_title: str, hrm_employee_id: str
) -> str:
    """Compensation Records / Payroll Register lines now link against each
    payroll app's own Payroll Employees roster (decoupled from hr_app — see
    ``tools/hrm_sync.py``), never hr_app's Employee id directly. Each
    app's ``mirror_employee_from_hrm`` hook creates that roster's mirror
    synchronously off the same ``entry.create`` this test's employee
    creation fires, so it's always present by the time this is called."""
    for rec in await ctx.find_entries_in_track_type(roster_track_title):
        if (rec.custom_fields or {}).get("hrm_source_employee_id") == hrm_employee_id:
            return rec.id
    raise AssertionError(
        f"no {roster_track_title!r} mirror found for hr_app employee {hrm_employee_id!r}"
    )


@pytest.mark.asyncio
async def test_no_track_title_collisions_between_the_two_payroll_apps():
    ws, owner_id = await _make_workspace()
    hr_lib = await _load_library_cp("hr_app")
    guyana_lib = await _load_library_cp("payroll-app")
    aruba_lib = await _load_library_cp("aruba-payroll")
    await install_app(workspace_id=ws.id, library_cp_id=hr_lib.id, actor_id=owner_id)
    guyana_result = await install_app(
        workspace_id=ws.id, library_cp_id=guyana_lib.id, actor_id=owner_id, settings={}
    )
    aruba_result = await install_app(
        workspace_id=ws.id, library_cp_id=aruba_lib.id, actor_id=owner_id, settings={}
    )
    guyana_app = await App.get(guyana_result["app_id"])
    aruba_app = await App.get(aruba_result["app_id"])

    guyana_titles = {
        getattr(t, "title", "")
        for t in await guyana_app.nodes(edge=[CONTAINS], node=["Track"])
    }
    aruba_titles = {
        getattr(t, "title", "")
        for t in await aruba_app.nodes(edge=[CONTAINS], node=["Track"])
    }
    # The shared HR "Employees" roster is the one deliberate exception —
    # every OWN track of each payroll app must be distinctly named.
    assert not (guyana_titles & aruba_titles)


@pytest.mark.asyncio
async def test_generating_aruba_payslips_does_not_touch_guyana_records():
    ws, owner_id = await _make_workspace()
    hr_lib = await _load_library_cp("hr_app")
    guyana_lib = await _load_library_cp("payroll-app")
    aruba_lib = await _load_library_cp("aruba-payroll")
    hr_result = await install_app(
        workspace_id=ws.id, library_cp_id=hr_lib.id, actor_id=owner_id
    )
    guyana_result = await install_app(
        workspace_id=ws.id, library_cp_id=guyana_lib.id, actor_id=owner_id, settings={}
    )
    aruba_result = await install_app(
        workspace_id=ws.id, library_cp_id=aruba_lib.id, actor_id=owner_id, settings={}
    )
    hr_app = await App.get(hr_result["app_id"])
    guyana_app = await App.get(guyana_result["app_id"])
    aruba_app = await App.get(aruba_result["app_id"])

    ctx = ToolContext(user_id=owner_id, workspace_id=ws.id, scope="test")

    member_user = await User.create(user_id="u_member", display_name="Member Account")
    await catalog_user(member_user)
    await member_user.connect(
        ws, edge=IS_MEMBER_OF, role="member", joined_at=utc_now_iso()
    )

    employees_track = await _track_by_title(hr_app, "Employees")
    guyana_employee = await ctx.create_entry(
        track_id=employees_track.id,
        entry_type_key="employee",
        title="Georgetown Employee",
        custom_fields={
            "status": "active",
            "job_title": "Clerk",
            "member": member_user.id,
        },
    )
    aruba_employee = await ctx.create_entry(
        track_id=employees_track.id,
        entry_type_key="employee",
        title="Oranjestad Employee",
        custom_fields={
            "status": "active",
            "job_title": "Clerk",
            "member": member_user.id,
        },
    )
    assert guyana_employee is not None and aruba_employee is not None

    # Both apps' entry.create hooks should have auto-provisioned a starting
    # Compensation Record for BOTH employees, into each app's own track.
    guyana_comp_track = await _track_by_title(guyana_app, "Guyana Compensation Records")
    aruba_comp_track = await _track_by_title(aruba_app, "Aruba Compensation Records")
    guyana_comps = await ctx.find_entries({"track_id": guyana_comp_track.id})
    aruba_comps = await ctx.find_entries({"track_id": aruba_comp_track.id})
    assert len(guyana_comps) == 2  # one per employee, from Guyana's own hook
    assert len(aruba_comps) == 2  # one per employee, from Aruba's own hook

    # Fund only the Aruba employee's Aruba comp record — the Guyana comp
    # records stay at their $0 default and must NOT be picked up. Comp
    # records link against each app's own Payroll Employees mirror, not the
    # raw hr_app employee id — resolve it first.
    aruba_employee_mirror_id = await _mirror_id(
        ctx, "Aruba Payroll Employees", aruba_employee.id
    )
    aruba_comp = next(
        c
        for c in aruba_comps
        if c.custom_fields.get("employee") == aruba_employee_mirror_id
    )
    await ctx.update_entry_fields(
        aruba_comp.id,
        {
            "base_salary": 3367.00,
            "pay_frequency": "monthly",
            "effective_date": "2026-01-01",
        },
    )

    aruba_pay_runs_track = await _track_by_title(aruba_app, "Aruba Pay Runs")
    pay_run = await ctx.create_entry(
        track_id=aruba_pay_runs_track.id,
        entry_type_key="pay_run",
        title="2026-01 Aruba pay run",
        custom_fields={
            "frequency": "monthly",
            "period_start": "2026-01-01",
            "period_end": "2026-01-31",
            "status": "draft",
        },
    )
    assert pay_run is not None

    from app.profiles.aruba_payroll.tools.generate_payslips import (  # type: ignore[import]
        generate_payslips_for_pay_run,
    )
    from app.profiles.aruba_payroll.tools.pay_run_line_calc import (  # type: ignore[import]
        populate_pay_run_lines_for_employees,
    )

    # Only the Aruba employee gets a Payroll Register line — the Guyana
    # employee, though on the same shared "employees" roster, is never
    # touched by this Aruba-only wizard population call.
    populate_result = await populate_pay_run_lines_for_employees(
        {"entry_id": pay_run.id, "employee_ids": [aruba_employee_mirror_id]}, ctx
    )
    assert populate_result["ok"], populate_result

    result = await generate_payslips_for_pay_run({"entry_id": pay_run.id}, ctx)
    assert result["ok"], result
    # Exactly the one funded Aruba employee — NOT the Guyana employee too
    # (which would happen if "employees" roster scoping were broken, though
    # that track is deliberately shared) and not a bogus $0 payslip for
    # anyone still on their $0 default comp record.
    assert result["generated_count"] == 1

    aruba_payslips_track = await _track_by_title(aruba_app, "Aruba Payslips")
    aruba_payslips = await ctx.find_entries({"track_id": aruba_payslips_track.id})
    assert len(aruba_payslips) == 1
    assert aruba_payslips[0].custom_fields["employee"] == aruba_employee_mirror_id

    # Guyana's own Payslips track is untouched — no Aruba run leaked into it.
    guyana_payslips_track = await _track_by_title(guyana_app, "Guyana Payslips")
    guyana_payslips = await ctx.find_entries({"track_id": guyana_payslips_track.id})
    assert guyana_payslips == []

    # And Guyana's own Statutory Rates weren't misread as Aruba's — the
    # generated payslip's numbers must match Aruba's real rates (10.5%
    # AOV employer), not anything from Guyana's paye_band/nis_contribution_cap.
    cf = aruba_payslips[0].custom_fields
    assert cf["aov_employer"] == 342.93
