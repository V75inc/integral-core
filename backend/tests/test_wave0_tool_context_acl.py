"""ACL coverage for ToolContext (Wave 0 / F0)."""

from __future__ import annotations

from typing import Any, Dict, Optional

import pytest

from app.services.hooks.registry import ToolContext


class _Entry:
    def __init__(self, eid: str, custom_fields: Optional[Dict[str, Any]] = None):
        self.id = eid
        self.track_id = "t1"
        self.custom_fields = custom_fields or {}

    async def nodes(self, **_kwargs):
        return []


@pytest.mark.asyncio
async def test_find_entries_in_track_type_empty_without_tracks(monkeypatch):
    async def fake_tracks(*_a, **_k):
        return []

    monkeypatch.setattr(
        "app.services.hooks.registry.ToolContext._tracks_by_title",
        fake_tracks,
    )
    ctx = ToolContext(user_id="u1", workspace_id="w1", scope="entry:e1")
    assert await ctx.find_entries_in_track_type("Employees") == []


@pytest.mark.asyncio
async def test_get_related_entries_is_generic_and_permission_filtered(monkeypatch):
    source = _Entry("e_source")
    permitted = _Entry("e_related", {"example_field": "owned by App"})
    denied = _Entry("e_denied", {"private": True})

    async def fake_entry_get(eid):
        return {"e_source": source}.get(eid)

    async def fake_track_get(_tid):
        return type("TrackStub", (), {"workspace_id": "w1"})()

    async def fake_nodes(**kwargs):
        assert kwargs == {
            "edge": ["REFERENCES"],
            "direction": "in",
            "node": ["Entry"],
            "limit": 25,
        }
        return [permitted, denied]

    source.nodes = fake_nodes  # type: ignore[attr-defined]

    async def fake_resolve_role(_user_id, _rtype, resource_id):
        return "viewer" if resource_id in {"e_source", "e_related"} else None

    monkeypatch.setattr("app.models.nodes.Entry.get", staticmethod(fake_entry_get))
    monkeypatch.setattr("app.models.nodes.Track.get", staticmethod(fake_track_get))
    monkeypatch.setattr(
        "app.services.permissions.resolve_role", staticmethod(fake_resolve_role)
    )

    ctx = ToolContext(user_id="u1", workspace_id="w1", scope="entry:e_source")
    related = await ctx.get_related_entries(
        "e_source", edge_type="REFERENCES", direction="in", limit=25
    )
    assert related == [permitted]
    assert related[0].custom_fields["example_field"] == "owned by App"
