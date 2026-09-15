"""Tests for ``generate_payslips_for_pay_run`` — the "Finalize Pay Run"
action (Guyana Payroll register redesign). For each Payroll Register
(``pay_run_line``) row with a linked employee AND a resolved compensation
record: renders a real PDF payslip (``_payslip_pdf.py``), creates a linked
Payslip record via ``ToolContext.create_entry`` (pay_run + compensation +
employee relations, plus the new category/allowance/take-home fields),
attaches that PDF to the record via ``ToolContext.attach_file``, and ships
every PDF generated as one zip on the click's own response too.

The tool no longer computes gross-to-net itself — it reads figures already
computed by ``pay_run_line_calc.py``'s ``recalc_pay_run_line`` hook onto
each Pay Run Line, so fixtures here build already-computed lines directly
(mirroring what that hook would have already written), not raw
employees/compensation records.
"""

from __future__ import annotations

import zipfile
from base64 import b64decode
from io import BytesIO

import pymupdf
import pytest

from app.profiles.payroll_app.tools.generate_payslips import (  # type: ignore[import]
    generate_payslips_for_pay_run,
)

_LINES_TRACK_ID = "pay-run-1-lines"


class _FakeEntry:
    def __init__(self, entry_id, custom_fields, title="", track_id=""):
        self.id = entry_id
        self.custom_fields = custom_fields
        self.title = title
        self.track_id = track_id


class _FakeCtx:
    """Minimal ToolContext stand-in — only the methods the tool uses."""

    def __init__(
        self,
        *,
        pay_run,
        employees,
        lines,
        company=None,
        existing_payslips=None,
    ):
        self._pay_run = pay_run
        self._employees = {e.id: e for e in employees}
        self._lines = lines
        self._company = company or []
        self._existing_payslips = existing_payslips or []
        self.updated_fields = None
        self.created_entries = []
        self.attached_files = []
        self._next_id = 1

    async def get_entry_system(self, entry_id):
        if self._pay_run.id == entry_id:
            return self._pay_run
        return self._employees.get(entry_id)

    async def find_entries_in_track_type(self, track_type, entry_type=None):
        return {
            "Guyana Settings": self._company,
            "Guyana Company Profile": self._company,
            "Guyana Payslips": self._existing_payslips,
        }.get(track_type, [])

    async def find_entries(self, filters):
        if filters.get("track_id") == _LINES_TRACK_ID:
            return self._lines
        return []

    async def find_track_id_by_title(self, track_type):
        return "payslips-track" if track_type == "Guyana Payslips" else None

    async def update_entry_fields(self, entry_id, fields):
        self.updated_fields = (entry_id, fields)

    async def create_entry(
        self, *, track_id, entry_type_key, title, custom_fields=None
    ):
        entry = _FakeEntry(
            f"payslip-{self._next_id}",
            dict(custom_fields or {}),
            title=title,
            track_id=track_id,
        )
        self._next_id += 1
        self.created_entries.append(entry)
        return entry

    async def attach_file(self, entry_id, *, filename, content, mime_type):
        self.attached_files.append((entry_id, filename, len(content), mime_type))
        return True

    async def get_attachment_bytes(self, attachment_id):
        return None


def _pay_run(
    status="approved",
    entry_id="pay-run-1",
    period_start="2026-06-01",
    period_end="2026-06-30",
    pay_date="2026-07-01",
    with_lines_track=True,
):
    return _FakeEntry(
        entry_id,
        {
            "period_start": period_start,
            "period_end": period_end,
            "pay_date": pay_date,
            "status": status,
            "pay_run_lines_track": _LINES_TRACK_ID if with_lines_track else None,
        },
        title="2026-06 pay run",
    )


def _line(
    line_id,
    *,
    employee,
    compensation,
    gross,
    nis=0.0,
    paye=0.0,
    net=None,
    total_allowances=0.0,
    take_home=None,
    category="employee",
):
    net = gross - nis - paye if net is None else net
    take_home = net + total_allowances if take_home is None else take_home
    return _FakeEntry(
        line_id,
        {
            "employee": employee,
            "compensation": compensation,
            "category": category,
            "gross": gross,
            "nis": nis,
            "paye": paye,
            "net": net,
            "total_allowances": total_allowances,
            "take_home": take_home,
        },
        track_id=_LINES_TRACK_ID,
    )


def _pdf_text(pdf_bytes: bytes) -> str:
    doc = pymupdf.open(stream=pdf_bytes, filetype="pdf")
    return doc[0].get_text()


@pytest.mark.asyncio
async def test_generates_one_payslip_per_line_with_compensation():
    employees = [
        _FakeEntry(
            "emp-1", {"status": "active", "job_title": "Engineer"}, title="Jane Doe"
        ),
        _FakeEntry("emp-2", {"status": "active"}, title="John Smith"),
    ]
    lines = [
        _line(
            "line-1",
            employee="emp-1",
            compensation="comp-1",
            gross=200000,
            nis=11200,
            paye=17500,
        ),
        _line(
            "line-2",
            employee="emp-2",
            compensation="comp-2",
            gross=300000,
            nis=15680,
            paye=32500,
        ),
    ]
    ctx = _FakeCtx(pay_run=_pay_run(), employees=employees, lines=lines)
    result = await generate_payslips_for_pay_run({"entry_id": "pay-run-1"}, ctx)

    assert result["ok"] is True
    assert result["generated_count"] == 2
    assert result["payslip_entries_created"] == 2
    assert result["skipped_employees"] == []

    zf = zipfile.ZipFile(BytesIO(b64decode(result["file"]["content_base64"])))
    names = sorted(zf.namelist())
    assert names == ["jane-doe.pdf", "john-smith.pdf"]
    payslip_text = _pdf_text(zf.read("jane-doe.pdf"))
    assert "Jane Doe" in payslip_text
    assert "Engineer" in payslip_text
    assert "NET PAY" in payslip_text

    # Real linked Payslip records created, one per line.
    assert len(ctx.created_entries) == 2
    jane_payslip = next(e for e in ctx.created_entries if "Jane Doe" in e.title)
    assert jane_payslip.custom_fields["pay_run"] == "pay-run-1"
    assert jane_payslip.custom_fields["compensation"] == "comp-1"
    assert jane_payslip.custom_fields["employee"] == "emp-1"
    assert jane_payslip.custom_fields["net"] < jane_payslip.custom_fields["gross"]
    # Title/pay_period derived from period_start ("June 2026"), NOT the Pay
    # Run's free-text title — preparers name those inconsistently ("2027",
    # "Test") so they can't be trusted to say what month a payslip covers.
    assert jane_payslip.title == "June 2026 - Jane Doe"
    assert jane_payslip.custom_fields["pay_period"] == "June 2026"

    # nis/paye split persisted alongside the combined `deductions` figure.
    assert jane_payslip.custom_fields["nis"] > 0
    assert jane_payslip.custom_fields["paye"] >= 0
    assert jane_payslip.custom_fields["nis"] + jane_payslip.custom_fields[
        "paye"
    ] == pytest.approx(jane_payslip.custom_fields["deductions"])
    # First payslip of the year for this employee — YTD equals this payslip's
    # own figures, no prior payslips to accumulate.
    assert jane_payslip.custom_fields["ytd_gross"] == pytest.approx(
        jane_payslip.custom_fields["gross"]
    )
    assert jane_payslip.custom_fields["ytd_nis"] == pytest.approx(
        jane_payslip.custom_fields["nis"]
    )
    assert jane_payslip.custom_fields["ytd_paye"] == pytest.approx(
        jane_payslip.custom_fields["paye"]
    )
    assert jane_payslip.custom_fields["ytd_net"] == pytest.approx(
        jane_payslip.custom_fields["net"]
    )

    # Each payslip's own PDF attached to its own record, not the Pay Run.
    assert len(ctx.attached_files) == 2
    attached_entry_ids = {a[0] for a in ctx.attached_files}
    assert attached_entry_ids == {e.id for e in ctx.created_entries}

    # gross_total/headcount mirrored back onto the Pay Run.
    entry_id, fields = ctx.updated_fields
    assert entry_id == "pay-run-1"
    assert fields["headcount"] == 2
    assert fields["gross_total"] == pytest.approx(500000.0)


@pytest.mark.asyncio
async def test_draft_pay_run_can_finalize_to_preview():
    """Finalizing from a still-draft run is how a preparer previews who's
    getting paid and how much before approving anything — must not be
    gated behind approval (that would force approving blind)."""
    employees = [_FakeEntry("emp-1", {"status": "active"}, title="Jane Doe")]
    lines = [
        _line(
            "line-1",
            employee="emp-1",
            compensation="comp-1",
            gross=200000,
            nis=11200,
            paye=17500,
        )
    ]
    ctx = _FakeCtx(pay_run=_pay_run(status="draft"), employees=employees, lines=lines)
    result = await generate_payslips_for_pay_run({"entry_id": "pay-run-1"}, ctx)

    assert result["ok"] is True
    assert result["payslip_entries_created"] == 1


@pytest.mark.asyncio
async def test_finalize_moves_draft_pay_run_to_approved():
    """Finalize Pay Run must actually move the run out of draft — it
    generates payslips/filings but previously never touched `status` at
    all, so a user who clicked Finalize watched the run stay "draft"
    forever with no visible outcome. Bumps to "approved" (reviewed, ready
    to pay), not "paid" (money actually sent — a separate, deliberate,
    human-confirmed step)."""
    employees = [_FakeEntry("emp-1", {"status": "active"}, title="Jane Doe")]
    lines = [
        _line(
            "line-1",
            employee="emp-1",
            compensation="comp-1",
            gross=200000,
            nis=11200,
            paye=17500,
        )
    ]
    ctx = _FakeCtx(pay_run=_pay_run(status="draft"), employees=employees, lines=lines)
    result = await generate_payslips_for_pay_run({"entry_id": "pay-run-1"}, ctx)

    assert result["ok"] is True
    updated_entry_id, updated_fields = ctx.updated_fields
    assert updated_entry_id == "pay-run-1"
    assert updated_fields["status"] == "approved"


@pytest.mark.asyncio
async def test_finalize_does_not_touch_status_when_already_approved():
    """Only draft -> approved — an already-approved run's status field
    isn't part of the update payload at all (no redundant no-op write)."""
    employees = [_FakeEntry("emp-1", {"status": "active"}, title="Jane Doe")]
    lines = [
        _line(
            "line-1",
            employee="emp-1",
            compensation="comp-1",
            gross=200000,
            nis=11200,
            paye=17500,
        )
    ]
    ctx = _FakeCtx(
        pay_run=_pay_run(status="approved"), employees=employees, lines=lines
    )
    result = await generate_payslips_for_pay_run({"entry_id": "pay-run-1"}, ctx)

    assert result["ok"] is True
    _updated_entry_id, updated_fields = ctx.updated_fields
    assert "status" not in updated_fields


@pytest.mark.asyncio
async def test_paid_pay_run_refuses_to_regenerate_payslips():
    employees = [_FakeEntry("emp-1", {"status": "active"}, title="Jane Doe")]
    lines = [
        _line(
            "line-1",
            employee="emp-1",
            compensation="comp-1",
            gross=200000,
            nis=11200,
            paye=17500,
        )
    ]
    ctx = _FakeCtx(pay_run=_pay_run(status="paid"), employees=employees, lines=lines)
    result = await generate_payslips_for_pay_run({"entry_id": "pay-run-1"}, ctx)

    assert result["ok"] is False
    assert "paid" in result["reason"].lower()
    assert ctx.created_entries == []


@pytest.mark.asyncio
async def test_ytd_accumulates_across_pay_runs_in_the_same_year():
    employees = [_FakeEntry("emp-1", {"status": "active"}, title="Jane Doe")]
    lines = [
        _line(
            "line-1",
            employee="emp-1",
            compensation="comp-1",
            gross=200000,
            nis=11200,
            paye=15000,
        )
    ]
    # A May payslip already exists for this employee, in the same calendar
    # year as the June pay run under test — YTD on the new June payslip
    # should fold that prior payslip's figures in.
    prior_payslip = _FakeEntry(
        "payslip-may",
        {
            "employee": "emp-1",
            "pay_run": "pay-run-0",
            "gross": 200000.0,
            "nis": 11200.0,
            "paye": 15000.0,
            "deductions": 26200.0,
            "net": 173800.0,
            "issued_date": "2026-05-31",
        },
    )
    ctx = _FakeCtx(
        pay_run=_pay_run(),
        employees=employees,
        lines=lines,
        existing_payslips=[prior_payslip],
    )
    result = await generate_payslips_for_pay_run({"entry_id": "pay-run-1"}, ctx)

    assert result["ok"] is True
    june_payslip = ctx.created_entries[0]
    assert june_payslip.custom_fields["ytd_gross"] == pytest.approx(
        prior_payslip.custom_fields["gross"] + june_payslip.custom_fields["gross"]
    )
    assert june_payslip.custom_fields["ytd_nis"] == pytest.approx(
        prior_payslip.custom_fields["nis"] + june_payslip.custom_fields["nis"]
    )
    assert june_payslip.custom_fields["ytd_paye"] == pytest.approx(
        prior_payslip.custom_fields["paye"] + june_payslip.custom_fields["paye"]
    )
    assert june_payslip.custom_fields["ytd_net"] == pytest.approx(
        prior_payslip.custom_fields["net"] + june_payslip.custom_fields["net"]
    )


@pytest.mark.asyncio
async def test_regenerating_does_not_create_duplicate_payslips():
    employees = [_FakeEntry("emp-1", {"status": "active"}, title="Jane Doe")]
    lines = [
        _line(
            "line-1",
            employee="emp-1",
            compensation="comp-1",
            gross=200000,
            nis=11200,
            paye=17500,
        )
    ]
    existing = [
        _FakeEntry("payslip-existing", {"pay_run": "pay-run-1", "employee": "emp-1"}),
    ]
    ctx = _FakeCtx(
        pay_run=_pay_run(), employees=employees, lines=lines, existing_payslips=existing
    )
    result = await generate_payslips_for_pay_run({"entry_id": "pay-run-1"}, ctx)

    assert result["ok"] is True
    assert result["generated_count"] == 1  # still counted toward totals
    assert result["payslip_entries_created"] == 0  # but no new record made
    assert ctx.created_entries == []
    assert ctx.attached_files == []


@pytest.mark.asyncio
async def test_skips_line_with_no_compensation_resolved():
    # recalc_pay_run_line never found a Compensation Record for this
    # employee — its line has no `compensation` id set.
    employees = [
        _FakeEntry("emp-1", {"status": "active"}, title="Jane Doe"),
        _FakeEntry("emp-2", {"status": "active"}, title="No Comp Employee"),
    ]
    lines = [
        _line(
            "line-1",
            employee="emp-1",
            compensation="comp-1",
            gross=200000,
            nis=11200,
            paye=17500,
        ),
        _FakeEntry(
            "line-2",
            {"employee": "emp-2", "compensation": None, "gross": 0},
            track_id=_LINES_TRACK_ID,
        ),
    ]
    ctx = _FakeCtx(pay_run=_pay_run(), employees=employees, lines=lines)
    result = await generate_payslips_for_pay_run({"entry_id": "pay-run-1"}, ctx)

    assert result["ok"] is True
    assert result["generated_count"] == 1
    assert result["skipped_employees"] == ["No Comp Employee"]


@pytest.mark.asyncio
async def test_no_lines_with_employee_is_clean_failure():
    ctx = _FakeCtx(pay_run=_pay_run(), employees=[], lines=[])
    result = await generate_payslips_for_pay_run({"entry_id": "pay-run-1"}, ctx)
    assert result["ok"] is False


@pytest.mark.asyncio
async def test_missing_entry_id_is_clean_failure():
    ctx = _FakeCtx(pay_run=_pay_run(), employees=[], lines=[])
    result = await generate_payslips_for_pay_run({}, ctx)
    assert result["ok"] is False


@pytest.mark.asyncio
async def test_pay_run_not_found_is_clean_failure():
    ctx = _FakeCtx(pay_run=_pay_run(), employees=[], lines=[])
    result = await generate_payslips_for_pay_run({"entry_id": "does-not-exist"}, ctx)
    assert result["ok"] is False


@pytest.mark.asyncio
async def test_duplicate_employee_titles_get_disambiguated_filenames():
    employees = [
        _FakeEntry("emp-1", {"status": "active"}, title="Jane Doe"),
        _FakeEntry("emp-2", {"status": "active"}, title="Jane Doe"),
    ]
    lines = [
        _line(
            "line-1",
            employee="emp-1",
            compensation="comp-1",
            gross=200000,
            nis=11200,
            paye=17500,
        ),
        _line(
            "line-2",
            employee="emp-2",
            compensation="comp-2",
            gross=250000,
            nis=14000,
            paye=22500,
        ),
    ]
    ctx = _FakeCtx(pay_run=_pay_run(), employees=employees, lines=lines)
    result = await generate_payslips_for_pay_run({"entry_id": "pay-run-1"}, ctx)
    assert result["ok"] is True
    zf = zipfile.ZipFile(BytesIO(b64decode(result["file"]["content_base64"])))
    assert sorted(zf.namelist()) == ["jane-doe-2.pdf", "jane-doe.pdf"]


@pytest.mark.asyncio
async def test_consultant_category_line_carries_take_home_and_category():
    employees = [_FakeEntry("emp-1", {"status": "active"}, title="Alondra Russell")]
    lines = [
        _line(
            "line-1",
            employee="emp-1",
            compensation="comp-1",
            gross=500000,
            nis=0,
            paye=0,
            net=500000,
            total_allowances=0,
            take_home=500000,
            category="consultant",
        ),
    ]
    ctx = _FakeCtx(pay_run=_pay_run(), employees=employees, lines=lines)
    result = await generate_payslips_for_pay_run({"entry_id": "pay-run-1"}, ctx)

    assert result["ok"] is True
    payslip = ctx.created_entries[0]
    assert payslip.custom_fields["category"] == "consultant"
    assert payslip.custom_fields["take_home"] == 500000
    assert payslip.custom_fields["net"] == 500000
