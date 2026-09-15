"""ToolContext.find_entries must scope entries to the workspace via their Track.

Regression: the old implementation AND-merged ``context.workspace_id`` into the
query, but Entry nodes carry no workspace_id field, so EVERY entry query
returned zero rows — silently breaking bundle tools (the leave-balance recalc
summed 0 approved requests). Entries are scoped through their Track instead.
"""

from __future__ import annotations

from typing import Any, Dict, List

import pytest

from app.services.hooks.registry import ToolContext


class _Entry:
    def __init__(self, _id, track_id):
        self.id, self.track_id = _id, track_id


class _Track:
    def __init__(self, _id, workspace_id):
        self.id, self.workspace_id = _id, workspace_id


@pytest.mark.asyncio
async def test_find_entries_keeps_only_in_workspace(monkeypatch):
    WS = "n.Workspace.ACME"
    entries = [
        _Entry("e1", "t_in"),  # track in workspace → kept
        _Entry("e2", "t_out"),  # track in another workspace → dropped
        _Entry("e3", "t_in"),  # kept
        _Entry("e4", None),  # no track → dropped
    ]
    tracks = {
        "t_in": _Track("t_in", WS),
        "t_out": _Track("t_out", "n.Workspace.OTHER"),
    }

    async def fake_entry_find(query):
        return list(entries)

    async def fake_track_get(tid):
        return tracks.get(tid)

    async def fake_resolve_role(user_id, resource_type, resource_id):
        # find_entries also applies a per-entry permission gate. These entries
        # are fakes with no graph presence, so the real resolver returns None
        # and filters every row out — which would mask the workspace-scoping
        # behaviour this test exists to pin. Grant a role so the assertion is
        # about Track-based scoping and nothing else; the permission gate has
        # its own coverage in the access-control suites.
        return "editor"

    monkeypatch.setattr("app.models.nodes.Entry.find", staticmethod(fake_entry_find))
    monkeypatch.setattr("app.models.nodes.Track.get", staticmethod(fake_track_get))
    monkeypatch.setattr(
        "app.services.permissions.resolve_role", staticmethod(fake_resolve_role)
    )

    ctx = ToolContext(user_id="u1", workspace_id=WS, scope="entry:e1")

    got = await ctx.find_entries({"context.type_id": "n.EntryType.X"})
    assert [e.id for e in got] == ["e1", "e3"]
