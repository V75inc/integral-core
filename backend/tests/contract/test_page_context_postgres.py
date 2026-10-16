"""Actual-store UI resource identity and active workspace qualification."""

import pytest
from jvspatial.core.context import GraphContext, scoped_default_context_async

from app.services.chat_page_context import verify_page_snapshot
from tests.test_resource_location import graph

pytestmark = [pytest.mark.asyncio, pytest.mark.contract, pytest.mark.postgres]


async def test_current_page_focus_and_rows_are_rooted_and_scoped(postgres_raw_db):
    """An owned App resolves canonical labels; a foreign scope returns no metadata."""
    async with scoped_default_context_async(GraphContext(database=postgres_raw_db)):
        owner, workspace, app, track, entry = await graph()
        snapshot = {
            "focused_app_id": app.id,
            "focused_track_id": track.id,
            "focused_entry_id": entry.id,
            "metadata": {"app_name": "Untrusted client label"},
            "visible_data": {
                "entries": [{"id": entry.id, "title": "Untrusted entry label"}]
            },
        }
        result = await verify_page_snapshot(
            snapshot, user_id=owner.id, workspace_id=workspace.id
        )
        assert result is not None
        assert result["metadata"]["app_name"] == app.name
        assert result["visible_data"]["entries"][0]["title"] == entry.title
        assert (
            await verify_page_snapshot(
                {"focused_app_id": app.id},
                user_id=owner.id,
                workspace_id="foreign-workspace",
            )
            is None
        )
