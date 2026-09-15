"""Phase 4 (payroll-filings PAYE port) — calculation + generation tests.

Mirrors test_nis_tools.py's structure. Covers the per-row K/L/M/N-style
formulas (resolved against payroll_filings' real paye_filing_line field
keys, not the plan's Excel-column shorthand — see
_paye_format.calc_row_totals's docstring for the mapping), the parent
rollup (including the "Total Gross" field, which has no source-app
precedent and is implemented here as sum(adjusted_7a) — flagged as an
interpretation, not a ported value), and a full generate_csv round trip
asserting actual column values/order against COLUMN_HEADERS.
"""

from __future__ import annotations

import base64

import pytest

from app.profiles.payroll_app.tools import _paye_format as fmt  # type: ignore[import]
from app.profiles.payroll_app.tools import paye_calc  # type: ignore[import]
from app.profiles.payroll_app.tools import paye_generate  # type: ignore[import]
from app.profiles.payroll_app.tools import paye_import  # type: ignore[import]


class _FakeEntry:
    def __init__(self, entry_id, custom_fields, track_id="track-1"):
        self.id = entry_id
        self.custom_fields = custom_fields
        self.track_id = track_id


class _FakeCtx:
    """Minimal ToolContext stand-in — only the methods paye_calc.calc_row uses."""

    def __init__(self, *, filings, lines, employees=None, statutory_rates=None):
        self._filings = filings
        self._lines = lines
        self._employees = employees or []
        self._statutory_rates = statutory_rates or []
        self.updates: dict[str, dict] = {}

    async def find_entries_in_track_type(self, track_type, entry_type=None):
        # nis_schedule and paye_filing share one "filings" track now (see
        # profile.yaml) — _find_parent_filing scans by that shared key.
        # Statutory Rates pages live on the shared "Guyana Settings" track
        # since the Settings-menu consolidation (Pay Calendar / Statutory
        # Rates / Company Profile folded into one track).
        return {
            "filings": self._filings,
            "Guyana Payroll Employees": self._employees,
            "Guyana Settings": self._statutory_rates,
        }.get(track_type, [])

    async def find_entries(self, query):
        track_id = query.get("track_id")
        return [e for e in self._lines if e.track_id == track_id]

    async def get_entry_system(self, entry_id):
        for e in [*self._filings, *self._lines]:
            if e.id == entry_id:
                return e
        return None

    async def update_entry_fields(self, entry_id, custom_fields):
        self.updates.setdefault(entry_id, {}).update(custom_fields)
        for e in [*self._filings, *self._lines]:
            if e.id == entry_id:
                e.custom_fields = {**(e.custom_fields or {}), **custom_fields}
        return True


# ── per-row K/L/M/N formulas ────────────────────────────────────────────────


def test_calc_row_totals_formula_mapping():
    cf = {
        "value_7a": 100000,
        "total_overtime": 5000,
        "second_job_deduction": 1000,
        "overtime_deduction": 500,
        "value_7b": 2000,
        "value_7c_taxable": 1000,
        "value_7c_nontaxable": 500,
        "personal_allowance": 65000,
        "employee_nis_contribution": 5600,
        "medical_life_insurance": 1000,
        "children_deduction": 2000,
    }
    totals = fmt.calc_row_totals(cf)
    # adjusted_7a = value_7a + total_overtime - second_job_deduction - overtime_deduction
    assert totals["adjusted_7a"] == 100000 + 5000 - 1000 - 500 == 103500
    # total_income = adjusted_7a + value_7b + value_7c_taxable + value_7c_nontaxable
    assert totals["total_income"] == 103500 + 2000 + 1000 + 500 == 107000
    # total_deductions = personal_allowance + employee_nis_contribution
    #                     + medical_life_insurance + children_deduction
    assert totals["total_deductions"] == 65000 + 5600 + 1000 + 2000 == 73600


def test_calc_row_totals_handles_missing_and_string_fields():
    # Source app's _int()/toInt() coerce blanks/strings/None to 0 — confirm
    # the ported version does too, since form fields round-trip as strings.
    totals = fmt.calc_row_totals(
        {"value_7a": "50000", "total_overtime": "", "personal_allowance": None}
    )
    assert totals["adjusted_7a"] == 50000
    assert totals["total_deductions"] == 0


@pytest.mark.asyncio
async def test_calc_row_writes_fields_and_rolls_up_parent():
    filing = _FakeEntry(
        "filing-1",
        {"company_name": "Acme Co", "employee_lines_track": "track-1"},
    )
    line_1 = _FakeEntry(
        "line-1",
        {
            "value_7a": 100000,
            "total_overtime": 0,
            "second_job_deduction": 0,
            "overtime_deduction": 0,
            "value_7b": 0,
            "value_7c_taxable": 0,
            "value_7c_nontaxable": 0,
            "personal_allowance": 65000,
            "employee_nis_contribution": 5600,
            "medical_life_insurance": 0,
            "children_deduction": 0,
            "tax_deducted": 3000,
        },
        track_id="track-1",
    )
    line_2 = _FakeEntry(
        "line-2",
        {
            "value_7a": 50000,
            "total_overtime": 0,
            "second_job_deduction": 0,
            "overtime_deduction": 0,
            "value_7b": 0,
            "value_7c_taxable": 0,
            "value_7c_nontaxable": 0,
            "personal_allowance": 65000,
            "employee_nis_contribution": 2800,
            "medical_life_insurance": 0,
            "children_deduction": 0,
            "tax_deducted": 0,
        },
        track_id="track-1",
    )
    ctx = _FakeCtx(filings=[filing], lines=[line_1, line_2])

    result = await paye_calc.calc_row({"entry_id": "line-1"}, ctx)

    assert result["ok"] is True
    assert result["adjusted_7a"] == 100000
    assert result["total_income"] == 100000
    assert result["total_deductions"] == 65000 + 5600
    assert result["rollup"] is True
    assert line_1.custom_fields["adjusted_7a"] == 100000

    # Parent rollup, computed AFTER line-1's own new totals are folded in —
    # line-2 hasn't been calc'd yet (its stored total_income/adjusted_7a is
    # absent), so the rollup recomputes each sibling's totals fresh from raw
    # fields rather than trusting possibly-stale computed columns.
    pcf = filing.custom_fields
    assert pcf["total_entries"] == 2
    assert pcf["total_income"] == 100000 + 50000
    assert (
        pcf["total_gross"] == 100000 + 50000
    )  # sum(adjusted_7a) — see module docstring
    assert pcf["total_deductions"] == (65000 + 5600) + (65000 + 2800)
    assert pcf["total_tax"] == 3000 + 0


@pytest.mark.asyncio
async def test_calc_row_no_parent_found_still_writes_own_fields():
    line = _FakeEntry("line-orphan", {"value_7a": 1000}, track_id="track-orphan")
    ctx = _FakeCtx(filings=[], lines=[line])
    result = await paye_calc.calc_row({"entry_id": "line-orphan"}, ctx)
    assert result["ok"] is True
    assert result["rollup"] is False
    assert line.custom_fields["adjusted_7a"] == 1000


# ── independent employee_nis_contribution derivation ────────────────────────
#
# PAYE and NIS are separate, independently-filable obligations — a PAYE
# filing must be completable whether or not a matching NIS filing exists.
# employee_nis_contribution is therefore derived straight from this line's
# own wage + the statutory_rates wiki page, never from a sibling NIS filing
# (an earlier, same-day version of this DID mirror from a matching NIS
# filing — rejected: "why is payee pulling from nis filing??? it can be
# done separately").


_NIS_CAP_PAGE_2026 = _FakeEntry(
    "cap-2026",
    {
        "monthly_ceiling": 280000,
        "employee_pct": 5.6,
        "employer_pct": 8.4,
        "effective_date": "2026-01-01",
    },
)


@pytest.mark.asyncio
async def test_calc_row_derives_employee_nis_contribution_from_statutory_rate():
    filing = _FakeEntry(
        "filing-nis-1",
        {
            "company_name": "Acme Co",
            "year": 2026,
            "period": 6,
            "employee_lines_track": "track-nis-1",
        },
    )
    line = _FakeEntry("line-nis-1", {"value_7a": 100000}, track_id="track-nis-1")
    ctx = _FakeCtx(filings=[filing], lines=[line], statutory_rates=[_NIS_CAP_PAGE_2026])

    result = await paye_calc.calc_row({"entry_id": "line-nis-1"}, ctx)

    assert result["ok"] is True
    expected = round(100000 * 5.6 / 100)  # under the ceiling -- full wage taxed
    assert result["employee_nis_contribution"] == expected
    assert line.custom_fields["employee_nis_contribution"] == expected
    # total_deductions reflects the freshly derived figure in the SAME save.
    assert result["total_deductions"] == expected


@pytest.mark.asyncio
async def test_calc_row_derives_from_ceiling_capped_wage():
    filing = _FakeEntry(
        "filing-nis-2",
        {
            "company_name": "Acme Co",
            "year": 2026,
            "period": 6,
            "employee_lines_track": "track-nis-2",
        },
    )
    # Above the 280000 monthly ceiling -- contribution must be capped there,
    # not computed off the full wage.
    line = _FakeEntry("line-nis-2", {"value_7a": 400000}, track_id="track-nis-2")
    ctx = _FakeCtx(filings=[filing], lines=[line], statutory_rates=[_NIS_CAP_PAGE_2026])

    result = await paye_calc.calc_row({"entry_id": "line-nis-2"}, ctx)

    expected = round(280000 * 5.6 / 100)
    assert result["employee_nis_contribution"] == expected


@pytest.mark.asyncio
async def test_calc_row_does_not_overwrite_manual_employee_nis_contribution():
    filing = _FakeEntry(
        "filing-nis-3",
        {
            "company_name": "Acme Co",
            "year": 2026,
            "period": 6,
            "employee_lines_track": "track-nis-3",
        },
    )
    # Preparer already typed a figure -- must not be clobbered even though a
    # rate page is available and would derive a different number.
    line = _FakeEntry(
        "line-nis-3",
        {"value_7a": 100000, "employee_nis_contribution": 1234},
        track_id="track-nis-3",
    )
    ctx = _FakeCtx(filings=[filing], lines=[line], statutory_rates=[_NIS_CAP_PAGE_2026])

    result = await paye_calc.calc_row({"entry_id": "line-nis-3"}, ctx)

    assert result["employee_nis_contribution"] == 1234
    assert line.custom_fields["employee_nis_contribution"] == 1234


@pytest.mark.asyncio
async def test_calc_row_no_matching_rate_page_leaves_nis_contribution_unset():
    """No statutory_rates page at all in effect -- must not guess, and must
    not require (or even look for) a sibling NIS filing to exist."""
    filing = _FakeEntry(
        "filing-nis-4",
        {
            "company_name": "Acme Co",
            "year": 2026,
            "period": 6,
            "employee_lines_track": "track-nis-4",
        },
    )
    line = _FakeEntry("line-nis-4", {"value_7a": 100000}, track_id="track-nis-4")
    ctx = _FakeCtx(filings=[filing], lines=[line], statutory_rates=[])

    result = await paye_calc.calc_row({"entry_id": "line-nis-4"}, ctx)

    assert result["ok"] is True
    assert result["employee_nis_contribution"] == 0
    assert "employee_nis_contribution" not in line.custom_fields


@pytest.mark.asyncio
async def test_calc_row_skips_recompute_when_filing_locked():
    """Payroll structural redesign — once the filing's own status has moved
    to submitted/accepted, calc_row must not keep changing figures."""
    filing = _FakeEntry(
        "filing-locked",
        {
            "company_name": "Acme Co",
            "year": 2026,
            "period": 6,
            "employee_lines_track": "track-locked",
            "status": "accepted",
            "total_income": 999,
        },
    )
    line = _FakeEntry(
        "line-locked",
        {"value_7a": 100000, "total_income": 111},
        track_id="track-locked",
    )
    ctx = _FakeCtx(filings=[filing], lines=[line])

    result = await paye_calc.calc_row({"entry_id": "line-locked"}, ctx)

    assert result["ok"] is True
    assert result.get("locked") is True
    assert ctx.updates == {}
    assert line.custom_fields["total_income"] == 111
    assert filing.custom_fields["total_income"] == 999


# ── generate_csv round trip ─────────────────────────────────────────────────


class _FakeGenCtx:
    def __init__(self, filing, lines):
        self._filing = filing
        self._lines = lines
        self.updates: dict[str, dict] = {}

    async def get_entry_system(self, entry_id):
        return self._filing if entry_id == self._filing.id else None

    async def find_entries(self, query):
        track_id = query.get("track_id")
        return [e for e in self._lines if e.track_id == track_id]

    async def update_entry_fields(self, entry_id, custom_fields):
        self.updates.setdefault(entry_id, {}).update(custom_fields)
        if entry_id == self._filing.id:
            self._filing.custom_fields = {
                **(self._filing.custom_fields or {}),
                **custom_fields,
            }


@pytest.mark.asyncio
async def test_generate_csv_column_values_and_summary_row():
    filing = _FakeEntry(
        "filing-2",
        {
            "company_name": "Acme Co",
            "company_tin": "123456789",
            "company_address": "1 Main St",
            "year": 2026,
            "period": 6,
            "employee_lines_track": "track-2",
        },
    )
    line = _FakeEntry(
        "line-3",
        {
            "tin": "987654321",
            "employee_number": "E1",
            "first_name": "Jane",
            "last_name": "Doe",
            "other_names": "",
            "address": "2 Second St",
            "pay_frequency": "Monthly",
            "period_employed": "6",
            "employee_type": "Full-Time",
            "primary_secondary_job": "Primary",
            "value_7a": 100000,
            "total_overtime": 0,
            "second_job_deduction": 0,
            "overtime_deduction": 0,
            "value_7b": 0,
            "value_7c_taxable": 0,
            "value_7c_nontaxable": 0,
            "personal_allowance": 65000,
            "employee_nis_contribution": 5600,
            "medical_life_insurance": 0,
            "children_deduction": 0,
            "tax_deducted": 3000,
            "date_of_birth": "1990-01-01",
            "bank_name": "Bank Co",
            "account_no": "1111",
            "routing_transit": "2222",
            "child_declaration_no": "",
        },
        track_id="track-2",
    )
    ctx = _FakeGenCtx(filing, [line])

    result = await paye_generate.generate_csv({"entry_id": "filing-2"}, ctx)

    assert result["ok"] is True
    file_payload = result["file"]
    assert file_payload["filename"] == "Acme_Co_06_2026.csv"
    assert file_payload["mime"] == "text/csv"

    csv_text = base64.b64decode(file_payload["content_base64"]).decode("utf-8")
    rows = csv_text.strip("\n").split("\n")
    assert rows[0] == ",".join(fmt.COLUMN_HEADERS)

    data_row = rows[1].split(",")
    assert data_row[0] == "987654321"  # TIN
    assert data_row[2] == "Jane"  # First_Name
    assert data_row[3] == "Doe"  # Last_Name
    assert data_row[10] == "100000"  # Value_7A_Salaries_Wages
    assert data_row[14] == "100000"  # Adjusted_7A_Salaries_Wages (O = K+L-M-N)
    assert data_row[18] == "100000"  # Total_Income (S = O+P+Q+R)
    assert data_row[23] == str(65000 + 5600)  # Total_Deductions (X = T+U+V+W)
    assert data_row[24] == "3000"  # Tax_Deducted

    summary_row = rows[2].split(",")
    assert summary_row[0] == "123456789"  # company TIN
    assert summary_row[2] == "Acme Co"  # company name
    assert summary_row[4] == "'06/2026"  # submission date
    assert summary_row[5] == "1 Main St"  # company address
    assert summary_row[7] == "1"  # employee count
    assert summary_row[14] == "100000"  # summed Adjusted_7A
    assert summary_row[18] == "100000"  # summed Total_Income
    assert summary_row[23] == str(65000 + 5600)  # summed Total_Deductions
    # draft -> generated on a successful Generate click.
    assert filing.custom_fields["status"] == "generated"


# ── import round trip ───────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_import_csv_parses_rows_and_strips_summary_row():
    filing = _FakeEntry(
        "filing-3",
        {
            "company_name": "Acme Co",
            "year": 2026,
            "period": 6,
            "employee_lines_track": "track-3",
        },
    )
    line = _FakeEntry(
        "line-4",
        {
            "tin": "1",
            "employee_number": "E1",
            "first_name": "Jane",
            "last_name": "Doe",
            "other_names": "",
            "address": "",
            "pay_frequency": "Monthly",
            "period_employed": "6",
            "employee_type": "Full-Time",
            "primary_secondary_job": "Primary",
            "value_7a": 100000,
            "total_overtime": 0,
            "second_job_deduction": 0,
            "overtime_deduction": 0,
            "value_7b": 0,
            "value_7c_taxable": 0,
            "value_7c_nontaxable": 0,
            "personal_allowance": 65000,
            "employee_nis_contribution": 5600,
            "medical_life_insurance": 0,
            "children_deduction": 0,
            "tax_deducted": 3000,
            "date_of_birth": "",
            "bank_name": "",
            "account_no": "",
            "routing_transit": "",
            "child_declaration_no": "",
        },
        track_id="track-3",
    )
    gen_ctx = _FakeGenCtx(filing, [line])
    generated = await paye_generate.generate_csv({"entry_id": "filing-3"}, gen_ctx)
    content_base64 = generated["file"]["content_base64"]

    employee = _FakeEntry("emp-jane", {"tin": "1"})
    import_ctx = _FakeCtx(filings=[filing], lines=[line], employees=[employee])
    result = await paye_import.import_csv(
        {"content_base64": content_base64}, import_ctx
    )

    assert result["ok"] is True
    assert result["unresolved_count"] == 0
    assert (
        len(result["rows"]) == 1
    )  # summary row correctly stripped, not treated as a 2nd employee
    row = result["rows"][0]
    assert row["employee_id"] == "emp-jane"
    cf = row["custom_fields"]
    assert cf["employee"] == "emp-jane"
    assert cf["first_name"] == "Jane"
    assert cf["last_name"] == "Doe"
    assert cf["value_7a"] == 100000
    assert result["company_info"]["name"] == "Acme Co"
    assert result["company_info"]["year"] == "2026"
    assert result["company_info"]["period"] == "06"


@pytest.mark.asyncio
async def test_import_csv_skips_rows_with_no_matching_employee():
    filing = _FakeEntry(
        "filing-4",
        {
            "company_name": "Acme Co",
            "year": 2026,
            "period": 6,
            "employee_lines_track": "track-4",
        },
    )
    line = _FakeEntry(
        "line-5",
        {
            "tin": "999",
            "employee_number": "E9",
            "first_name": "No",
            "last_name": "Match",
            "other_names": "",
            "address": "",
            "pay_frequency": "Monthly",
            "period_employed": "6",
            "employee_type": "Full-Time",
            "primary_secondary_job": "Primary",
            "value_7a": 50000,
            "total_overtime": 0,
            "second_job_deduction": 0,
            "overtime_deduction": 0,
            "value_7b": 0,
            "value_7c_taxable": 0,
            "value_7c_nontaxable": 0,
            "personal_allowance": 0,
            "employee_nis_contribution": 0,
            "medical_life_insurance": 0,
            "children_deduction": 0,
            "tax_deducted": 0,
            "date_of_birth": "",
            "bank_name": "",
            "account_no": "",
            "routing_sort_code": "",
            "routing_transit": "",
            "child_declaration_no": "",
        },
        track_id="track-4",
    )
    gen_ctx = _FakeGenCtx(filing, [line])
    generated = await paye_generate.generate_csv({"entry_id": "filing-4"}, gen_ctx)
    content_base64 = generated["file"]["content_base64"]

    import_ctx = _FakeCtx(filings=[filing], lines=[line], employees=[])
    result = await paye_import.import_csv(
        {"content_base64": content_base64}, import_ctx
    )

    assert result["ok"] is True
    assert result["unresolved_count"] == 1
    assert len(result["rows"]) == 0
