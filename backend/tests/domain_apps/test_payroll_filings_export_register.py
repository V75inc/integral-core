"""Tests for ``export_payroll_register`` — the single Pay Run action-bar
button (CSV/PDF format dropdown, not two buttons — ActionBarWidget.tsx's
``options``/``option_param``) that exports the Payroll Register ("the
paysheet"). Reads already-computed ``pay_run_line`` entries directly (same
fixture shape as ``test_payroll_filings_generate_payslips.py``), nothing
is recomputed. Tests exercise ``export_payroll_register_csv``/
``export_payroll_register_pdf`` directly (the two renderers) plus the
``export_payroll_register`` dispatcher's format routing.
"""

from __future__ import annotations

import csv
from base64 import b64decode
from io import StringIO

import pymupdf
import pytest

from app.profiles.payroll_app.tools.export_payroll_register import (
    export_payroll_register,
    export_payroll_register_csv,
    export_payroll_register_pdf,
)

_LINES_TRACK_ID = "pay-run-1-lines"


class _FakeEntry:
    def __init__(self, entry_id, custom_fields, title="", track_id=""):
        self.id = entry_id
        self.custom_fields = custom_fields
        self.title = title
        self.track_id = track_id


class _FakeCtx:
    def __init__(self, *, pay_run, employees, lines):
        self._pay_run = pay_run
        self._employees = {e.id: e for e in employees}
        self._lines = lines

    async def get_entry_system(self, entry_id):
        if self._pay_run.id == entry_id:
            return self._pay_run
        return self._employees.get(entry_id)

    async def find_entries(self, filters):
        if filters.get("track_id") == _LINES_TRACK_ID:
            return self._lines
        return []


def _employee(eid, name, *, job_title="Engineer", employee_id="E-001"):
    return _FakeEntry(
        eid,
        {
            "job_title": job_title,
            "employee_id": employee_id,
            "legal_last_name": name.split()[-1],
            "legal_first_name": name.split()[0],
        },
        title=name,
    )


def _line(lid, employee_id, *, category="employee", **fields):
    base = {
        "employee": employee_id,
        "category": category,
        "basic": 100000.0,
        "bonus": 0.0,
        "gross": 100000.0,
        "govt_allowance": 0.0,
        "child_allowance": 0.0,
        "nis": 5600.0,
        "nis_employer": 8400.0,
        "chargeable_income": 94400.0,
        "paye": 0.0,
        "net": 94400.0,
        "internet_allowance": 0.0,
        "telephone_allowance": 0.0,
        "traveling_allowance": 0.0,
        "entertainment_allowance": 0.0,
        "specialization_allowance": 0.0,
        "responsibility_allowance": 0.0,
        "total_allowances": 0.0,
        "take_home": 94400.0,
        "deduction": 0.0,
        "reimbursement": 0.0,
    }
    base.update(fields)
    return _FakeEntry(lid, base, title=f"line-{lid}", track_id=_LINES_TRACK_ID)


def _pay_run(*, pay_run_lines_track=_LINES_TRACK_ID):
    return _FakeEntry(
        "pay-run-1",
        {
            "period_start": "2026-06-01",
            "period_end": "2026-06-30",
            "pay_run_lines_track": pay_run_lines_track,
        },
        title="June 2026 pay run",
    )


@pytest.mark.asyncio
async def test_export_csv_no_entry_id():
    result = await export_payroll_register_csv(
        {}, _FakeCtx(pay_run=_pay_run(), employees=[], lines=[])
    )
    assert result["ok"] is False


@pytest.mark.asyncio
async def test_export_csv_no_lines_is_clean_failure():
    ctx = _FakeCtx(pay_run=_pay_run(), employees=[], lines=[])
    result = await export_payroll_register_csv({"entry_id": "pay-run-1"}, ctx)
    assert result["ok"] is False
    assert "no Payroll Register lines" in result["reason"]


@pytest.mark.asyncio
async def test_export_csv_produces_grouped_rows_with_totals():
    e1 = _employee("emp-1", "Jane Doe")
    e2 = _employee("emp-2", "John Smith")
    lines = [
        _line(
            "line-1",
            "emp-1",
            category="employee",
            basic=100000.0,
            gross=100000.0,
            net=94400.0,
        ),
        _line(
            "line-2",
            "emp-2",
            category="consultant",
            basic=50000.0,
            gross=50000.0,
            net=50000.0,
            nis=0.0,
            nis_employer=0.0,
            chargeable_income=0.0,
        ),
    ]
    ctx = _FakeCtx(pay_run=_pay_run(), employees=[e1, e2], lines=lines)
    result = await export_payroll_register_csv({"entry_id": "pay-run-1"}, ctx)

    assert result["ok"] is True
    assert result["row_count"] == 2
    csv_text = b64decode(result["file"]["content_base64"]).decode("utf-8")
    rows = list(csv.reader(StringIO(csv_text)))
    header = rows[0]
    assert header[0] == "Employee"
    assert "Gross" in header

    employee_names = [r[0] for r in rows]
    assert "Jane Doe" in employee_names
    assert "John Smith" in employee_names
    assert any("subtotal" in r[1].lower() for r in rows if len(r) > 1)
    assert any("GRAND TOTAL" in r[1] for r in rows if len(r) > 1)

    gross_idx = header.index("Gross")
    grand_total_row = next(r for r in rows if len(r) > 1 and r[1] == "GRAND TOTAL")
    assert grand_total_row[gross_idx] == "150,000.00"


@pytest.mark.asyncio
async def test_export_pdf_produces_a_readable_pdf():
    e1 = _employee("emp-1", "Jane Doe")
    lines = [
        _line(
            "line-1",
            "emp-1",
            category="employee",
            basic=100000.0,
            gross=100000.0,
            net=94400.0,
        )
    ]
    ctx = _FakeCtx(pay_run=_pay_run(), employees=[e1], lines=lines)
    result = await export_payroll_register_pdf({"entry_id": "pay-run-1"}, ctx)

    assert result["ok"] is True
    assert result["file"]["mime"] == "application/pdf"
    pdf_bytes = b64decode(result["file"]["content_base64"])
    doc = pymupdf.open(stream=pdf_bytes, filetype="pdf")
    assert doc.page_count >= 1
    text = doc[0].get_text()
    assert "Jane Doe" in text
    assert "GRAND TOTAL" in text
    doc.close()


@pytest.mark.asyncio
async def test_export_pdf_no_lines_is_clean_failure():
    ctx = _FakeCtx(pay_run=_pay_run(), employees=[], lines=[])
    result = await export_payroll_register_pdf({"entry_id": "pay-run-1"}, ctx)
    assert result["ok"] is False


# ── export_payroll_register — single-button format dispatcher ───────────


@pytest.mark.asyncio
async def test_dispatcher_defaults_to_csv_when_format_omitted():
    e1 = _employee("emp-1", "Jane Doe")
    lines = [_line("line-1", "emp-1")]
    ctx = _FakeCtx(pay_run=_pay_run(), employees=[e1], lines=lines)
    result = await export_payroll_register({"entry_id": "pay-run-1"}, ctx)
    assert result["ok"] is True
    assert result["file"]["mime"].startswith("text/csv")


@pytest.mark.asyncio
async def test_dispatcher_routes_csv_format():
    e1 = _employee("emp-1", "Jane Doe")
    lines = [_line("line-1", "emp-1")]
    ctx = _FakeCtx(pay_run=_pay_run(), employees=[e1], lines=lines)
    result = await export_payroll_register(
        {"entry_id": "pay-run-1", "format": "csv"}, ctx
    )
    assert result["ok"] is True
    assert result["file"]["mime"].startswith("text/csv")


@pytest.mark.asyncio
async def test_dispatcher_routes_pdf_format():
    e1 = _employee("emp-1", "Jane Doe")
    lines = [_line("line-1", "emp-1")]
    ctx = _FakeCtx(pay_run=_pay_run(), employees=[e1], lines=lines)
    result = await export_payroll_register(
        {"entry_id": "pay-run-1", "format": "pdf"}, ctx
    )
    assert result["ok"] is True
    assert result["file"]["mime"] == "application/pdf"
