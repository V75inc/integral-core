"""Concurrent workspace counters use the shared store's atomic updates."""

import asyncio

import pytest
from jvspatial.core.context import GraphContext, scoped_default_context_async

from app.models.edges import CONTAINS
from app.models.nodes import Entry, Track, Workspace
from app.services.app_graph import (
    catalog_track,
    catalog_workspace,
    ensure_integral_app_graph,
)
from app.services.workspace_storage_usage import (
    decrement_for_entry,
    get_usage,
    increment,
    recalculate_for_workspace,
)

pytestmark = [pytest.mark.contract, pytest.mark.postgres, pytest.mark.asyncio]


async def test_concurrent_increments_and_decrements_do_not_overwrite_workspace(
    postgres_raw_db,
):
    """Each atomic delta survives concurrent writers and preserves other fields."""
    async with scoped_default_context_async(GraphContext(database=postgres_raw_db)):
        await ensure_integral_app_graph(include_library=False)
        workspace = await Workspace.create(
            name="Storage accounting", kind="organization"
        )
        await catalog_workspace(workspace)
        track = await Track.create(title="Documents", workspace_id=workspace.id)
        await catalog_track(track)
        entry = await Entry.create(title="Report", track_id=track.id)
        await track.connect(entry, edge=CONTAINS)
        await asyncio.gather(*(increment(workspace.id, 100) for _ in range(30)))
        assert (await get_usage(workspace.id)).bytes_used == 3000
        assert (await Workspace.get(workspace.id)).storage_bytes_used == 3000

        await postgres_raw_db.find_one_and_update(
            "node", {"id": workspace.id}, {"$set": {"context.name": "Renamed"}}
        )
        await asyncio.gather(
            *(increment(workspace.id, 100) for _ in range(10)),
            *(decrement_for_entry(entry, 50) for _ in range(10)),
        )
        stored = await postgres_raw_db.get("node", workspace.id)
        assert stored["context"]["storage_bytes_used"] == 3500
        assert stored["context"]["name"] == "Renamed"
        assert (await get_usage(workspace.id)).bytes_used == 3500

        await asyncio.gather(*(decrement_for_entry(entry, 1000) for _ in range(5)))
        assert (await get_usage(workspace.id)).bytes_used == 0

        await postgres_raw_db.find_one_and_update(
            "node", {"id": workspace.id}, {"$unset": {"context.storage_bytes_used": ""}}
        )
        assert (await recalculate_for_workspace(workspace.id)).bytes_used == 0
        await increment(workspace.id, 5)
        assert (await get_usage(workspace.id)).bytes_used == 5
