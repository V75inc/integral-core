"""Tests for ``nis_paye_for_pay_run.py`` — the Guyana Payroll merge's
"generate NIS/PAYE when we do a pay run" feature:
``generate_nis_paye_filings_for_pay_run`` (auto-called at the end of
Finalize Pay Run) and ``export_nis_paye_for_pay_run`` (the Pay Run's own
single "Export NIS/PAYE" action-bar button, format dropdown).
"""

from __future__ import annotations

import pytest

from app.profiles.payroll_app.tools.nis_paye_for_pay_run import (
    export_nis_paye_for_pay_run,
    generate_nis_paye_filings_for_pay_run,
)

_FILINGS_TRACK_ID = "filings-track"


class _FakeEntry:
    def __init__(self, entry_id, custom_fields, title=""):
        self.id = entry_id
        self.custom_fields = custom_fields
        self.title = title


class _FakeCtx:
    def __init__(self, *, pay_run, filings=None):
        self._pay_run = pay_run
        self._filings = filings or []
        self.created_entries = []
        self._next_id = 1

    async def get_entry_system(self, entry_id):
        if self._pay_run.id == entry_id:
            return self._pay_run
        for f in self._filings + self.created_entries:
            if f.id == entry_id:
                return f
        return None

    async def find_entries_in_track_type(self, track_type):
        if track_type == "Filings":
            return self._filings + self.created_entries
        return []

    async def find_track_id_by_title(self, track_type):
        return _FILINGS_TRACK_ID if track_type == "Filings" else None

    async def find_entries(self, filters):
        return []

    async def update_entry_fields(self, entry_id, fields):
        for e in self._filings + self.created_entries:
            if e.id == entry_id:
                e.custom_fields.update(fields)

    async def create_entry(
        self, *, track_id, entry_type_key, title, custom_fields=None
    ):
        entry = _FakeEntry(
            f"{entry_type_key}-{self._next_id}", dict(custom_fields or {}), title=title
        )
        self._next_id += 1
        self.created_entries.append(entry)
        return entry


def _pay_run():
    return _FakeEntry(
        "pay-run-1",
        {"period_start": "2026-06-01", "period_end": "2026-06-30"},
        title="June 2026 pay run",
    )


@pytest.mark.asyncio
async def test_generate_creates_both_filings_linked_to_pay_run():
    ctx = _FakeCtx(pay_run=_pay_run())
    result = await generate_nis_paye_filings_for_pay_run({"entry_id": "pay-run-1"}, ctx)

    assert result["ok"] is True
    assert result["skipped_existing"] == []
    assert len(ctx.created_entries) == 2

    nis = next(e for e in ctx.created_entries if e.id.startswith("nis_schedule"))
    assert nis.custom_fields["pay_run"] == "pay-run-1"
    assert nis.custom_fields["contribution_year"] == "2026"
    assert nis.custom_fields["contribution_month"] == "June"

    paye = next(e for e in ctx.created_entries if e.id.startswith("paye_filing"))
    assert paye.custom_fields["pay_run"] == "pay-run-1"
    assert paye.custom_fields["year"] == "2026"
    assert paye.custom_fields["period"] == 6


@pytest.mark.asyncio
async def test_generate_no_entry_id_is_clean_failure():
    ctx = _FakeCtx(pay_run=_pay_run())
    result = await generate_nis_paye_filings_for_pay_run({}, ctx)
    assert result["ok"] is False


@pytest.mark.asyncio
async def test_generate_is_idempotent_when_filings_already_linked():
    pay_run = _pay_run()
    existing_nis = _FakeEntry(
        "existing-nis", {"pay_run": "pay-run-1", "contribution_year": "2026"}
    )
    existing_paye = _FakeEntry(
        "existing-paye", {"pay_run": "pay-run-1", "company_tin": "123"}
    )
    ctx = _FakeCtx(pay_run=pay_run, filings=[existing_nis, existing_paye])

    result = await generate_nis_paye_filings_for_pay_run({"entry_id": "pay-run-1"}, ctx)

    assert result["ok"] is True
    assert set(result["skipped_existing"]) == {"nis_schedule", "paye_filing"}
    assert ctx.created_entries == []


@pytest.mark.asyncio
async def test_export_no_linked_filing_is_clean_failure():
    ctx = _FakeCtx(pay_run=_pay_run())
    result = await export_nis_paye_for_pay_run(
        {"entry_id": "pay-run-1", "format": "nis_txt"}, ctx
    )
    assert result["ok"] is False
    assert "Finalize Pay Run" in result["reason"]


@pytest.mark.asyncio
async def test_export_resolves_linked_nis_schedule_for_txt_and_xls():
    pay_run = _pay_run()
    nis = _FakeEntry(
        "nis-1",
        {
            "pay_run": "pay-run-1",
            "contribution_year": "2026",
            "contribution_month": "June",
            "schedule_type": "Monthly",
            "registration_number": "123456",
        },
    )
    ctx = _FakeCtx(pay_run=pay_run, filings=[nis])

    result = await export_nis_paye_for_pay_run(
        {"entry_id": "pay-run-1", "format": "nis_txt"}, ctx
    )
    assert result["ok"] is True
    assert result["file"]["filename"].endswith(".txt")

    result_xls = await export_nis_paye_for_pay_run(
        {"entry_id": "pay-run-1", "format": "nis_xls"}, ctx
    )
    assert result_xls["ok"] is True


@pytest.mark.asyncio
async def test_export_resolves_linked_paye_filing_for_csv():
    pay_run = _pay_run()
    paye = _FakeEntry(
        "paye-1",
        {
            "pay_run": "pay-run-1",
            "company_tin": "999",
            "company_name": "Acme",
            "year": "2026",
            "period": 6,
        },
    )
    ctx = _FakeCtx(pay_run=pay_run, filings=[paye])

    result = await export_nis_paye_for_pay_run(
        {"entry_id": "pay-run-1", "format": "paye_csv"}, ctx
    )
    assert result["ok"] is True
    assert result["file"]["filename"].endswith(".csv")


@pytest.mark.asyncio
async def test_export_defaults_to_nis_txt_when_format_omitted():
    pay_run = _pay_run()
    nis = _FakeEntry(
        "nis-1",
        {
            "pay_run": "pay-run-1",
            "contribution_year": "2026",
            "contribution_month": "June",
            "schedule_type": "Monthly",
            "registration_number": "123456",
        },
    )
    ctx = _FakeCtx(pay_run=pay_run, filings=[nis])
    result = await export_nis_paye_for_pay_run({"entry_id": "pay-run-1"}, ctx)
    assert result["ok"] is True
    assert result["file"]["filename"].endswith(".txt")
