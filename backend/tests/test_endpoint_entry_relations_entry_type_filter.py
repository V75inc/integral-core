"""GET /api/entries/{id}/related?relation={field_key}&entry_type={name} —
narrowing a reverse-relation lookup to one source EntryType.

Added alongside the Payroll Employees "Compensation Records" / "Payslips"
reverse-relation regions (see payroll-app/operational-model.yaml) — multiple sibling
entry types there declare a same-named ``employee`` relation field back to
the same roster, so ``relation=employee`` alone can't separate them; this
is the guard that keeps them from merging into one mixed list.
"""

from __future__ import annotations

import pytest
from httpx import AsyncClient

from app.models.edges import COLLABORATES_ON, CONTAINS, IS_MEMBER_OF
from app.models.nodes import Entry, EntryType, Track, User, Workspace
from app.utils.time import utc_now_iso


async def _make_fixture(owner: User):
    now = utc_now_iso()
    ws = await Workspace.create(
        kind="organization",
        workspace_type="company",
        name="WS Rel Entry Type Filter",
        name_fold="ws rel entry type filter",
        created_at=now,
        updated_at=now,
    )
    await owner.connect(ws, edge=IS_MEMBER_OF, role="owner", added_at=now)

    roster_track = await Track.create(
        title="Payroll Employees", owner_id=owner.id, workspace_id=ws.id
    )
    employee = await Entry.create(
        title="Jane Employee", body="", track_id=roster_track.id, author_id=owner.id
    )
    await owner.connect(roster_track, edge=COLLABORATES_ON, role="owner", added_at=now)
    await roster_track.connect(employee, edge=CONTAINS, added_at=now)
    await owner.connect(employee, edge=COLLABORATES_ON, role="owner", added_at=now)

    other_track = await Track.create(
        title="Compensation and Payslips", owner_id=owner.id, workspace_id=ws.id
    )
    await owner.connect(other_track, edge=COLLABORATES_ON, role="owner", added_at=now)

    payslip_et = await EntryType.create(name="Payslip", track_id=other_track.id)
    comp_et = await EntryType.create(
        name="Compensation Record", track_id=other_track.id
    )

    payslip = await Entry.create(
        title="2026-06 Payslip",
        body="",
        track_id=other_track.id,
        author_id=owner.id,
        type_id=payslip_et.id,
        custom_fields={"employee": employee.id},
    )
    await other_track.connect(payslip, edge=CONTAINS, added_at=now)
    await owner.connect(payslip, edge=COLLABORATES_ON, role="owner", added_at=now)

    comp = await Entry.create(
        title="2026-01 Compensation",
        body="",
        track_id=other_track.id,
        author_id=owner.id,
        type_id=comp_et.id,
        custom_fields={"employee": employee.id},
    )
    await other_track.connect(comp, edge=CONTAINS, added_at=now)
    await owner.connect(comp, edge=COLLABORATES_ON, role="owner", added_at=now)

    return {"employee": employee, "payslip": payslip, "compensation": comp}


@pytest.mark.asyncio
async def test_entry_type_filter_separates_same_field_key_siblings(
    authenticated_client: AsyncClient, test_user
):
    """``entry_type`` narrows a reverse-relation list to one sibling type."""
    fx = await _make_fixture(test_user)

    for source_id, relation in (
        (fx["payslip"].id, "employee"),
        (fx["compensation"].id, "employee"),
    ):
        link_resp = await authenticated_client.post(
            f"/api/entries/{fx['employee'].id}/related/link",
            json={"source_id": source_id, "relation": relation},
        )
        assert link_resp.status_code == 200, link_resp.text

    # No entry_type filter — both siblings come back, since they share the
    # exact same field_key ("employee") on their REFERENCES edge.
    unfiltered = await authenticated_client.get(
        f"/api/entries/{fx['employee'].id}/related",
        params={"relation": "employee"},
    )
    assert unfiltered.status_code == 200, unfiltered.text
    unfiltered_ids = {e["id"] for e in unfiltered.json()["entries"]}
    assert unfiltered_ids == {fx["payslip"].id, fx["compensation"].id}

    # entry_type=payslip narrows to just the Payslip sibling.
    payslips_only = await authenticated_client.get(
        f"/api/entries/{fx['employee'].id}/related",
        params={"relation": "employee", "entry_type": "payslip"},
    )
    assert payslips_only.status_code == 200, payslips_only.text
    payslip_ids = {e["id"] for e in payslips_only.json()["entries"]}
    assert payslip_ids == {fx["payslip"].id}

    # entry_type=compensation_record (slug of "Compensation Record") narrows
    # to just the Compensation Record sibling.
    comp_only = await authenticated_client.get(
        f"/api/entries/{fx['employee'].id}/related",
        params={"relation": "employee", "entry_type": "compensation_record"},
    )
    assert comp_only.status_code == 200, comp_only.text
    comp_ids = {e["id"] for e in comp_only.json()["entries"]}
    assert comp_ids == {fx["compensation"].id}
