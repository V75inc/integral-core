"""Proves BVI Payroll (``bvi-payroll``) can be installed alongside BOTH
Guyana Payroll (``payroll-app``) and Aruba Payroll (``aruba-payroll``) in
the SAME workspace without cross-contamination — the three-country version
of ``test_payroll_apps_coexist_in_one_workspace.py``.

All three apps' substrate reads (``find_entries_in_track_type`` /
``find_track_id_by_title``) match by TRACK TITLE, workspace-wide — NOT
scoped per installed App (see ``ToolContext._tracks_by_title`` in
``app/services/hooks/registry.py``). Every payroll app's own tracks and
tool keys MUST be country-prefixed (see
docs/backend/payroll-apps-design.md's "The collision problem"), including
each app's own Payroll Employees roster — Compensation Records link
against that, never hr_app's Employees track directly, now that Payroll is
decoupled from HR (see ``tools/hrm_sync.py``). hr_app's shared
``Employees`` track is still the one intentional exception: a hire made
there mirrors into EVERY installed payroll app's own roster.
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
        name="Three Country Payroll Test",
        name_fold="three country payroll test",
        created_at=now,
        updated_at=now,
    )
    owner = await User.create(name="Three Country Payroll Test owner")
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
    """See test_payroll_apps_coexist_in_one_workspace.py's ``_mirror_id`` —
    same helper, three-app version."""
    for rec in await ctx.find_entries_in_track_type(roster_track_title):
        if (rec.custom_fields or {}).get("hrm_source_employee_id") == hrm_employee_id:
            return rec.id
    raise AssertionError(
        f"no {roster_track_title!r} mirror found for hr_app employee {hrm_employee_id!r}"
    )


async def _install_all_three(ws: Workspace, owner_id: str):
    hr_lib = await _load_library_cp("hr_app")
    guyana_lib = await _load_library_cp("payroll-app")
    aruba_lib = await _load_library_cp("aruba-payroll")
    bvi_lib = await _load_library_cp("bvi-payroll")
    await install_app(workspace_id=ws.id, library_cp_id=hr_lib.id, actor_id=owner_id)
    guyana_result = await install_app(
        workspace_id=ws.id, library_cp_id=guyana_lib.id, actor_id=owner_id, settings={}
    )
    aruba_result = await install_app(
        workspace_id=ws.id, library_cp_id=aruba_lib.id, actor_id=owner_id, settings={}
    )
    bvi_result = await install_app(
        workspace_id=ws.id, library_cp_id=bvi_lib.id, actor_id=owner_id, settings={}
    )
    guyana_app = await App.get(guyana_result["app_id"])
    aruba_app = await App.get(aruba_result["app_id"])
    bvi_app = await App.get(bvi_result["app_id"])
    return guyana_app, aruba_app, bvi_app


@pytest.mark.asyncio
async def test_no_track_title_collisions_across_all_three_payroll_apps():
    ws, owner_id = await _make_workspace()
    guyana_app, aruba_app, bvi_app = await _install_all_three(ws, owner_id)

    guyana_titles = {
        getattr(t, "title", "")
        for t in await guyana_app.nodes(edge=[CONTAINS], node=["Track"])
    }
    aruba_titles = {
        getattr(t, "title", "")
        for t in await aruba_app.nodes(edge=[CONTAINS], node=["Track"])
    }
    bvi_titles = {
        getattr(t, "title", "")
        for t in await bvi_app.nodes(edge=[CONTAINS], node=["Track"])
    }
    # The shared HR "Employees" roster is the one deliberate exception —
    # every OWN track of each payroll app must be distinctly named across
    # all three apps, pairwise.
    assert not (guyana_titles & aruba_titles)
    assert not (guyana_titles & bvi_titles)
    assert not (aruba_titles & bvi_titles)

    # And BVI's own tracks are exactly the expected country-prefixed set.
    assert {
        "BVI Pay Runs",
        "BVI Settings",
        "BVI Compensation Records",
        "BVI Payslips",
    } <= bvi_titles


@pytest.mark.asyncio
async def test_generating_bvi_payslips_does_not_touch_guyana_or_aruba_records():
    ws, owner_id = await _make_workspace()
    guyana_app, aruba_app, bvi_app = await _install_all_three(ws, owner_id)

    ctx = ToolContext(user_id=owner_id, workspace_id=ws.id, scope="test")

    member_user = await User.create(user_id="u_member", display_name="Member Account")
    await catalog_user(member_user)
    await member_user.connect(
        ws, edge=IS_MEMBER_OF, role="member", joined_at=utc_now_iso()
    )

    # Resolve the shared Employees track via any of the three apps' HR
    # dependency graph — track-title lookup is workspace-wide, so any app
    # node works to resolve it.
    employees_track_id = await ctx.find_track_id_by_title("Employees")
    assert employees_track_id

    guyana_employee = await ctx.create_entry(
        track_id=employees_track_id,
        entry_type_key="employee",
        title="Georgetown Employee",
        custom_fields={
            "status": "active",
            "job_title": "Clerk",
            "member": member_user.id,
        },
    )
    aruba_employee = await ctx.create_entry(
        track_id=employees_track_id,
        entry_type_key="employee",
        title="Oranjestad Employee",
        custom_fields={
            "status": "active",
            "job_title": "Clerk",
            "member": member_user.id,
        },
    )
    bvi_employee = await ctx.create_entry(
        track_id=employees_track_id,
        entry_type_key="employee",
        title="Road Town Employee",
        custom_fields={
            "status": "active",
            "job_title": "Clerk",
            "member": member_user.id,
        },
    )
    assert guyana_employee and aruba_employee and bvi_employee

    # Every payroll app's own entry.create hook should have auto-
    # provisioned a starting Compensation Record into its OWN track, for
    # ALL THREE employees (the shared roster fires every app's hook).
    guyana_comp_track = await _track_by_title(guyana_app, "Guyana Compensation Records")
    aruba_comp_track = await _track_by_title(aruba_app, "Aruba Compensation Records")
    bvi_comp_track = await _track_by_title(bvi_app, "BVI Compensation Records")
    guyana_comps = await ctx.find_entries({"track_id": guyana_comp_track.id})
    aruba_comps = await ctx.find_entries({"track_id": aruba_comp_track.id})
    bvi_comps = await ctx.find_entries({"track_id": bvi_comp_track.id})
    assert len(guyana_comps) == 3
    assert len(aruba_comps) == 3
    assert len(bvi_comps) == 3

    # Fund ONLY the BVI employee's BVI comp record — the Guyana/Aruba comp
    # records stay at their $0 default and must NOT be picked up. Comp
    # records link against each app's own Payroll Employees mirror, not the
    # raw hr_app employee id — resolve it first.
    bvi_employee_mirror_id = await _mirror_id(
        ctx, "BVI Payroll Employees", bvi_employee.id
    )
    bvi_comp = next(
        c
        for c in bvi_comps
        if c.custom_fields.get("employee") == bvi_employee_mirror_id
    )
    await ctx.update_entry_fields(
        bvi_comp.id,
        {
            "base_salary": 5000.00,
            "pay_frequency": "monthly",
            "effective_date": "2026-01-01",
        },
    )

    bvi_pay_runs_track = await _track_by_title(bvi_app, "BVI Pay Runs")
    pay_run = await ctx.create_entry(
        track_id=bvi_pay_runs_track.id,
        entry_type_key="pay_run",
        title="2026-01 BVI pay run",
        custom_fields={
            "frequency": "monthly",
            "period_start": "2026-01-01",
            "period_end": "2026-01-31",
            "status": "draft",
        },
    )
    assert pay_run is not None

    from app.profiles.bvi_payroll.tools.generate_payslips import (  # type: ignore[import]
        generate_payslips_for_pay_run,
    )
    from app.profiles.bvi_payroll.tools.pay_run_line_calc import (  # type: ignore[import]
        populate_pay_run_lines_for_employees,
    )

    # Only the BVI employee gets a Payroll Register line — the Guyana/Aruba
    # employees, though on the same shared "employees" roster, are never
    # touched by this BVI-only wizard population call.
    populate_result = await populate_pay_run_lines_for_employees(
        {"entry_id": pay_run.id, "employee_ids": [bvi_employee_mirror_id]}, ctx
    )
    assert populate_result["ok"], populate_result

    result = await generate_payslips_for_pay_run({"entry_id": pay_run.id}, ctx)
    assert result["ok"], result
    # Exactly the one funded BVI employee — not a bogus payslip for the
    # Guyana/Aruba employees, and not a $0.00 payslip for anyone still on a
    # $0 default comp record.
    assert result["generated_count"] == 1

    bvi_payslips_track = await _track_by_title(bvi_app, "BVI Payslips")
    bvi_payslips = await ctx.find_entries({"track_id": bvi_payslips_track.id})
    assert len(bvi_payslips) == 1
    assert bvi_payslips[0].custom_fields["employee"] == bvi_employee_mirror_id

    # Neither other country's own Payslips track is touched — no BVI run
    # leaked into either.
    guyana_payslips_track = await _track_by_title(guyana_app, "Guyana Payslips")
    guyana_payslips = await ctx.find_entries({"track_id": guyana_payslips_track.id})
    assert guyana_payslips == []

    aruba_payslips_track = await _track_by_title(aruba_app, "Aruba Payslips")
    aruba_payslips = await ctx.find_entries({"track_id": aruba_payslips_track.id})
    assert aruba_payslips == []

    # And the OTHER two countries' Statutory Rates weren't misread as
    # BVI's — the generated payslip only carries BVI-shaped deduction
    # fields (ssb_/payroll_tax_/nhi_ prefixes), never AOV/AZV/SVb/wage_tax
    # or NIS/PAYE field names.
    cf = bvi_payslips[0].custom_fields
    assert "ssb_employer" in cf
    assert "aov_employer" not in cf
    assert "nis_employee" not in cf


@pytest.mark.asyncio
async def test_bvi_tool_keys_are_not_silently_overwritten_by_the_other_apps():
    """Per-workspace tool registration is a FLAT dict keyed by tool key
    (``register_workspace_tools`` in ``app/services/hooks/registry.py``) —
    the second app installed silently overwrites the first's registration
    for any shared key. Proves every one of BVI Payroll's own declared
    tool keys is present after all three apps install, AND still stamped
    with BVI's own bundle slug (``_bundle_slug``) — i.e. nothing else
    installed after it clobbered its registration."""
    ws, owner_id = await _make_workspace()
    await _install_all_three(ws, owner_id)

    from app.services.hooks.registry import get_workspace_tools

    tools = get_workspace_tools(ws.id)

    expected_bvi_keys = {
        "bvi_generate_payslips_for_pay_run",
        "bvi_provision_compensation_for_employee",
        "bvi_backfill_compensation_records",
        "bvi_compute_upcoming_periods",
        "bvi_create_next_pay_run",
        "bvi_populate_pay_run_lines_for_employees",
        "bvi_populate_pay_run_lines_from_hr",
        "bvi_recalc_pay_run_line",
    }
    missing = expected_bvi_keys - set(tools.keys())
    assert not missing, f"BVI tool keys missing from workspace registry: {missing}"
    for key in expected_bvi_keys:
        assert tools[key].get("_bundle_slug") == "bvi-payroll", (
            f"{key} is registered but attributed to "
            f"{tools[key].get('_bundle_slug')!r}, not 'bvi-payroll' — "
            "another app's tool silently overwrote it"
        )
