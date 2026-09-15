"""Structural tests for BVI Payroll's ``_statutory_rates.py`` reader —
proves effective-dating picks the latest page in effect for a given date,
and that a payroll_tax_class page SET (multiple rows sharing one
effective_date) is grouped correctly. Uses a minimal fake ``ctx`` (no real
DB) since the reader only calls ``find_entries_in_track_type``.
"""

from __future__ import annotations

from datetime import date
from types import SimpleNamespace
from typing import Any, Dict, List

import pytest

from app.profiles.bvi_payroll.tools._statutory_rates import (  # type: ignore[import]
    classify_rate_page,
    find_payroll_tax_class_page,
    latest_nhi_rate_page,
    latest_payroll_tax_class_pages,
    latest_ssb_rate_page,
    parse_date,
)


def _entry(custom_fields: Dict[str, Any]):
    return SimpleNamespace(custom_fields=custom_fields)


class _FakeCtx:
    def __init__(self, entries: List[Any]):
        self._entries = entries

    async def find_entries_in_track_type(self, track_title: str):
        assert track_title == "BVI Settings"
        return self._entries


def test_parse_date_handles_iso_strings_and_none():
    assert parse_date("2026-01-01") == date(2026, 1, 1)
    assert parse_date(None) is None
    assert parse_date(date(2026, 6, 1)) == date(2026, 6, 1)


def test_classify_rate_page_infers_from_field_shape_without_slug():
    assert (
        classify_rate_page(
            {"ssb_employer_pct": 4.5, "insurable_wage_ceiling_monthly": 3000}
        )
        == "ssb_rate"
    )
    assert classify_rate_page({"employer_class": "small"}) == "payroll_tax_class"
    assert (
        classify_rate_page({"nhi_employer_pct": 3.75, "wage_ceiling_monthly": None})
        == "nhi_rate"
    )
    assert classify_rate_page({"something_else": 1}) is None


@pytest.mark.asyncio
async def test_ssb_reader_picks_latest_page_not_later_than_as_of():
    entries = [
        _entry(
            {
                "ssb_employer_pct": 4.0,
                "ssb_employee_pct": 4.0,
                "effective_date": "2025-01-01",
            }
        ),
        _entry(
            {
                "ssb_employer_pct": 4.5,
                "ssb_employee_pct": 4.5,
                "effective_date": "2026-01-01",
            }
        ),
        _entry(
            {
                "ssb_employer_pct": 5.0,
                "ssb_employee_pct": 5.0,
                "effective_date": "2027-01-01",
            }
        ),
    ]
    ctx = _FakeCtx(entries)
    page = await latest_ssb_rate_page(ctx, date(2026, 6, 1))
    assert page is not None
    assert (
        page["ssb_employer_pct"] == 4.5
    )  # not the 2025 page, not the future 2027 page


@pytest.mark.asyncio
async def test_ssb_reader_returns_none_when_all_pages_are_in_the_future():
    entries = [
        _entry(
            {
                "ssb_employer_pct": 4.5,
                "ssb_employee_pct": 4.5,
                "effective_date": "2030-01-01",
            }
        )
    ]
    ctx = _FakeCtx(entries)
    page = await latest_ssb_rate_page(ctx, date(2026, 1, 1))
    assert page is None


@pytest.mark.asyncio
async def test_nhi_reader_picks_latest_page():
    entries = [
        _entry(
            {
                "nhi_employer_pct": 3.0,
                "wage_ceiling_monthly": None,
                "effective_date": "2025-01-01",
            }
        ),
        _entry(
            {
                "nhi_employer_pct": 3.75,
                "wage_ceiling_monthly": None,
                "effective_date": "2026-01-01",
            }
        ),
    ]
    ctx = _FakeCtx(entries)
    page = await latest_nhi_rate_page(ctx, date(2026, 12, 31))
    assert page is not None
    assert page["nhi_employer_pct"] == 3.75


@pytest.mark.asyncio
async def test_payroll_tax_class_reader_groups_the_latest_full_set():
    entries = [
        # An older, complete set at 2025-01-01
        _entry(
            {
                "employer_class": "small",
                "employer_rate_pct": 1.0,
                "effective_date": "2025-01-01",
            }
        ),
        _entry(
            {
                "employer_class": "large",
                "employer_rate_pct": 5.0,
                "effective_date": "2025-01-01",
            }
        ),
        # A newer, complete set at 2026-01-01
        _entry(
            {
                "employer_class": "small",
                "employer_rate_pct": 2.0,
                "effective_date": "2026-01-01",
            }
        ),
        _entry(
            {
                "employer_class": "large",
                "employer_rate_pct": 6.0,
                "effective_date": "2026-01-01",
            }
        ),
    ]
    ctx = _FakeCtx(entries)
    pages = await latest_payroll_tax_class_pages(ctx, date(2026, 6, 1))
    assert len(pages) == 2  # the 2026 SET, not a mix with 2025
    rates_by_class = {p["employer_class"]: p["employer_rate_pct"] for p in pages}
    assert rates_by_class == {"small": 2.0, "large": 6.0}


def test_find_payroll_tax_class_page_matches_by_employer_class():
    pages = [
        {"employer_class": "small", "employer_rate_pct": 2.0},
        {"employer_class": "large", "employer_rate_pct": 6.0},
    ]
    assert find_payroll_tax_class_page(pages, "large")["employer_rate_pct"] == 6.0
    assert find_payroll_tax_class_page(pages, "small")["employer_rate_pct"] == 2.0


def test_find_payroll_tax_class_page_falls_back_to_first_when_no_match():
    pages = [{"employer_class": "small", "employer_rate_pct": 2.0}]
    assert find_payroll_tax_class_page(pages, "nonexistent")["employer_rate_pct"] == 2.0
    assert find_payroll_tax_class_page([], "small") is None
