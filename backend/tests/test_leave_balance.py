"""Unit tests for automatic leave balance calculations.

Covers:
- Adding time-off requests for an employee.
- Verifying leave balance updates (leave_taken, leave_remaining, currently_on_leave)
  when time-off requests are created or updated.
"""

from __future__ import annotations

from datetime import date, timedelta
from typing import Any, Dict, List

import pytest

from app.models.nodes import Entry, EntryType, Track, Workspace

# Import the hr_app bundle tool directly (not via hook dispatch — unit test).
from app.profiles.hr_app.tools.leave_balance import recalculate


class _TestCtx:
    """Minimal ToolContext stand-in for unit tests."""

    def __init__(self, workspace_id: str):
        self.workspace_id = workspace_id
        self.user_id = "test"
        self.scope = "test"

    async def get_entry_system(self, entry_id: str):
        return await Entry.get(entry_id)

    async def find_entries(self, query: Dict[str, Any]) -> List[Any]:
        # Unit test: Entry has no workspace_id field; query directly.
        return list(await Entry.find(query))

    async def update_entry_fields(
        self, entry_id: str, custom_fields: Dict[str, Any]
    ) -> bool:
        ent = await Entry.get(entry_id)
        if ent is None:
            return False
        ent.custom_fields = {**(ent.custom_fields or {}), **custom_fields}
        await ent.save()
        return True


@pytest.mark.asyncio
async def test_automatic_leave_balance_calculation():
    """Approved leave requests correctly update leave_taken, leave_remaining, currently_on_leave."""
    ws = await Workspace.create(name="Test Company", kind="organization")
    track = await Track.create(
        title="Employees & Leave", owner_id="test-owner", workspace_id=ws.id
    )

    employee_et = await EntryType.create(name="Employee", track_id=track.id)
    employee = await Entry.create(
        track_id=track.id,
        type_id=employee_et.id,
        title="Jane Doe",
        author_id="test-owner",
        custom_fields={
            "leave_allowance": 21.0,
            "leave_taken": 0.0,
            "leave_remaining": 21.0,
            "currently_on_leave": False,
        },
    )

    time_off_et = await EntryType.create(name="Time-off request", track_id=track.id)
    ctx = _TestCtx(ws.id)

    assert employee.custom_fields.get("leave_taken") == 0.0
    assert employee.custom_fields.get("leave_remaining") == 21.0
    assert employee.custom_fields.get("currently_on_leave") is False

    # Draft (requested) — should NOT affect balance.
    req1 = await Entry.create(
        track_id=track.id,
        type_id=time_off_et.id,
        title="Vacation",
        author_id="test-owner",
        custom_fields={
            "employee": employee.id,
            "days": 5.0,
            "status": "requested",
            "start_date": (date.today() + timedelta(days=10)).isoformat(),
            "end_date": (date.today() + timedelta(days=15)).isoformat(),
        },
    )
    await recalculate(
        {
            "entry_id": req1.id,
            "entry_type_id": time_off_et.id,
            "entry_type": "time_off_request",
        },
        ctx,
    )

    employee = await Entry.get(employee.id)
    assert employee.custom_fields.get("leave_taken") == 0.0
    assert employee.custom_fields.get("leave_remaining") == 21.0
    assert employee.custom_fields.get("currently_on_leave") is False

    # Approve — should update balance.
    req1.custom_fields["status"] = "approved"
    await req1.save()
    await recalculate(
        {
            "entry_id": req1.id,
            "entry_type_id": time_off_et.id,
            "entry_type": "time_off_request",
        },
        ctx,
    )

    employee = await Entry.get(employee.id)
    assert employee.custom_fields.get("leave_taken") == 5.0
    assert employee.custom_fields.get("leave_remaining") == 16.0
    assert employee.custom_fields.get("currently_on_leave") is False

    # Second approved request covering today — should set currently_on_leave.
    req2 = await Entry.create(
        track_id=track.id,
        type_id=time_off_et.id,
        title="Sick Leave",
        author_id="test-owner",
        custom_fields={
            "employee": employee.id,
            "days": 2.0,
            "status": "approved",
            "start_date": date.today().isoformat(),
            "end_date": (date.today() + timedelta(days=1)).isoformat(),
        },
    )
    await recalculate(
        {
            "entry_id": req2.id,
            "entry_type_id": time_off_et.id,
            "entry_type": "time_off_request",
        },
        ctx,
    )

    employee = await Entry.get(employee.id)
    assert employee.custom_fields.get("leave_taken") == 7.0
    assert employee.custom_fields.get("leave_remaining") == 14.0
    assert employee.custom_fields.get("currently_on_leave") is True

    # Close out approved leave as completed — leave_taken preserved, not on leave.
    req2.custom_fields["status"] = "completed"
    await req2.save()
    await recalculate(
        {
            "entry_id": req2.id,
            "entry_type_id": time_off_et.id,
            "entry_type": "time_off_request",
        },
        ctx,
    )

    employee = await Entry.get(employee.id)
    assert employee.custom_fields.get("leave_taken") == 7.0
    assert employee.custom_fields.get("leave_remaining") == 14.0
    assert employee.custom_fields.get("currently_on_leave") is False
