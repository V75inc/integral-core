"""Tests for recurring pay-period support (payroll structural redesign) —
``compute_next_period`` (read-only) and ``create_next_pay_run`` on
``tools/pay_run_cadence.py``.
"""

from __future__ import annotations

import pytest

from app.profiles.payroll_app.tools.pay_run_cadence import (
    compute_next_period,
    compute_upcoming_periods,
    create_next_pay_run,
)


class _FakeEntry:
    def __init__(self, entry_id, custom_fields, title=""):
        self.id = entry_id
        self.custom_fields = custom_fields
        self.title = title


class _FakeCtx:
    def __init__(self, *, calendars=None, pay_runs=None):
        self._calendars = calendars or []
        self._pay_runs = pay_runs or []
        self.created_entries = []
        self._next_id = 1

    async def find_entries_in_track_type(self, track_type):
        return {"Pay Calendar": self._calendars, "Guyana Pay Runs": self._pay_runs}.get(
            track_type, []
        )

    async def find_track_id_by_title(self, track_type):
        return "pay-runs-track" if track_type == "Guyana Pay Runs" else None

    async def create_entry(
        self, *, track_id, entry_type_key, title, custom_fields=None
    ):
        entry = _FakeEntry(
            f"pay-run-{self._next_id}", dict(custom_fields or {}), title=title
        )
        self._next_id += 1
        self.created_entries.append(entry)
        return entry


def _calendar(cadence="monthly", anchor="2026-01-01", offset=5):
    return _FakeEntry(
        "cal-1",
        {
            "cadence": cadence,
            "anchor_period_start": anchor,
            "pay_date_offset_days": offset,
        },
    )


@pytest.mark.asyncio
async def test_compute_next_period_no_calendar_is_clean_failure():
    ctx = _FakeCtx()
    result = await compute_next_period({}, ctx)
    assert result["ok"] is False
    assert "Pay Calendar" in result["reason"]


@pytest.mark.asyncio
async def test_compute_next_period_monthly_from_anchor_when_no_prior_runs():
    ctx = _FakeCtx(calendars=[_calendar(cadence="monthly", anchor="2026-03-15")])
    result = await compute_next_period({}, ctx)

    assert result["ok"] is True
    # Anchor's month is used, normalized to the 1st — first period ever.
    assert result["period_start"] == "2026-03-01"
    assert result["period_end"] == "2026-03-31"
    assert result["pay_date"] == "2026-04-05"


@pytest.mark.asyncio
async def test_compute_next_period_monthly_continues_from_latest_pay_run():
    ctx = _FakeCtx(
        calendars=[_calendar(cadence="monthly", anchor="2026-01-01")],
        pay_runs=[
            _FakeEntry(
                "pr-1", {"period_start": "2026-05-01", "period_end": "2026-05-31"}
            )
        ],
    )
    result = await compute_next_period({}, ctx)

    assert result["ok"] is True
    assert result["period_start"] == "2026-06-01"
    assert result["period_end"] == "2026-06-30"
    assert result["pay_date"] == "2026-07-05"


@pytest.mark.asyncio
async def test_compute_next_period_picks_the_latest_of_several_pay_runs():
    ctx = _FakeCtx(
        calendars=[_calendar(cadence="monthly", anchor="2026-01-01")],
        pay_runs=[
            _FakeEntry(
                "pr-1", {"period_start": "2026-02-01", "period_end": "2026-02-28"}
            ),
            _FakeEntry(
                "pr-2", {"period_start": "2026-04-01", "period_end": "2026-04-30"}
            ),
            _FakeEntry(
                "pr-3", {"period_start": "2026-03-01", "period_end": "2026-03-31"}
            ),
        ],
    )
    result = await compute_next_period({}, ctx)
    assert result["period_start"] == "2026-05-01"


@pytest.mark.asyncio
async def test_compute_next_period_biweekly():
    ctx = _FakeCtx(
        calendars=[_calendar(cadence="biweekly", anchor="2026-01-05", offset=3)],
    )
    result = await compute_next_period({}, ctx)
    assert result["ok"] is True
    assert result["period_start"] == "2026-01-05"
    assert result["period_end"] == "2026-01-18"  # 14-day period
    assert result["pay_date"] == "2026-01-21"


@pytest.mark.asyncio
async def test_compute_next_period_weekly():
    ctx = _FakeCtx(
        calendars=[_calendar(cadence="weekly", anchor="2026-01-05", offset=2)]
    )
    result = await compute_next_period({}, ctx)
    assert result["ok"] is True
    assert result["period_start"] == "2026-01-05"
    assert result["period_end"] == "2026-01-11"  # 7-day period
    assert result["pay_date"] == "2026-01-13"


@pytest.mark.asyncio
async def test_compute_next_period_rejects_unknown_cadence():
    ctx = _FakeCtx(calendars=[_calendar(cadence="daily")])
    result = await compute_next_period({}, ctx)
    assert result["ok"] is False


@pytest.mark.asyncio
async def test_create_next_pay_run_creates_a_draft():
    ctx = _FakeCtx(calendars=[_calendar(cadence="monthly", anchor="2026-06-01")])
    result = await create_next_pay_run({}, ctx)

    assert result["ok"] is True
    assert len(ctx.created_entries) == 1
    created = ctx.created_entries[0]
    assert created.custom_fields["status"] == "draft"
    assert created.custom_fields["period_start"] == "2026-06-01"
    assert created.custom_fields["period_end"] == "2026-06-30"
    assert created.title == "June 2026 pay run"
    assert result["entry_id"] == created.id


@pytest.mark.asyncio
async def test_create_next_pay_run_always_advances_past_an_existing_run():
    """No separate duplicate guard needed: compute_next_period always
    derives a period strictly after the latest existing Pay Run, so a
    second call in a row (e.g. re-clicking the button) advances forward
    rather than re-proposing the same period."""
    ctx = _FakeCtx(
        calendars=[_calendar(cadence="monthly", anchor="2026-06-01")],
        pay_runs=[
            _FakeEntry(
                "existing-1", {"period_start": "2026-06-01", "period_end": "2026-06-30"}
            )
        ],
    )
    result = await create_next_pay_run({}, ctx)

    assert result["ok"] is True
    assert result["period_start"] == "2026-07-01"
    assert ctx.created_entries[0].custom_fields["period_start"] == "2026-07-01"


@pytest.mark.asyncio
async def test_create_next_pay_run_propagates_compute_failure():
    ctx = _FakeCtx()  # no calendar configured
    result = await create_next_pay_run({}, ctx)
    assert result["ok"] is False
    assert ctx.created_entries == []


# ── compute_upcoming_periods — multi-frequency support ──────────────────


def _calendar_with_id(cal_id, cadence, anchor="2026-01-01", offset=5):
    return _FakeEntry(
        cal_id,
        {
            "cadence": cadence,
            "anchor_period_start": anchor,
            "pay_date_offset_days": offset,
        },
    )


@pytest.mark.asyncio
async def test_compute_upcoming_periods_no_frequency_uses_first_calendar():
    """Back-compat: a single-cadence company (no frequency passed) behaves
    exactly as before this feature — takes the first calendar found."""
    ctx = _FakeCtx(
        calendars=[_calendar_with_id("cal-1", "monthly", anchor="2026-03-01")]
    )
    result = await compute_upcoming_periods({"count": 2}, ctx)
    assert result["ok"] is True
    assert result["cadence"] == "monthly"
    assert result["periods"][0]["period_start"] == "2026-03-01"


@pytest.mark.asyncio
async def test_compute_upcoming_periods_selects_matching_frequency_calendar():
    """Two Pay Calendar entries (monthly + weekly) coexist — passing
    frequency picks the RIGHT one, not just the first."""
    ctx = _FakeCtx(
        calendars=[
            _calendar_with_id("cal-monthly", "monthly", anchor="2026-01-01"),
            _calendar_with_id("cal-weekly", "weekly", anchor="2026-01-05", offset=2),
        ]
    )
    result = await compute_upcoming_periods({"frequency": "weekly", "count": 1}, ctx)
    assert result["ok"] is True
    assert result["cadence"] == "weekly"
    assert result["periods"][0]["period_start"] == "2026-01-05"
    assert result["periods"][0]["period_end"] == "2026-01-11"


@pytest.mark.asyncio
async def test_compute_upcoming_periods_unknown_frequency_is_clean_failure():
    ctx = _FakeCtx(calendars=[_calendar_with_id("cal-1", "monthly")])
    result = await compute_upcoming_periods({"frequency": "weekly"}, ctx)
    assert result["ok"] is False
    assert "weekly" in result["reason"]


@pytest.mark.asyncio
async def test_compute_upcoming_periods_scopes_recurrence_to_same_frequency_pay_runs():
    """A monthly Pay Run's period_end must not anchor a weekly cadence's
    'next period' math, and vice versa — each frequency's recurrence reads
    only its OWN Pay Runs once frequency is passed."""
    ctx = _FakeCtx(
        calendars=[
            _calendar_with_id("cal-monthly", "monthly", anchor="2026-01-01"),
            _calendar_with_id("cal-weekly", "weekly", anchor="2026-01-05", offset=2),
        ],
        pay_runs=[
            _FakeEntry(
                "pr-monthly",
                {
                    "frequency": "monthly",
                    "period_start": "2026-05-01",
                    "period_end": "2026-05-31",
                },
            ),
        ],
    )
    result = await compute_upcoming_periods({"frequency": "weekly", "count": 1}, ctx)
    assert result["ok"] is True
    # Weekly cadence ignores the monthly Pay Run entirely — starts fresh
    # from its own calendar's anchor, not 2026-06-01.
    assert result["periods"][0]["period_start"] == "2026-01-05"
