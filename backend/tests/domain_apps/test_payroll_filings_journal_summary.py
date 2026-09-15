"""Tests for ``generate_journal_summary_for_pay_run`` — the double-entry
payroll journal CSV export (Wave 1 payroll completion, "feeding the finance
app"). Finance mirrors QuickBooks and reconciles the mirror, it doesn't
post journal entries itself (see finance/skills/month_close_checklist), so
this stops at producing the export a human posts into QuickBooks — same
seam, no cross-app write.
"""

from __future__ import annotations

import csv
from base64 import b64decode
from io import StringIO

import pytest

from app.profiles.payroll_app.tools.generate_journal_summary import (  # type: ignore[import]
    generate_journal_summary_for_pay_run,
)


class _FakeEntry:
    def __init__(self, entry_id, custom_fields, title=""):
        self.id = entry_id
        self.custom_fields = custom_fields
        self.title = title


class _FakeCtx:
    def __init__(self, *, pay_run, employees, compensation, statutory_rates):
        self._pay_run = pay_run
        self._employees = employees
        self._compensation = compensation
        self._statutory_rates = statutory_rates

    async def get_entry_system(self, entry_id):
        return self._pay_run if self._pay_run.id == entry_id else None

    async def find_entries_in_track_type(self, track_type):
        return {
            "Guyana Payroll Employees": self._employees,
            "Guyana Compensation Records": self._compensation,
            "Guyana Statutory Rates": self._statutory_rates,
        }.get(track_type, [])


def _rate_pages():
    return [
        _FakeEntry(
            "band-1",
            {
                "from_amount": 0,
                "to_amount": 130000,
                "rate_pct": 0,
                "effective_date": "2026-01-01",
            },
        ),
        _FakeEntry(
            "band-2",
            {
                "from_amount": 130000,
                "to_amount": None,
                "rate_pct": 25,
                "effective_date": "2026-01-01",
            },
        ),
        _FakeEntry(
            "nis-cap",
            {
                "monthly_ceiling": 280000,
                "employee_pct": 5.6,
                "employer_pct": 8.4,
                "effective_date": "2026-01-01",
            },
        ),
    ]


def _pay_run():
    return _FakeEntry(
        "pay-run-1",
        {"period_start": "2026-06-01", "period_end": "2026-06-30"},
        title="2026-06 pay run",
    )


@pytest.mark.asyncio
async def test_journal_is_balanced_and_totals_match_two_employees():
    employees = [
        _FakeEntry("emp-1", {"status": "active"}, title="Jane Doe"),
        _FakeEntry("emp-2", {"status": "active"}, title="John Smith"),
    ]
    compensation = [
        _FakeEntry(
            "comp-1",
            {
                "employee": "emp-1",
                "base_salary": 200000,
                "pay_frequency": "monthly",
                "effective_date": "2026-01-01",
            },
        ),
        _FakeEntry(
            "comp-2",
            {
                "employee": "emp-2",
                "base_salary": 300000,
                "pay_frequency": "monthly",
                "effective_date": "2026-01-01",
            },
        ),
    ]
    ctx = _FakeCtx(
        pay_run=_pay_run(),
        employees=employees,
        compensation=compensation,
        statutory_rates=_rate_pages(),
    )
    result = await generate_journal_summary_for_pay_run({"entry_id": "pay-run-1"}, ctx)

    assert result["ok"] is True
    assert result["employee_count"] == 2
    assert result["balanced"] is True
    assert result["totals"]["gross"] == pytest.approx(500000.0)

    csv_text = b64decode(result["file"]["content_base64"]).decode("utf-8")
    rows = list(csv.reader(StringIO(csv_text)))
    header, *data_rows = rows
    assert header == ["Date", "Account", "Memo", "Debit", "Credit"]
    accounts = [r[1] for r in data_rows[:-1]]
    assert accounts == [
        "Salaries & Wages Expense",
        "Employer NIS Expense",
        "NIS Payable",
        "PAYE Payable",
        "Cash / Bank",
    ]
    total_row = data_rows[-1]
    assert total_row[2] == "TOTAL"
    assert float(total_row[3]) == pytest.approx(float(total_row[4]))


@pytest.mark.asyncio
async def test_nis_payable_credit_equals_employee_plus_employer_nis():
    employees = [_FakeEntry("emp-1", {"status": "active"}, title="Jane Doe")]
    compensation = [
        _FakeEntry(
            "comp-1",
            {
                "employee": "emp-1",
                "base_salary": 200000,
                "pay_frequency": "monthly",
                "effective_date": "2026-01-01",
            },
        ),
    ]
    ctx = _FakeCtx(
        pay_run=_pay_run(),
        employees=employees,
        compensation=compensation,
        statutory_rates=_rate_pages(),
    )
    result = await generate_journal_summary_for_pay_run({"entry_id": "pay-run-1"}, ctx)
    totals = result["totals"]
    csv_text = b64decode(result["file"]["content_base64"]).decode("utf-8")
    rows = list(csv.reader(StringIO(csv_text)))
    nis_payable_row = next(r for r in rows if r[1] == "NIS Payable")
    assert float(nis_payable_row[4]) == pytest.approx(
        totals["nis_employee_contribution"] + totals["nis_employer_contribution"]
    )


@pytest.mark.asyncio
async def test_no_active_employees_with_compensation_is_clean_failure():
    ctx = _FakeCtx(
        pay_run=_pay_run(), employees=[], compensation=[], statutory_rates=_rate_pages()
    )
    result = await generate_journal_summary_for_pay_run({"entry_id": "pay-run-1"}, ctx)
    assert result["ok"] is False


@pytest.mark.asyncio
async def test_missing_entry_id_is_clean_failure():
    ctx = _FakeCtx(
        pay_run=_pay_run(), employees=[], compensation=[], statutory_rates=[]
    )
    result = await generate_journal_summary_for_pay_run({}, ctx)
    assert result["ok"] is False


@pytest.mark.asyncio
async def test_missing_rate_pages_is_clean_failure():
    employees = [_FakeEntry("emp-1", {"status": "active"}, title="Jane Doe")]
    compensation = [
        _FakeEntry(
            "comp-1",
            {
                "employee": "emp-1",
                "base_salary": 200000,
                "pay_frequency": "monthly",
                "effective_date": "2026-01-01",
            },
        ),
    ]
    ctx = _FakeCtx(
        pay_run=_pay_run(),
        employees=employees,
        compensation=compensation,
        statutory_rates=[],
    )
    result = await generate_journal_summary_for_pay_run({"entry_id": "pay-run-1"}, ctx)
    assert result["ok"] is False
