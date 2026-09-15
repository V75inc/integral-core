"""``provision_compensation.py``'s consumption of a seeded employee's
private ``_seed_*`` starting-compensation fields.

The seed mechanism (``plant_seeds`` in ``app_install.py``) has no way for
one seed entry's ``relation`` field to reference another seed entry's
not-yet-assigned real id, so a seeded Compensation Record can't point
``employee`` at a seeded Employee directly (see profile.yaml's
``payroll_employees`` seed group comment). Routing the real V75 Inc.
starting figures through the Employee entry's own ``_seed_*`` fields and
consuming them here, at auto-provision time, is what makes those seeded
employees demo with proper compensation instead of $0 defaults — this
locks that consumption in.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

MODULE = (
    Path(__file__).parents[2]
    / "app/profiles/payroll-app/tools/provision_compensation.py"
)
SPEC = importlib.util.spec_from_file_location("payroll_provision_compensation", MODULE)
assert SPEC and SPEC.loader
provision = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = provision
SPEC.loader.exec_module(provision)


class _FakeEntry:
    def __init__(self, entry_id, custom_fields, title="", track_id=None):
        self.id = entry_id
        self.custom_fields = custom_fields
        self.title = title
        self.track_id = track_id


class _FakeCtx:
    def __init__(self):
        self.created = []

    async def find_entries_in_track_type(self, track_type):
        return []  # no existing compensation records

    async def find_track_id_by_title(self, title):
        return "comp-track-1"

    async def create_entry(self, *, track_id, entry_type_key, title, custom_fields):
        self.created.append(
            {
                "track_id": track_id,
                "entry_type_key": entry_type_key,
                "title": title,
                "custom_fields": custom_fields,
            }
        )
        return _FakeEntry("comp-1", custom_fields, title=title, track_id=track_id)


@pytest.mark.asyncio
async def test_seeded_employee_gets_real_starting_compensation_not_zero():
    """A seeded employee (V75 Inc.'s own roster) auto-provisions with the
    real Basic salary + all 6 allowances from _seed_* fields, not the
    $0-everything draft a hand-created employee gets."""
    ctx = _FakeCtx()
    employee = _FakeEntry(
        "emp-jack-fisher",
        {
            "job_title": "CEO",
            "_seed_base_salary": 691803,
            "_seed_internet_allowance": 10000,
            "_seed_telephone_allowance": 20000,
            "_seed_traveling_allowance": 20000,
            "_seed_entertainment_allowance": 35000,
            "_seed_specialization_allowance": 40000,
            "_seed_responsibility_allowance": 40000,
        },
        title="Jack Fisher",
    )

    created = await provision._provision_one(ctx, employee)

    assert created is True
    assert len(ctx.created) == 1
    cf = ctx.created[0]["custom_fields"]
    assert cf["employee"] == "emp-jack-fisher"
    assert cf["base_salary"] == 691803
    assert cf["internet_allowance"] == 10000
    assert cf["telephone_allowance"] == 20000
    assert cf["traveling_allowance"] == 20000
    assert cf["entertainment_allowance"] == 35000
    assert cf["specialization_allowance"] == 40000
    assert cf["responsibility_allowance"] == 40000
    # No leftover private seed keys on the Compensation Record itself.
    assert not any(k.startswith("_seed_") for k in cf)


@pytest.mark.asyncio
async def test_seeded_consultant_with_zero_allowances_stays_zero_not_defaulted_away():
    """A $0 seeded allowance (Alondra Russell et al — consultants get no
    allowances in the source sheet) must still come through as an explicit
    0, not be skipped and silently fall back to whatever default existed
    before — ``is not None`` (not truthiness) is what makes that true."""
    ctx = _FakeCtx()
    employee = _FakeEntry(
        "emp-alondra-russell",
        {
            "job_title": "QA Engineer",
            "_seed_base_salary": 500000,
            "_seed_internet_allowance": 0,
            "_seed_telephone_allowance": 0,
            "_seed_traveling_allowance": 0,
            "_seed_entertainment_allowance": 0,
            "_seed_specialization_allowance": 0,
            "_seed_responsibility_allowance": 0,
        },
        title="Alondra Russell",
    )

    await provision._provision_one(ctx, employee)

    cf = ctx.created[0]["custom_fields"]
    assert cf["base_salary"] == 500000
    assert cf["internet_allowance"] == 0
    assert cf["responsibility_allowance"] == 0


@pytest.mark.asyncio
async def test_non_seeded_employee_still_gets_zero_default_unchanged():
    """A hand-created employee (no _seed_* fields at all) must keep the
    original $0-draft behavior exactly — this is a strictly additive
    change, not a rewrite of the default path."""
    ctx = _FakeCtx()
    employee = _FakeEntry("emp-new-hire", {"job_title": "Engineer"}, title="New Hire")

    await provision._provision_one(ctx, employee)

    cf = ctx.created[0]["custom_fields"]
    assert cf["base_salary"] == 0
    assert cf["currency"] == "GYD"
    assert cf["pay_frequency"] == "monthly"
    assert "internet_allowance" not in cf
