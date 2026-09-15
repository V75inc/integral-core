"""Tests for payroll_filings' server-side field-mirror tools
(``app/profiles/payroll_filings/tools/field_mirror.py``), which replace
``EditableTableWidget.tsx``'s client-side ``maybeAutofillFromEmployee`` /
``maybeAutofillWageFromCompensation`` with a reactive-on-save mechanism.

Covers: identity mirroring per entry type (PAYE local field names vs NIS's
Phase-3-pending ones), monthly-equivalent wage math for annual/biweekly/
monthly pay frequencies, the fill-once (don't clobber a manual edit) guard,
picking the most recent Compensation Record by effective_date, and — the
privilege-sensitive case — that I-PAYROLL-PRIV-01 still holds after mirroring:
a caller excluded from the Compensation track can read the *derived* wage on
a filing line they do have access to, but still cannot read the *source*
Compensation entry directly.
"""

from __future__ import annotations

import pytest

from app.models.edges import COLLABORATES_ON, CONTAINS
from app.models.nodes import Entry, EntryType, Track, Workspace
from app.profiles.payroll_app.tools.field_mirror import (
    mirror_identity_from_employee,
    mirror_wage_from_compensation,
)
from app.services.hooks.registry import ToolContext
from app.services.permissions import resolve_role
from app.utils.time import utc_now_iso
from tests.domain_apps.test_payroll_privilege import _provision_payroll_fixture


async def _make_employee(track: Track, owner_id: str, **custom_fields) -> Entry:
    et = await EntryType.create(name="Employee", track_id=track.id)
    entry = await Entry.create(
        track_id=track.id,
        type_id=et.id,
        title=custom_fields.get("title", "Test Employee"),
        author_id=owner_id,
        custom_fields=custom_fields,
    )
    await track.connect(entry, edge=CONTAINS, added_at=utc_now_iso())
    return entry


async def _make_line(
    track: Track, owner_id: str, entry_type_name: str, employee_id: str, **extra_fields
) -> Entry:
    et = await EntryType.create(name=entry_type_name, track_id=track.id)
    entry = await Entry.create(
        track_id=track.id,
        type_id=et.id,
        title="Line",
        author_id=owner_id,
        custom_fields={"employee": employee_id, **extra_fields},
    )
    await track.connect(entry, edge=CONTAINS, added_at=utc_now_iso())
    return entry


async def _make_locked_filing(
    lines_track: Track, owner_id: str, *, status: str
) -> Entry:
    """A "filings"-titled track holding one filing entry whose
    employee_lines_track anchor points at ``lines_track`` — the shape
    ``_filing_lock.find_parent_filing`` scans for (payroll structural
    redesign's locked-filing skip)."""
    filings_track = await Track.create(
        title="Filings", owner_id=owner_id, workspace_id=lines_track.workspace_id
    )
    et = await EntryType.create(name="NIS Schedule", track_id=filings_track.id)
    filing = await Entry.create(
        track_id=filings_track.id,
        type_id=et.id,
        title="Locked Filing",
        author_id=owner_id,
        custom_fields={"employee_lines_track": lines_track.id, "status": status},
    )
    await filings_track.connect(filing, edge=CONTAINS, added_at=utc_now_iso())
    return filing


async def _make_compensation(
    track: Track,
    owner_id: str,
    employee_id: str,
    base_salary,
    pay_frequency,
    effective_date,
) -> Entry:
    et = await EntryType.create(name="Compensation record", track_id=track.id)
    entry = await Entry.create(
        track_id=track.id,
        type_id=et.id,
        title="Comp",
        author_id=owner_id,
        custom_fields={
            "employee": employee_id,
            "base_salary": base_salary,
            "currency": "GYD",
            "pay_frequency": pay_frequency,
            "effective_date": effective_date,
        },
    )
    await track.connect(entry, edge=CONTAINS, added_at=utc_now_iso())
    return entry


# ----- Identity mirroring -----------------------------------------------------


@pytest.mark.asyncio
async def test_mirror_identity_from_employee_paye():
    ws = await Workspace.create(name="Field Mirror Test Co — PAYE", kind="organization")
    employees_track = await Track.create(
        title="Employees", owner_id="tester", workspace_id=ws.id
    )
    lines_track = await Track.create(
        title="PAYE Filing Lines", owner_id="tester", workspace_id=ws.id
    )

    employee = await _make_employee(
        employees_track,
        "tester",
        legal_first_name="Jane",
        legal_last_name="Doe",
        legal_other_names="M",
        tin="123456789",
        date_of_birth="1990-01-01",
        bank_name="Demerara Bank",
        bank_account_number="00112233",
        address_line="1 Main St",
        nis_number="A123456",
    )
    line = await _make_line(lines_track, "tester", "PAYE filing line", employee.id)

    ctx = ToolContext(user_id="tester", workspace_id=ws.id, scope="workspace")
    result = await mirror_identity_from_employee(
        {"entry_id": line.id, "entry_type": "paye_filing_line"}, ctx
    )
    assert result["ok"] is True

    line = await Entry.get(line.id)
    cf = line.custom_fields
    assert cf["first_name"] == "Jane"
    assert cf["last_name"] == "Doe"
    assert cf["other_names"] == "M"
    assert cf["tin"] == "123456789"
    assert cf["date_of_birth"] == "1990-01-01"
    assert cf["bank_name"] == "Demerara Bank"
    assert cf["account_no"] == "00112233"
    assert cf["address"] == "1 Main St"
    # NIS-only local field names must not leak onto a PAYE line.
    assert "ssn" not in cf
    assert "surname" not in cf


@pytest.mark.asyncio
async def test_mirror_identity_from_employee_nis():
    ws = await Workspace.create(name="Field Mirror Test Co — NIS", kind="organization")
    employees_track = await Track.create(
        title="Employees", owner_id="tester", workspace_id=ws.id
    )
    lines_track = await Track.create(
        title="NIS Schedule Lines", owner_id="tester", workspace_id=ws.id
    )

    employee = await _make_employee(
        employees_track,
        "tester",
        legal_first_name="John",
        legal_last_name="Smith",
        nis_number="Z987654",
    )
    line = await _make_line(lines_track, "tester", "NIS schedule line", employee.id)

    ctx = ToolContext(user_id="tester", workspace_id=ws.id, scope="workspace")
    result = await mirror_identity_from_employee(
        {"entry_id": line.id, "entry_type": "nis_schedule_line"}, ctx
    )
    assert result["ok"] is True

    line = await Entry.get(line.id)
    cf = line.custom_fields
    # These 3 keys don't exist on nis_schedule_line's form_schema yet
    # (Phase 3 adds them) but update_entry_fields merges into custom_fields
    # regardless — proving the mirror already produces the right values ready
    # for Phase 3 to render.
    assert cf["surname"] == "Smith"
    assert cf["first_name"] == "John"
    assert cf["ssn"] == "Z987654"


@pytest.mark.asyncio
async def test_mirror_identity_unknown_entry_type_is_noop():
    ws = await Workspace.create(
        name="Field Mirror Test Co — Unknown", kind="organization"
    )
    track = await Track.create(title="Misc", owner_id="tester", workspace_id=ws.id)
    entry = await _make_line(
        track, "tester", "Something else", employee_id="does-not-matter"
    )

    ctx = ToolContext(user_id="tester", workspace_id=ws.id, scope="workspace")
    result = await mirror_identity_from_employee(
        {"entry_id": entry.id, "entry_type": "some_other_entry_type"}, ctx
    )
    assert result["ok"] is True
    assert "no local identity fields" in result["reason"]


@pytest.mark.asyncio
async def test_mirror_identity_no_employee_linked_is_noop():
    ws = await Workspace.create(
        name="Field Mirror Test Co — No Employee", kind="organization"
    )
    lines_track = await Track.create(
        title="PAYE Filing Lines", owner_id="tester", workspace_id=ws.id
    )
    et = await EntryType.create(name="PAYE filing line", track_id=lines_track.id)
    line = await Entry.create(
        track_id=lines_track.id,
        type_id=et.id,
        title="Line",
        author_id="tester",
        custom_fields={},
    )
    await lines_track.connect(line, edge=CONTAINS, added_at=utc_now_iso())

    ctx = ToolContext(user_id="tester", workspace_id=ws.id, scope="workspace")
    result = await mirror_identity_from_employee(
        {"entry_id": line.id, "entry_type": "paye_filing_line"}, ctx
    )
    assert result["ok"] is True
    assert result["reason"] == "no employee linked"


# ----- Wage mirroring ----------------------------------------------------------


@pytest.mark.asyncio
async def test_mirror_wage_from_compensation_annual():
    ws = await Workspace.create(
        name="Field Mirror Test Co — Wage Annual", kind="organization"
    )
    employees_track = await Track.create(
        title="Employees", owner_id="tester", workspace_id=ws.id
    )
    comp_track = await Track.create(
        title="Guyana Compensation Records", owner_id="tester", workspace_id=ws.id
    )
    lines_track = await Track.create(
        title="PAYE Filing Lines", owner_id="tester", workspace_id=ws.id
    )

    employee = await _make_employee(employees_track, "tester", legal_first_name="Ann")
    await _make_compensation(
        comp_track,
        "tester",
        employee.id,
        base_salary=1_200_000,
        pay_frequency="annual",
        effective_date="2026-01-01",
    )
    line = await _make_line(
        lines_track, "tester", "PAYE filing line", employee.id, value_7a=0
    )

    ctx = ToolContext(user_id="tester", workspace_id=ws.id, scope="workspace")
    result = await mirror_wage_from_compensation(
        {"entry_id": line.id, "entry_type": "paye_filing_line"}, ctx
    )
    assert result["ok"] is True

    line = await Entry.get(line.id)
    assert line.custom_fields["value_7a"] == round(1_200_000 / 12)


@pytest.mark.asyncio
async def test_mirror_wage_from_compensation_biweekly():
    ws = await Workspace.create(
        name="Field Mirror Test Co — Wage Biweekly", kind="organization"
    )
    employees_track = await Track.create(
        title="Employees", owner_id="tester", workspace_id=ws.id
    )
    comp_track = await Track.create(
        title="Guyana Compensation Records", owner_id="tester", workspace_id=ws.id
    )
    lines_track = await Track.create(
        title="NIS Schedule Lines", owner_id="tester", workspace_id=ws.id
    )

    employee = await _make_employee(employees_track, "tester", legal_first_name="Bob")
    await _make_compensation(
        comp_track,
        "tester",
        employee.id,
        base_salary=20_000,
        pay_frequency="biweekly",
        effective_date="2026-01-01",
    )
    line = await _make_line(lines_track, "tester", "NIS schedule line", employee.id)

    ctx = ToolContext(user_id="tester", workspace_id=ws.id, scope="workspace")
    result = await mirror_wage_from_compensation(
        {"entry_id": line.id, "entry_type": "nis_schedule_line"}, ctx
    )
    assert result["ok"] is True

    line = await Entry.get(line.id)
    assert line.custom_fields["wage_period_1"] == round((20_000 * 26) / 12)


@pytest.mark.asyncio
async def test_mirror_wage_does_not_overwrite_existing_value():
    ws = await Workspace.create(
        name="Field Mirror Test Co — Wage No Clobber", kind="organization"
    )
    employees_track = await Track.create(
        title="Employees", owner_id="tester", workspace_id=ws.id
    )
    comp_track = await Track.create(
        title="Guyana Compensation Records", owner_id="tester", workspace_id=ws.id
    )
    lines_track = await Track.create(
        title="PAYE Filing Lines", owner_id="tester", workspace_id=ws.id
    )

    employee = await _make_employee(employees_track, "tester", legal_first_name="Cara")
    await _make_compensation(
        comp_track,
        "tester",
        employee.id,
        base_salary=1_200_000,
        pay_frequency="annual",
        effective_date="2026-01-01",
    )
    # Preparer has already manually set (or a prior mirror already filled) the wage cell.
    line = await _make_line(
        lines_track, "tester", "PAYE filing line", employee.id, value_7a=55_000
    )

    ctx = ToolContext(user_id="tester", workspace_id=ws.id, scope="workspace")
    result = await mirror_wage_from_compensation(
        {"entry_id": line.id, "entry_type": "paye_filing_line"}, ctx
    )
    assert result["ok"] is True
    assert "already set" in result["reason"]

    line = await Entry.get(line.id)
    assert line.custom_fields["value_7a"] == 55_000


@pytest.mark.asyncio
async def test_mirror_wage_picks_most_recent_compensation_record():
    ws = await Workspace.create(
        name="Field Mirror Test Co — Wage Most Recent", kind="organization"
    )
    employees_track = await Track.create(
        title="Employees", owner_id="tester", workspace_id=ws.id
    )
    comp_track = await Track.create(
        title="Guyana Compensation Records", owner_id="tester", workspace_id=ws.id
    )
    lines_track = await Track.create(
        title="PAYE Filing Lines", owner_id="tester", workspace_id=ws.id
    )

    employee = await _make_employee(employees_track, "tester", legal_first_name="Dev")
    await _make_compensation(
        comp_track,
        "tester",
        employee.id,
        base_salary=600_000,
        pay_frequency="annual",
        effective_date="2024-01-01",
    )
    await _make_compensation(
        comp_track,
        "tester",
        employee.id,
        base_salary=1_200_000,
        pay_frequency="annual",
        effective_date="2026-01-01",
    )
    line = await _make_line(
        lines_track, "tester", "PAYE filing line", employee.id, value_7a=0
    )

    ctx = ToolContext(user_id="tester", workspace_id=ws.id, scope="workspace")
    result = await mirror_wage_from_compensation(
        {"entry_id": line.id, "entry_type": "paye_filing_line"}, ctx
    )
    assert result["ok"] is True

    line = await Entry.get(line.id)
    assert line.custom_fields["value_7a"] == round(1_200_000 / 12)


# ----- Locked filing (payroll structural redesign) -----------------------------


@pytest.mark.asyncio
async def test_mirror_identity_skips_when_filing_locked():
    ws = await Workspace.create(
        name="Field Mirror Test Co — Locked Identity", kind="organization"
    )
    employees_track = await Track.create(
        title="Employees", owner_id="tester", workspace_id=ws.id
    )
    lines_track = await Track.create(
        title="PAYE Filing Lines", owner_id="tester", workspace_id=ws.id
    )
    await _make_locked_filing(lines_track, "tester", status="submitted")

    employee = await _make_employee(
        employees_track, "tester", legal_first_name="Jane", legal_last_name="Doe"
    )
    line = await _make_line(lines_track, "tester", "PAYE filing line", employee.id)

    ctx = ToolContext(user_id="tester", workspace_id=ws.id, scope="workspace")
    result = await mirror_identity_from_employee(
        {"entry_id": line.id, "entry_type": "paye_filing_line"}, ctx
    )
    assert result["ok"] is True
    assert result.get("locked") is True

    line = await Entry.get(line.id)
    # Never mirrored — the identity fields stay entirely absent, not just
    # unchanged, proving the skip happened before any write.
    assert "first_name" not in line.custom_fields


@pytest.mark.asyncio
async def test_mirror_wage_skips_when_filing_locked():
    ws = await Workspace.create(
        name="Field Mirror Test Co — Locked Wage", kind="organization"
    )
    employees_track = await Track.create(
        title="Employees", owner_id="tester", workspace_id=ws.id
    )
    comp_track = await Track.create(
        title="Guyana Compensation Records", owner_id="tester", workspace_id=ws.id
    )
    lines_track = await Track.create(
        title="PAYE Filing Lines", owner_id="tester", workspace_id=ws.id
    )
    await _make_locked_filing(lines_track, "tester", status="accepted")

    employee = await _make_employee(employees_track, "tester", legal_first_name="Ann")
    await _make_compensation(
        comp_track,
        "tester",
        employee.id,
        base_salary=1_200_000,
        pay_frequency="annual",
        effective_date="2026-01-01",
    )
    line = await _make_line(
        lines_track, "tester", "PAYE filing line", employee.id, value_7a=0
    )

    ctx = ToolContext(user_id="tester", workspace_id=ws.id, scope="workspace")
    result = await mirror_wage_from_compensation(
        {"entry_id": line.id, "entry_type": "paye_filing_line"}, ctx
    )
    assert result["ok"] is True
    assert result.get("locked") is True

    line = await Entry.get(line.id)
    assert line.custom_fields["value_7a"] == 0


# ----- Privilege boundary (I-PAYROLL-PRIV-01) ----------------------------------


@pytest.mark.asyncio
async def test_mirror_wage_preserves_payroll_privilege_boundary():
    """The mirror runs with a trusted, ungated ToolContext (by design — same
    trust tier as nis_calc_row/paye_calc_row), so it CAN read the Compensation
    Record. That must not leak into a wider read grant for ordinary callers:
    an engineer excluded from every Payroll track can read the *derived* wage
    already sitting on a filing line they have direct access to, but still
    cannot resolve any role on the source Compensation entry itself.
    """
    fx = await _provision_payroll_fixture()
    now = utc_now_iso()
    employees_track = fx["hr_tracks"]["Employees"]
    compensation_track = fx["payroll_tracks"]["Guyana Compensation Records"]

    employee = await Entry.create(
        track_id=employees_track.id,
        title="Priv Test Employee",
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
        title="Priv Test Comp",
        author_id=fx["founder"].id,
        custom_fields={
            "employee": employee.id,
            "base_salary": 600_000,
            "currency": "GYD",
            "pay_frequency": "annual",
            "effective_date": "2026-01-01",
        },
    )
    await compensation_track.connect(comp, edge=CONTAINS, added_at=now)

    # A filing-line track the engineer DOES have direct access to (not a
    # Payroll track — mirrors how payroll_filings' own tracks are separately
    # ACL'd from the Payroll app's privileged Compensation/Pay Runs/Payslips).
    lines_track = await Track.create(
        title="PAYE Filing Lines",
        owner_id=fx["founder"].id,
        workspace_id=fx["workspace"].id,
    )
    await fx["engineer"].connect(
        lines_track,
        edge=COLLABORATES_ON,
        role="editor",
        granted_at=now,
        granted_by=fx["founder"].id,
    )
    line_et = await EntryType.create(name="PAYE filing line", track_id=lines_track.id)
    line = await Entry.create(
        track_id=lines_track.id,
        type_id=line_et.id,
        title="Line",
        author_id=fx["founder"].id,
        custom_fields={"employee": employee.id},
    )
    await lines_track.connect(line, edge=CONTAINS, added_at=now)

    ctx = ToolContext(
        user_id=fx["founder"].id, workspace_id=fx["workspace"].id, scope="workspace"
    )
    result = await mirror_wage_from_compensation(
        {"entry_id": line.id, "entry_type": "paye_filing_line"}, ctx
    )
    assert result["ok"] is True

    line = await Entry.get(line.id)
    assert line.custom_fields.get("value_7a") == round(600_000 / 12)

    # Engineer can read the derived value via the line entry they have access to.
    line_role = await resolve_role(fx["engineer"].id, "entry", line.id)
    assert line_role in ("editor", "commenter", "viewer"), line_role

    # Engineer still cannot resolve any role on the source Compensation entry.
    comp_role = await resolve_role(fx["engineer"].id, "entry", comp.id)
    assert comp_role is None, comp_role
