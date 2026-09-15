"""Phase 3 (payroll-filings NIS port) — calculation + generation tests.

Covers the pieces most likely to silently diverge from internal-tools'
byte-exact output: VBA banker's rounding, the wage-ceiling/contribution-rate
lookup's tie-breaking (including the June-2013 rate-change boundary), SSN
normalization, and a full generate_txt fixed-width round trip against hand
-computed expected values (not just "some bytes came back").

``nis_calc.calc_row``/``nis_generate.generate_txt`` take a ``ToolContext``
and hit the real Entry/Track substrate — exercised here against a lightweight
fake context (no DB) so the per-period ceiling-capping and per-period
banker's-rounding-accumulation logic (the part most likely to be
"simplified" incorrectly) is verified fast and deterministically.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from types import SimpleNamespace

import pytest

from app.profiles.payroll_app.tools import _nis_format as fmt  # type: ignore[import]
from app.profiles.payroll_app.tools import (
    _statutory_rates as rates,  # type: ignore[import]
)
from app.profiles.payroll_app.tools import nis_calc  # type: ignore[import]
from app.profiles.payroll_app.tools import nis_generate  # type: ignore[import]

# ── banker's rounding ───────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "value,expected",
    [
        (0.5, 0),  # round-half-to-even: 0 is even
        (1.5, 2),  # 2 is even
        (2.5, 2),  # 2 is even
        (3.5, 4),  # 4 is even
        (-0.5, 0),
        (-1.5, -2),
        (2.4, 2),
        (2.6, 3),
    ],
)
def test_vba_round_0_banker_rounding(value, expected):
    assert fmt.vba_round_0(Decimal(str(value))) == expected


# ── SSN normalization ────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("123-456-789", "123456-789"),  # only the FIRST "-" is stripped
        ("123 456 789", "123456 789"),  # only the FIRST " " is stripped
        ("12O345678", "12345678"),  # first "O" stripped
        ("12L345678", "12345678"),  # 'L' at index 2 stripped (one pass)
        (
            "12L2L45678",
            "122L45678",
        ),  # one pass: index-2 'L' stripped, next char '2' is a
        # digit so the loop breaks after one pass (doesn't strip the second L)
        ("abc123456", "ABC123456"),  # uppercased
    ],
)
def test_process_ssn(raw, expected):
    assert fmt.process_ssn(raw) == expected


# ── rate-table lookup / June-2013 boundary ──────────────────────────────────


class _FakeEntry:
    def __init__(self, entry_id, custom_fields, track_id="track-1"):
        self.id = entry_id
        self.custom_fields = custom_fields
        self.track_id = track_id


# Default nis_contribution_cap wiki page used by tests that don't care about
# the rate-boundary lookup itself — matches the 2013-06-effective rate/2014-01
# ceiling the old hardcoded _nis_rates.py table carried for these same dates.
_DEFAULT_NIS_CAP_PAGE = {
    "monthly_ceiling": 158159,
    "employee_pct": 5.6,
    "employer_pct": 8.4,
    "effective_date": "2013-06-01",
}


class _FakeCtx:
    """Minimal ToolContext stand-in — only the methods nis_calc.calc_row uses.

    Wage-ceiling/contribution-rate lookups now go through the
    ``statutory_rates`` wiki track (see _statutory_rates.py) instead of a
    hardcoded table — ``nis_cap_pages`` stands in for that track's entries.
    """

    def __init__(self, *, filings, lines, nis_cap_pages=None):
        self._filings = filings
        self._lines = lines
        self._nis_cap_pages = (
            [_FakeEntry("cap-default", dict(_DEFAULT_NIS_CAP_PAGE))]
            if nis_cap_pages is None
            else nis_cap_pages
        )
        self.updates: dict[str, dict] = {}

    async def find_entries_in_track_type(self, track_type):
        # nis_schedule and paye_filing share one "filings" track now (see
        # profile.yaml) — _find_parent_filing scans by that shared key.
        return {
            "filings": self._filings,
            "Guyana Statutory Rates": self._nis_cap_pages,
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


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "as_of,expected_employer_pct",
    [
        (date(2013, 5, 31), 7.8),  # just before the boundary: old rate
        (date(2013, 6, 30), 8.4),  # exactly the boundary month: new rate
        (date(2013, 7, 31), 8.4),  # after: new rate
        (date(2020, 1, 31), 8.4),  # far after: still new rate (no newer page)
    ],
)
async def test_latest_nis_cap_page_june_2013_boundary(as_of, expected_employer_pct):
    ctx = _FakeCtx(
        filings=[],
        lines=[],
        nis_cap_pages=[
            _FakeEntry(
                "cap-pre-2013",
                {
                    "monthly_ceiling": 150628,
                    "employee_pct": 5.2,
                    "employer_pct": 7.8,
                    "effective_date": "1900-01-01",
                },
            ),
            _FakeEntry("cap-2013", dict(_DEFAULT_NIS_CAP_PAGE)),
        ],
    )
    cf = await rates.latest_nis_cap_page(ctx, as_of)
    assert cf is not None
    assert cf["employer_pct"] == expected_employer_pct


@pytest.mark.asyncio
async def test_calc_row_per_period_ceiling_and_rounding():
    """Ports App.jsx's calcRow(): ceiling caps EACH period before summing,
    and ER/EE accumulate banker's-rounded PER-PERIOD amounts — not
    round(sum(capped)). Use a ceiling low enough that one period is capped
    and one isn't, so a total-then-round shortcut would disagree.
    """
    filing = _FakeEntry(
        "filing-1",
        {
            "contribution_year": 2014,
            "contribution_month": "January",
            "schedule_type": "Monthly",
            "employee_lines_track": "track-1",
        },
    )
    line = _FakeEntry(
        "line-1",
        {
            "over_60": False,
            "wage_period_1": 200000,  # above the 2014-01 ceiling (158159) -> capped
            "wage_period_2": 50000,  # below ceiling -> uncapped
            "wage_period_3": 0,
            "wage_period_4": 0,
            "wage_period_5": 0,
        },
        track_id="track-1",
    )
    ctx = _FakeCtx(filings=[filing], lines=[line])

    result = await nis_calc.calc_row({"entry_id": "line-1"}, ctx)

    assert result["ok"] is True
    assert result["total_actual_wages"] == 250000
    assert result["total_insurable_wages"] == 158159 + 50000
    # employer/employee contributions = per-period banker-rounded sum at 8.4% / 5.6%
    expected_er = fmt.vba_round_0(
        Decimal("158159") * Decimal("8.4") / 100
    ) + fmt.vba_round_0(Decimal("50000") * Decimal("8.4") / 100)
    expected_ee = fmt.vba_round_0(
        Decimal("158159") * Decimal("5.6") / 100
    ) + fmt.vba_round_0(Decimal("50000") * Decimal("5.6") / 100)
    assert result["employer_contribution"] == expected_er
    assert result["employee_contribution"] == expected_ee
    # parent rollup
    assert filing.custom_fields["total_payable"] == expected_er + expected_ee


@pytest.mark.asyncio
async def test_calc_row_over_60_is_employer_only():
    filing = _FakeEntry(
        "filing-2",
        {
            "contribution_year": 2014,
            "contribution_month": "January",
            "schedule_type": "Monthly",
            "employee_lines_track": "track-2",
        },
    )
    line = _FakeEntry(
        "line-2",
        {
            "over_60": True,
            "wage_period_1": 50000,
            "wage_period_2": 0,
            "wage_period_3": 0,
            "wage_period_4": 0,
            "wage_period_5": 0,
        },
        track_id="track-2",
    )
    ctx = _FakeCtx(filings=[filing], lines=[line])

    result = await nis_calc.calc_row({"entry_id": "line-2"}, ctx)

    assert result["employee_contribution"] == 0
    assert result["employer_contribution"] == fmt.vba_round_0(
        Decimal("50000") * Decimal(str(nis_calc.DEFAULT_OVER60_EMPLOYER_PCT)) / 100
    )


@pytest.mark.asyncio
async def test_calc_row_skips_recompute_when_filing_locked():
    """Payroll structural redesign — once a filing's own status has moved to
    submitted/accepted, calc_row must not silently keep changing a line's
    figures out from under a save to an unrelated cell."""
    filing = _FakeEntry(
        "filing-locked",
        {
            "contribution_year": 2014,
            "contribution_month": "January",
            "schedule_type": "Monthly",
            "employee_lines_track": "track-locked",
            "status": "submitted",
            "total_payable": 999,  # pre-existing value that must NOT change
        },
    )
    line = _FakeEntry(
        "line-locked",
        {
            "over_60": False,
            "wage_period_1": 200000,
            "wage_period_2": 0,
            "wage_period_3": 0,
            "wage_period_4": 0,
            "wage_period_5": 0,
            # Pre-existing computed fields that must survive untouched.
            "total_actual_wages": 111,
            "total_insurable_wages": 111,
            "employer_contribution": 111,
            "employee_contribution": 111,
        },
        track_id="track-locked",
    )
    ctx = _FakeCtx(filings=[filing], lines=[line])

    result = await nis_calc.calc_row({"entry_id": "line-locked"}, ctx)

    assert result["ok"] is True
    assert result.get("locked") is True
    assert ctx.updates == {}
    # Untouched — proves the skip happened before any recompute/write.
    assert line.custom_fields["employer_contribution"] == 111
    assert filing.custom_fields["total_payable"] == 999


# ── generate_txt fixed-width round trip ─────────────────────────────────────


class _FakeGenCtx:
    def __init__(self, filing, lines, employees_by_id):
        self._filing = filing
        self._lines = lines
        self._employees = employees_by_id
        self.updates: dict[str, dict] = {}

    async def get_entry_system(self, entry_id):
        if entry_id == self._filing.id:
            return self._filing
        if entry_id in self._employees:
            return self._employees[entry_id]
        return None

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
async def test_generate_txt_field_widths_and_values():
    filing = _FakeEntry(
        "filing-3",
        {
            "employer_name": "Acme Co",
            "registration_number": "1234",
            "contribution_year": 2014,
            "contribution_month": "January",
            "schedule_type": "Monthly",
            "period_1_date": "2014-01-15",
            "period_2_date": None,
            "period_3_date": None,
            "period_4_date": None,
            "period_5_date": None,
            "employee_lines_track": "track-3",
        },
    )
    employee = _FakeEntry(
        "emp-1",
        {
            "legal_last_name": "Doe",
            "legal_first_name": "Jane",
            "nis_number": "123-456789",
        },
    )
    line = _FakeEntry(
        "line-3",
        {
            "employee": "emp-1",
            "wage_period_1": 50000,
            "wage_period_2": 0,
            "wage_period_3": 0,
            "wage_period_4": 0,
            "wage_period_5": 0,
            "weeks_worked": 4,
            "employee_contribution": 2800,
            "employer_contribution": 4200,
        },
        track_id="track-3",
    )
    ctx = _FakeGenCtx(filing, [line], {"emp-1": employee})

    result = await nis_generate.generate_txt({"entry_id": "filing-3"}, ctx)

    assert result["ok"] is True
    file_payload = result["file"]
    # build_filename uses the raw (unpadded) reg number — only the in-line
    # fixed-width record content gets lpad'd to 6 chars, not the filename.
    assert file_payload["filename"] == "1234_2014_JAN_M_ABOVE_1.txt"

    import base64

    body = base64.b64decode(file_payload["content_base64"]).decode("cp1252")
    (line_text,) = body.split("\n")
    assert len(line_text) == 168
    assert line_text[0:6] == "001234"
    assert line_text[6:10] == "2014"
    assert line_text[10:12] == "01"
    assert line_text[12:13] == "M"
    assert line_text[13:22] == "123456789"  # SSN, process_ssn-cleaned, right-padded
    assert line_text[22:42] == "Doe".ljust(20)
    assert line_text[42:57] == "Jane".ljust(15)
    # period 1 = 2014-01-15
    assert line_text[57:65] == "20140115"
    # period 2-5 blank
    assert line_text[65:97] == " " * 32
    # wage period 1 = 50000 -> "50000" + "00" cents suffix = "5000000", lpad to 10 with '0'
    assert line_text[97:107] == "0005000000"
    # format_rounded_contribution appends a "00" cents suffix same as wages
    assert line_text[147:157] == "0000280000"  # EE contribution ($2800 + "00")
    assert line_text[157:167] == "0000420000"  # ER contribution ($4200 + "00")
    assert line_text[167:168] == "4"  # weeks worked
    # draft -> generated on a successful Generate click.
    assert filing.custom_fields["status"] == "generated"


@pytest.mark.asyncio
async def test_generate_txt_does_not_downgrade_submitted_status():
    filing = _FakeEntry(
        "filing-4",
        {
            "employer_name": "Acme Co",
            "registration_number": "1234",
            "contribution_year": 2014,
            "contribution_month": "January",
            "schedule_type": "Monthly",
            "period_1_date": "2014-01-15",
            "period_2_date": None,
            "period_3_date": None,
            "period_4_date": None,
            "period_5_date": None,
            "employee_lines_track": "track-4",
            "status": "submitted",
        },
    )
    ctx = _FakeGenCtx(filing, [], {})

    result = await nis_generate.generate_txt({"entry_id": "filing-4"}, ctx)

    assert result["ok"] is True
    assert filing.custom_fields["status"] == "submitted"
    assert ctx.updates == {}
