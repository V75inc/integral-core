"""run_entry_save_hooks entry_type resolution — found via live browser testing.

hooks[].match.entry_type in profile.yaml is always the manifest ``key:``
(e.g. ``nis_schedule_line``). The dispatch payload used to build ``entry_type``
by slugifying the EntryType's display ``name`` instead (e.g. "Employee Line"
-> "employee_line") — silently never matching hook bindings for any entry
type whose display name doesn't happen to slugify identically to its
manifest key. hr_app's hooks "worked" only because "Time Off Request" ->
"time_off_request" happens to coincide with its key. payroll_filings' NIS/PAYE
line entry types are both named "Employee Line" (key: nis_schedule_line /
paye_filing_line) — a real, differently-named case that exposed the bug:
nis_calc_row never fired on row edits, no error, no log line, computed
columns stayed permanently null.
"""

from __future__ import annotations

from typing import Any, Dict, List

import pytest

from app.models.nodes import Entry, EntryType, Track
from app.services.hooks import entry_save_runtime, resolver


@pytest.mark.asyncio
async def test_hook_dispatch_prefers_manifest_key_over_slugified_name(monkeypatch):
    captured: List[Dict[str, Any]] = []

    def fake_find_matching_bindings(
        workspace_id, point, payload, explicit_hook_key=None
    ):
        captured.append(payload)
        return []

    monkeypatch.setattr(resolver, "find_matching_bindings", fake_find_matching_bindings)

    track = await Track.create(
        title="Lines", owner_id="user-hooktest", workspace_id="ws-hooktest"
    )
    et = await EntryType.create(
        name="Employee Line",
        icon="user",
        form_schema={
            "_manifest_entry_type_key": "nis_schedule_line",
            "fields": [],
        },
        track_id=track.id,
        is_template=False,
    )
    entry = await Entry.create(
        type_id=et.id,
        title="",
        author_id="user-hooktest",
        track_id=track.id,
        custom_fields={},
    )

    await entry_save_runtime.run_entry_save_hooks(
        entry=entry,
        workspace_id="ws-hooktest",
        actor_id="user-hooktest",
        hook_point="entry.update",
    )

    assert len(captured) == 1
    # The bug: this used to be "employee_line" (slugified display name).
    assert captured[0]["entry_type"] == "nis_schedule_line"


@pytest.mark.asyncio
async def test_hook_dispatch_falls_back_to_slugified_name_when_no_manifest_key(
    monkeypatch,
):
    """Back-compat: entry types with no manifest key (ad-hoc, user-created via
    the UI, not from a profile.yaml) keep today's slugified-name behavior."""
    captured: List[Dict[str, Any]] = []

    def fake_find_matching_bindings(
        workspace_id, point, payload, explicit_hook_key=None
    ):
        captured.append(payload)
        return []

    monkeypatch.setattr(resolver, "find_matching_bindings", fake_find_matching_bindings)

    track = await Track.create(
        title="Ad Hoc", owner_id="user-hooktest2", workspace_id="ws-hooktest2"
    )
    et = await EntryType.create(
        name="Time Off Request",
        icon="calendar",
        form_schema={"fields": []},
        track_id=track.id,
        is_template=False,
    )
    entry = await Entry.create(
        type_id=et.id,
        title="",
        author_id="user-hooktest2",
        track_id=track.id,
        custom_fields={},
    )

    await entry_save_runtime.run_entry_save_hooks(
        entry=entry,
        workspace_id="ws-hooktest2",
        actor_id="user-hooktest2",
        hook_point="entry.update",
    )

    assert len(captured) == 1
    assert captured[0]["entry_type"] == "time_off_request"
