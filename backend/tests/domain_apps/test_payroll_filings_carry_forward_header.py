"""Tests for the "set company info once" carry-forward hook
(``app/profiles/payroll_filings/tools/carry_forward_header.py``).

Covers: the primary source (the company_profile track's single record,
so even the very FIRST filing of a type is pre-filled — the prior-filing
copy alone could never do that), the fallback path (copying header fields
from the most recent prior filing of the SAME type when no company_profile
record exists), company_profile taking priority when both are available,
never overwriting a value the preparer already typed, only filling fields
that are actually still empty, and doing nothing on the very first filing
of a type with neither source available.
"""

from __future__ import annotations

import pytest

from app.models.edges import CONTAINS
from app.models.nodes import Entry, EntryType, Track, Workspace
from app.profiles.payroll_app.tools.carry_forward_header import (
    carry_forward_filing_header,
)
from app.services.hooks.registry import ToolContext
from app.utils.time import utc_now_iso


async def _make_filing(
    track: Track, et: EntryType, owner_id: str, **custom_fields
) -> Entry:
    now = utc_now_iso()
    entry = await Entry.create(
        track_id=track.id,
        type_id=et.id,
        title=custom_fields.pop("title", "Filing"),
        author_id=owner_id,
        custom_fields=custom_fields,
        created_at=now,
        updated_at=now,
    )
    await track.connect(entry, edge=CONTAINS, added_at=now)
    return entry


@pytest.mark.asyncio
async def test_carry_forward_first_filing_of_type_is_noop():
    ws = await Workspace.create(
        name="Carry Forward Test Co — First", kind="organization"
    )
    filings_track = await Track.create(
        title="Filings", owner_id="tester", workspace_id=ws.id
    )
    nis_et = await EntryType.create(name="nis_schedule", track_id=filings_track.id)

    filing = await _make_filing(
        filings_track, nis_et, "tester", employer_name="", registration_number=""
    )
    ctx = ToolContext(user_id="tester", workspace_id=ws.id, scope="workspace")
    result = await carry_forward_filing_header(
        {"entry_id": filing.id, "entry_type": "nis_schedule"}, ctx
    )
    assert result["ok"] is True
    assert "no prior filing" in result["reason"]


@pytest.mark.asyncio
async def test_carry_forward_copies_from_most_recent_same_type_filing():
    ws = await Workspace.create(
        name="Carry Forward Test Co — Copy", kind="organization"
    )
    filings_track = await Track.create(
        title="Filings", owner_id="tester", workspace_id=ws.id
    )
    nis_et = await EntryType.create(name="nis_schedule", track_id=filings_track.id)

    older = await _make_filing(
        filings_track,
        nis_et,
        "tester",
        employer_name="Coastal Trading Company Ltd",
        registration_number="004521",
    )
    new_filing = await _make_filing(
        filings_track,
        nis_et,
        "tester",
        employer_name="",
        registration_number="",
    )

    ctx = ToolContext(user_id="tester", workspace_id=ws.id, scope="workspace")
    result = await carry_forward_filing_header(
        {"entry_id": new_filing.id, "entry_type": "nis_schedule"}, ctx
    )
    assert result["ok"] is True
    assert result["carried_forward_from"] == older.id
    assert set(result["updated_fields"]) == {"employer_name", "registration_number"}

    new_filing = await Entry.get(new_filing.id)
    assert new_filing.custom_fields["employer_name"] == "Coastal Trading Company Ltd"
    assert new_filing.custom_fields["registration_number"] == "004521"


@pytest.mark.asyncio
async def test_carry_forward_never_overwrites_a_value_already_set():
    ws = await Workspace.create(
        name="Carry Forward Test Co — No Clobber", kind="organization"
    )
    filings_track = await Track.create(
        title="Filings", owner_id="tester", workspace_id=ws.id
    )
    paye_et = await EntryType.create(name="paye_filing", track_id=filings_track.id)

    await _make_filing(
        filings_track,
        paye_et,
        "tester",
        company_name="Old Employer Inc",
        company_tin="111111111",
        company_address="Old Address",
    )
    # Preparer switched employers mid-workspace and typed the new company
    # name themselves, but left TIN/address blank.
    new_filing = await _make_filing(
        filings_track,
        paye_et,
        "tester",
        company_name="New Employer Ltd",
        company_tin="",
        company_address="",
    )

    ctx = ToolContext(user_id="tester", workspace_id=ws.id, scope="workspace")
    result = await carry_forward_filing_header(
        {"entry_id": new_filing.id, "entry_type": "paye_filing"}, ctx
    )
    assert result["ok"] is True
    assert set(result["updated_fields"]) == {"company_tin", "company_address"}

    new_filing = await Entry.get(new_filing.id)
    assert new_filing.custom_fields["company_name"] == "New Employer Ltd"  # untouched
    assert new_filing.custom_fields["company_tin"] == "111111111"  # carried forward
    assert (
        new_filing.custom_fields["company_address"] == "Old Address"
    )  # carried forward


@pytest.mark.asyncio
async def test_carry_forward_skips_when_all_header_fields_already_set():
    ws = await Workspace.create(
        name="Carry Forward Test Co — All Set", kind="organization"
    )
    filings_track = await Track.create(
        title="Filings", owner_id="tester", workspace_id=ws.id
    )
    nis_et = await EntryType.create(name="nis_schedule", track_id=filings_track.id)

    await _make_filing(
        filings_track,
        nis_et,
        "tester",
        employer_name="Old Co",
        registration_number="999999",
    )
    filing = await _make_filing(
        filings_track,
        nis_et,
        "tester",
        employer_name="Fully Typed Co",
        registration_number="123123",
    )

    ctx = ToolContext(user_id="tester", workspace_id=ws.id, scope="workspace")
    result = await carry_forward_filing_header(
        {"entry_id": filing.id, "entry_type": "nis_schedule"}, ctx
    )
    assert result["ok"] is True
    assert "already set" in result["reason"]

    filing = await Entry.get(filing.id)
    assert filing.custom_fields["employer_name"] == "Fully Typed Co"


@pytest.mark.asyncio
async def test_carry_forward_unknown_entry_type_is_noop():
    ctx = ToolContext(user_id="t", workspace_id="w", scope="workspace")
    result = await carry_forward_filing_header(
        {"entry_id": "does-not-matter", "entry_type": "some_other_type"}, ctx
    )
    assert result["ok"] is True
    assert "no carry-forward header fields" in result["reason"]


async def _make_company_profile(
    workspace_id: str, owner_id: str, **custom_fields
) -> None:
    # Title-matched by find_entries_in_track_type — "Guyana Settings" is the
    # real track title payroll-app's profile.yaml declares (the Settings-menu
    # consolidation folds Pay Calendar / Statutory Rates / Company Profile
    # into one track). The EntryType name must slug to "company_profile" —
    # find_entries_in_track_type's entry_type filter falls back to
    # slug(name) when no _manifest_entry_type_key is present (as here, a
    # raw EntryType.create bypassing real manifest compilation).
    track = await Track.create(
        title="Guyana Settings", owner_id=owner_id, workspace_id=workspace_id
    )
    et = await EntryType.create(name="Company Profile", track_id=track.id)
    now = utc_now_iso()
    entry = await Entry.create(
        track_id=track.id,
        type_id=et.id,
        title="Guyana Company Profile",
        author_id=owner_id,
        custom_fields=custom_fields,
        created_at=now,
        updated_at=now,
    )
    await track.connect(entry, edge=CONTAINS, added_at=now)


@pytest.mark.asyncio
async def test_carry_forward_uses_company_profile_even_on_the_very_first_filing():
    """The prior-filing fallback alone could never fill in the FIRST filing
    of a type (there's nothing prior to copy from) — this is exactly the
    gap company_profile closes."""
    ws = await Workspace.create(
        name="Carry Forward Test Co — Company Profile First", kind="organization"
    )
    filings_track = await Track.create(
        title="Filings", owner_id="tester", workspace_id=ws.id
    )
    nis_et = await EntryType.create(name="nis_schedule", track_id=filings_track.id)
    await _make_company_profile(
        ws.id,
        "tester",
        company_name="Coastal Trading Company Ltd",
        registration_number="004521",
    )

    filing = await _make_filing(
        filings_track, nis_et, "tester", employer_name="", registration_number=""
    )
    ctx = ToolContext(user_id="tester", workspace_id=ws.id, scope="workspace")
    result = await carry_forward_filing_header(
        {"entry_id": filing.id, "entry_type": "nis_schedule"}, ctx
    )
    assert result["ok"] is True
    assert result["carried_forward_from"] == "company_profile"
    assert set(result["updated_fields"]) == {"employer_name", "registration_number"}

    filing = await Entry.get(filing.id)
    assert filing.custom_fields["employer_name"] == "Coastal Trading Company Ltd"
    assert filing.custom_fields["registration_number"] == "004521"


@pytest.mark.asyncio
async def test_carry_forward_prefers_company_profile_over_prior_filing():
    ws = await Workspace.create(
        name="Carry Forward Test Co — Profile Priority", kind="organization"
    )
    filings_track = await Track.create(
        title="Filings", owner_id="tester", workspace_id=ws.id
    )
    paye_et = await EntryType.create(name="paye_filing", track_id=filings_track.id)
    await _make_company_profile(
        ws.id,
        "tester",
        company_name="Current Employer Ltd",
        tin="222222222",
        address="New Address",
    )
    await _make_filing(
        filings_track,
        paye_et,
        "tester",
        company_name="Stale Prior Filing Co",
        company_tin="111111111",
        company_address="Old Address",
    )

    new_filing = await _make_filing(
        filings_track,
        paye_et,
        "tester",
        company_name="",
        company_tin="",
        company_address="",
    )
    ctx = ToolContext(user_id="tester", workspace_id=ws.id, scope="workspace")
    result = await carry_forward_filing_header(
        {"entry_id": new_filing.id, "entry_type": "paye_filing"}, ctx
    )
    assert result["ok"] is True
    assert result["carried_forward_from"] == "company_profile"

    new_filing = await Entry.get(new_filing.id)
    assert new_filing.custom_fields["company_name"] == "Current Employer Ltd"
    assert new_filing.custom_fields["company_tin"] == "222222222"
    assert new_filing.custom_fields["company_address"] == "New Address"
