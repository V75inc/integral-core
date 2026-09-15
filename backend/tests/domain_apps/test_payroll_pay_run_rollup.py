"""``_roll_up_pay_run_totals`` — the Pay Run summary-fields rollup in
payroll-app's ``pay_run_line_calc.py``. Covers the gross_total/net_total/
total_deductions/employer_cost math (moved server-side off
ReportCenterWidget.tsx — see that module's docstring) and the
exception_count/filing_count fields added alongside it (referenced by the
"Payroll controls" report card since it was authored, but nothing ever
computed them until now).

Was ``test_guyana_payroll_hr_pay_run_rollup.py``, importing the identical
copy of this function from the now-removed ``guyana-payroll-hr`` app (every
payroll app is standalone-with-optional-HR-sync now — see payroll-app's own
docstrings — so guyana-payroll-hr's hard-required-HR, no-toggle design was
retired in its favor). Renamed/repointed rather than deleted since the
rollup logic itself is unchanged and untested elsewhere.
"""

from __future__ import annotations

import pytest

from app.profiles.payroll_app.tools.pay_run_line_calc import (
    _roll_up_pay_run_totals,
)

_LINES_TRACK_ID = "lines-track-1"
_FILINGS_TRACK_ID = "filings-track"


class _FakeEntry:
    def __init__(self, entry_id, custom_fields, title="", track_id=None):
        self.id = entry_id
        self.custom_fields = custom_fields
        self.title = title
        self.track_id = track_id


class _FakeCtx:
    def __init__(self, *, lines=None, filings=None):
        self._lines = lines or []
        self._filings = filings or []
        self.updates = {}

    async def find_entries(self, filters):
        if filters.get("track_id") == _LINES_TRACK_ID:
            return self._lines
        return []

    async def find_entries_in_track_type(self, track_type):
        if track_type == "Filings":
            return self._filings
        return []

    async def update_entry_fields(self, entry_id, fields):
        self.updates.setdefault(entry_id, {}).update(fields)


def _pay_run():
    return _FakeEntry("pay-run-1", {}, title="June 2026")


def _complete_line(entry_id="line-1"):
    return _FakeEntry(
        entry_id,
        {
            "employee": "emp-1",
            "compensation": "comp-1",
            "gross": 100000.0,
            "nis": 5600.0,
            "paye": 2000.0,
            "net": 92400.0,
            "nis_employer": 8400.0,
        },
    )


@pytest.mark.asyncio
async def test_rollup_no_lines_track_is_a_noop():
    ctx = _FakeCtx()
    await _roll_up_pay_run_totals(ctx, _pay_run(), None)
    assert ctx.updates == {}


@pytest.mark.asyncio
async def test_rollup_sums_gross_net_deductions_and_employer_cost():
    lines = [_complete_line("line-1"), _complete_line("line-2")]
    ctx = _FakeCtx(lines=lines)
    pay_run = _pay_run()
    await _roll_up_pay_run_totals(ctx, pay_run, _LINES_TRACK_ID)

    updated = ctx.updates[pay_run.id]
    assert updated["gross_total"] == 200000.0
    assert updated["net_total"] == 184800.0
    # gross - net
    assert updated["total_deductions"] == 15200.0
    # gross + employer NIS
    assert updated["employer_cost"] == 216800.0
    assert updated["headcount"] == 2
    assert updated["exception_count"] == 0
    assert updated["filing_count"] == 0


@pytest.mark.asyncio
async def test_rollup_sums_line_amounts_as_cents_not_binary_floats():
    lines = [
        _FakeEntry(
            "line-1",
            {
                "employee": "emp-1",
                "compensation": "comp-1",
                "gross": 0.1,
                "nis": 0,
                "paye": 0,
                "net": 0.1,
                "nis_employer": 0.1,
            },
        ),
        _FakeEntry(
            "line-2",
            {
                "employee": "emp-2",
                "compensation": "comp-2",
                "gross": 0.2,
                "nis": 0,
                "paye": 0,
                "net": 0.2,
                "nis_employer": 0.2,
            },
        ),
    ]
    ctx = _FakeCtx(lines=lines)
    pay_run = _pay_run()

    await _roll_up_pay_run_totals(ctx, pay_run, _LINES_TRACK_ID)

    updated = ctx.updates[pay_run.id]
    assert updated["gross_total"] == 0.3
    assert updated["net_total"] == 0.3
    assert updated["total_deductions"] == 0.0
    assert updated["employer_cost"] == 0.6


@pytest.mark.asyncio
async def test_rollup_counts_lines_missing_employee_or_compensation_as_exceptions():
    lines = [
        _complete_line("line-1"),
        _FakeEntry("line-2", {"employee": "emp-2"}),  # no compensation, no calc
        _FakeEntry("line-3", {}),  # no employee at all
    ]
    ctx = _FakeCtx(lines=lines)
    pay_run = _pay_run()
    await _roll_up_pay_run_totals(ctx, pay_run, _LINES_TRACK_ID)

    updated = ctx.updates[pay_run.id]
    assert updated["exception_count"] == 2
    # line-2/line-3 have no gross, so they don't count toward headcount either
    assert updated["headcount"] == 1


@pytest.mark.asyncio
async def test_rollup_counts_only_filings_linked_to_this_pay_run():
    ctx = _FakeCtx(
        lines=[_complete_line()],
        filings=[
            _FakeEntry("nis-1", {"pay_run": "pay-run-1"}),
            _FakeEntry("paye-1", {"pay_run": "pay-run-1"}),
            _FakeEntry("nis-2", {"pay_run": "some-other-run"}),
        ],
    )
    pay_run = _pay_run()
    await _roll_up_pay_run_totals(ctx, pay_run, _LINES_TRACK_ID)

    assert ctx.updates[pay_run.id]["filing_count"] == 2
