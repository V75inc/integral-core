"""End-to-end (real DB, real install) test for Aruba Payroll's full
wizard/register flow: install -> Aruba Pay Calendar -> Create Pay Run
(anchors a Payroll Register track) -> populate a line for an employee
(fires the ``aruba_recalc_pay_run_line`` hook) -> Generate Payslips (reads
the already-computed line, snapshots it into a Payslip).

Also proves the hyphenated bundle slug (``aruba-payroll``) resolves its
tool handler_refs correctly — the same class of bug that once broke
``payroll-app``'s own install.
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
        name="Aruba Payroll Test",
        name_fold="aruba payroll test",
        created_at=now,
        updated_at=now,
    )
    owner = await User.create(name="Aruba Payroll Test owner")
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


async def _mirror_id(ctx: ToolContext, hrm_employee_id: str) -> str:
    """Compensation Records / Payroll Register lines link against this
    app's own Aruba Payroll Employees mirror, not the raw hr_app employee
    id — see tools/hrm_sync.py. The mirror hook creates it synchronously
    off the same entry.create this test's employee creation fires."""
    for rec in await ctx.find_entries_in_track_type("Aruba Payroll Employees"):
        if (rec.custom_fields or {}).get("hrm_source_employee_id") == hrm_employee_id:
            return rec.id
    raise AssertionError(
        f"no Aruba Payroll Employees mirror found for {hrm_employee_id!r}"
    )


@pytest.mark.asyncio
async def test_install_registers_expected_tracks_and_tools():
    ws, owner_id = await _make_workspace()
    hr_lib = await _load_library_cp("hr_app")
    aruba_lib = await _load_library_cp("aruba-payroll")
    await install_app(workspace_id=ws.id, library_cp_id=hr_lib.id, actor_id=owner_id)
    result = await install_app(
        workspace_id=ws.id, library_cp_id=aruba_lib.id, actor_id=owner_id, settings={}
    )
    assert result["ok"] if "ok" in result else result.get("app_id")

    app_node = await App.get(result["app_id"])
    tracks = await app_node.nodes(edge=[CONTAINS], node=["Track"])
    titles = {getattr(t, "title", "") for t in tracks}
    assert {
        "Aruba Pay Runs",
        "Aruba Settings",
        "Aruba Compensation Records",
        "Aruba Payslips",
    } <= titles


@pytest.mark.asyncio
async def test_wizard_populated_pay_run_reproduces_golden_example_end_to_end():
    """Reproduces the "Zimmerman" real payslip/tax-report example: gross
    7,000.00/month, no children, no sickness/fringe, period 1 — confirmed
    exactly against real Aruba data: taxable_income_this_period=6,421.25,
    annual_taxable_income=77,055.00, annual_tax=2,546.25 (both bands
    exercised: 0% to 34,930, 21% above). wage_tax is asserted with a small
    tolerance (~3 cents) — see ``_net_pay_calc.py``'s docstring for why the
    period-spreading step isn't reproduced to the exact cent.

    This time through the FULL wizard/register flow, not a direct calc
    call: Create Pay Run (auto-provisions a Payroll Register track) ->
    populate a line for the employee (fires recalc_pay_run_line) ->
    Generate Payslips (reads the already-computed line).
    """
    ws, owner_id = await _make_workspace()
    hr_lib = await _load_library_cp("hr_app")
    aruba_lib = await _load_library_cp("aruba-payroll")
    hr_result = await install_app(
        workspace_id=ws.id, library_cp_id=hr_lib.id, actor_id=owner_id
    )
    aruba_result = await install_app(
        workspace_id=ws.id, library_cp_id=aruba_lib.id, actor_id=owner_id, settings={}
    )
    hr_app = await App.get(hr_result["app_id"])
    aruba_app = await App.get(aruba_result["app_id"])

    ctx = ToolContext(user_id=owner_id, workspace_id=ws.id, scope="test")

    member_user = await User.create(user_id="u_member", display_name="Member Account")
    await catalog_user(member_user)
    await member_user.connect(
        ws, edge=IS_MEMBER_OF, role="member", joined_at=utc_now_iso()
    )

    employees_track = await _track_by_title(hr_app, "Employees")
    employee = await ctx.create_entry(
        track_id=employees_track.id,
        entry_type_key="employee",
        title="Jacqueline Zimmerman",
        custom_fields={
            "status": "active",
            "job_title": "Manager",
            "member": member_user.id,
        },
    )
    assert employee is not None
    employee_mirror_id = await _mirror_id(ctx, employee.id)

    # The entry.create hook should have auto-provisioned a $0 starting
    # Compensation Record — fund it with the golden-example figures.
    # Comp records link against this app's own Payroll Employees mirror,
    # not the raw hr_app employee id.
    comp_records = await ctx.find_entries_in_track_type("Aruba Compensation Records")
    comp = next(
        (
            c
            for c in comp_records
            if c.custom_fields.get("employee") == employee_mirror_id
        ),
        None,
    )
    assert comp is not None, "provision_compensation_for_employee hook did not fire"
    await ctx.update_entry_fields(
        comp.id,
        {
            "base_salary": 7000.00,
            "pay_frequency": "monthly",
            "effective_date": "2026-01-01",
        },
    )

    # Pay Calendar records live on the shared "Aruba Settings" track since
    # the Settings-menu consolidation — not a dedicated track anymore.
    settings_track = await _track_by_title(aruba_app, "Aruba Settings")
    calendar_entry = await ctx.create_entry(
        track_id=settings_track.id,
        entry_type_key="pay_calendar",
        title="Aruba Pay Calendar",
        custom_fields={
            "cadence": "monthly",
            "anchor_period_start": "2026-01-01",
            "pay_date_offset_days": 5,
        },
    )
    assert calendar_entry is not None

    pay_runs_track = await _track_by_title(aruba_app, "Aruba Pay Runs")
    pay_run = await ctx.create_entry(
        track_id=pay_runs_track.id,
        entry_type_key="pay_run",
        title="2026-01 Aruba pay run",
        custom_fields={
            "frequency": "monthly",
            "period_start": "2026-01-01",
            "period_end": "2026-01-31",
            "pay_date": "2026-02-05",
            "status": "draft",
        },
    )
    assert pay_run is not None
    lines_track_id = pay_run.custom_fields.get("pay_run_lines_track")
    assert lines_track_id, "pay_run_lines_track anchor did not auto-provision"

    from app.profiles.aruba_payroll.tools.pay_run_line_calc import (  # type: ignore[import]
        populate_pay_run_lines_for_employees,
    )

    populate_result = await populate_pay_run_lines_for_employees(
        {"entry_id": pay_run.id, "employee_ids": [employee_mirror_id]}, ctx
    )
    assert populate_result["ok"], populate_result
    assert populate_result["added_count"] == 1

    lines = await ctx.find_entries({"track_id": lines_track_id})
    assert len(lines) == 1
    line = lines[0]
    lcf = line.custom_fields
    assert lcf["gross"] == 7000.00
    assert lcf["taxable_income_this_period"] == 6421.25
    assert lcf["annual_taxable_income"] == 77055.00
    assert lcf["annual_tax"] == 2546.25
    assert abs(lcf["wage_tax"] - 212.16) < 0.05
    assert lcf["category"] == "employee"
    assert lcf["compensation"] == comp.id

    from app.profiles.aruba_payroll.tools.generate_payslips import (  # type: ignore[import]
        generate_payslips_for_pay_run,
    )

    result = await generate_payslips_for_pay_run({"entry_id": pay_run.id}, ctx)
    assert result["ok"], result
    assert result["generated_count"] == 1

    payslips = await ctx.find_entries_in_track_type("Aruba Payslips")
    payslip = next(
        p for p in payslips if p.custom_fields.get("employee") == employee_mirror_id
    )
    cf = payslip.custom_fields

    assert cf["gross"] == 7000.00
    assert cf["taxable_income_this_period"] == 6421.25
    assert cf["annual_taxable_income"] == 77055.00
    assert cf["annual_tax"] == 2546.25
    assert abs(cf["wage_tax"] - 212.16) < 0.05
    expected_net = round(
        7000.00 - cf["aov_employee"] - cf["azv_employee"] - cf["wage_tax"], 2
    )
    assert cf["net"] == expected_net

    reloaded_pay_run = await ctx.get_entry_system(pay_run.id)
    assert reloaded_pay_run.custom_fields["status"] == "approved"
    assert reloaded_pay_run.custom_fields["headcount"] == 1


@pytest.mark.asyncio
async def test_generate_payslips_skips_unfunded_line_without_error():
    """A Pay Run Line for an employee still on the $0 auto-provisioned
    default Compensation Record must be skipped, not manufacture a $0.00
    Payslip — same convention as the direct-from-compensation v1 had."""
    ws, owner_id = await _make_workspace()
    hr_lib = await _load_library_cp("hr_app")
    aruba_lib = await _load_library_cp("aruba-payroll")
    hr_result = await install_app(
        workspace_id=ws.id, library_cp_id=hr_lib.id, actor_id=owner_id
    )
    aruba_result = await install_app(
        workspace_id=ws.id, library_cp_id=aruba_lib.id, actor_id=owner_id, settings={}
    )
    hr_app = await App.get(hr_result["app_id"])
    aruba_app = await App.get(aruba_result["app_id"])
    ctx = ToolContext(user_id=owner_id, workspace_id=ws.id, scope="test")

    member_user = await User.create(
        user_id="u_member2", display_name="Member Account 2"
    )
    await catalog_user(member_user)
    await member_user.connect(
        ws, edge=IS_MEMBER_OF, role="member", joined_at=utc_now_iso()
    )

    employees_track = await _track_by_title(hr_app, "Employees")
    employee = await ctx.create_entry(
        track_id=employees_track.id,
        entry_type_key="employee",
        title="Unfunded Employee",
        custom_fields={
            "status": "active",
            "job_title": "Clerk",
            "member": member_user.id,
        },
    )
    assert employee is not None
    employee_mirror_id = await _mirror_id(ctx, employee.id)

    pay_runs_track = await _track_by_title(aruba_app, "Aruba Pay Runs")
    pay_run = await ctx.create_entry(
        track_id=pay_runs_track.id,
        entry_type_key="pay_run",
        title="2026-01 pay run",
        custom_fields={
            "frequency": "monthly",
            "period_start": "2026-01-01",
            "period_end": "2026-01-31",
            "status": "draft",
        },
    )
    lines_track_id = pay_run.custom_fields.get("pay_run_lines_track")

    from app.profiles.aruba_payroll.tools.generate_payslips import (  # type: ignore[import]
        generate_payslips_for_pay_run,
    )
    from app.profiles.aruba_payroll.tools.pay_run_line_calc import (  # type: ignore[import]
        populate_pay_run_lines_for_employees,
    )

    await populate_pay_run_lines_for_employees(
        {"entry_id": pay_run.id, "employee_ids": [employee_mirror_id]}, ctx
    )
    lines = await ctx.find_entries({"track_id": lines_track_id})
    assert len(lines) == 1
    assert lines[0].custom_fields.get("gross") in (0, 0.0, None)

    result = await generate_payslips_for_pay_run({"entry_id": pay_run.id}, ctx)
    assert result["ok"] is False
    assert "reason" in result
