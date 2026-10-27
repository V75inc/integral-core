"""ACL coverage for ToolContext (Wave 0 / F0)."""

from __future__ import annotations

import pytest

from app.services.hooks.registry import ToolContext


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
async def test_get_related_entries_is_generic_and_permission_filtered():
    from app.models.edges import (
        COLLABORATES_ON,
        CONTAINS,
        EXCLUDED_FROM,
        IS_MEMBER_OF,
        REFERENCES,
    )
    from app.models.nodes import Entry, User
    from app.services.app_graph import catalog_user
    from tests.test_resource_location import graph

    _, workspace, app, track, source = await graph()
    permitted = await Entry.create(
        track_id=track.id, custom_fields={"example_field": "owned by App"}
    )
    denied = await Entry.create(track_id=track.id, custom_fields={"private": True})
    for entry in (permitted, denied):
        await track.connect(entry, edge=CONTAINS)
        await entry.connect(source, edge=REFERENCES)
    reader = await User.create(display_name="Related reader")
    await catalog_user(reader)
    await reader.connect(workspace, edge=IS_MEMBER_OF, role="member")
    await reader.connect(app, edge=COLLABORATES_ON, role="viewer")
    await reader.connect(denied, edge=EXCLUDED_FROM)
    ctx = ToolContext(user_id=reader.id, workspace_id=workspace.id, scope="tool")
    related = await ctx.get_related_entries(
        source.id, edge_type="REFERENCES", direction="in", limit=25
    )
    assert [entry.id for entry in related] == [permitted.id]
    assert related[0].custom_fields["example_field"] == "owned by App"


@pytest.mark.asyncio
async def test_current_profile_is_bound_to_active_principal(monkeypatch):
    from types import SimpleNamespace

    seen = []

    async def resolve(principal):
        seen.append(principal)
        return SimpleNamespace(
            id="n.User.profile",
            display_name="  Example User  ",
            email="private@example.com",
        )

    monkeypatch.setattr(
        "app.services.identity_admission.resolve_current_profile", resolve
    )
    ctx = ToolContext(user_id="o.User.authentication", workspace_id="w1", scope="tool")
    assert await ctx.current_user_profile() == {
        "id": "n.User.profile",
        "display_name": "Example User",
    }
    assert seen == ["o.User.authentication"]


@pytest.mark.asyncio
async def test_current_profile_does_not_hide_identity_failures(monkeypatch):
    async def resolve(principal):
        raise RuntimeError("identity unavailable")

    monkeypatch.setattr(
        "app.services.identity_admission.resolve_current_profile", resolve
    )
    with pytest.raises(RuntimeError, match="identity unavailable"):
        await ToolContext(
            user_id="u1", workspace_id="w1", scope="tool"
        ).current_user_profile()
