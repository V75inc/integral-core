"""Exact App field projections retain current authority without node hydration."""

from unittest.mock import AsyncMock

import pytest

from tests.test_app_record_read import setup

pytestmark = pytest.mark.asyncio


async def test_exact_field_values_preserve_scope_without_hydration(monkeypatch):
    """Number-like values stay exact while App and workspace authority remain live."""
    from app.services import entry_access_projection as projection

    _, _, app, track, entry, ctx = await setup()
    exact = "INV-" + "9" * 200
    entry.custom_fields = {"document_number": exact}
    await entry.save()
    monkeypatch.setattr(
        projection,
        "hydrate_records",
        AsyncMock(side_effect=AssertionError("no hydration")),
    )
    values = await ctx.find_entry_values(
        {"context.track_id": track.id}, ["document_number"]
    )
    assert values == [{"id": entry.id, "document_number": exact}]
    _, _, foreign_app, foreign_track, _, _ = await setup()
    assert foreign_app.id != app.id
    assert (
        await ctx.find_entry_values(
            {"context.track_id": foreign_track.id}, ["document_number"]
        )
        == []
    )
    for invalid in (["_private"], ["id"], [123]):
        with pytest.raises(ValueError):
            await ctx.find_entry_values({}, invalid)


async def test_workspace_hook_projection_uses_current_workspace_authority():
    """Save hooks lack app_id, just like the existing find_entries facade."""
    from app.services.hooks.registry import ToolContext

    owner, ws, _, track, entry, _ = await setup()
    hook = ToolContext(user_id=owner.id, workspace_id=ws.id, scope=f"entry:{entry.id}")
    assert not hasattr(hook, "app_id")
    unscoped = ToolContext(user_id=owner.id, workspace_id="", scope="tool")
    assert await unscoped.get_entry(entry.id) is None
    with pytest.raises(ValueError, match="workspace context"):
        await unscoped.find_entry_values({"id": entry.id}, ["document_number"])
    entry.custom_fields = {"document_number": "INV-0042"}
    await entry.save()
    assert await hook.find_entry_values(
        {"context.track_id": track.id}, ["document_number"]
    ) == [{"id": entry.id, "document_number": "INV-0042"}]
    _, _, _, foreign_track, _, _ = await setup()
    assert (
        await hook.find_entry_values(
            {"context.track_id": foreign_track.id}, ["document_number"]
        )
        == []
    )
