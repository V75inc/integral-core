"""Real PostgreSQL metadata populations across the former 512-item cliff."""

import copy
import os
import time
import uuid

import pytest
from jvspatial.core.context import get_default_context

from app.models.edges import HAS_OPERATIONAL_MODEL
from app.models.nodes import OperationalModel
from app.services import resource_metadata_index as index
from tests.test_resource_location import graph
from tests.test_resource_metadata_index import ready

pytestmark = [
    pytest.mark.asyncio,
    pytest.mark.postgres,
    pytest.mark.skipif(
        os.getenv("INTEGRAL_TEST_DB") != "postgres",
        reason="real PostgreSQL population gate",
    ),
]


@pytest.mark.parametrize("size", [511, 512, 513, 1024])
async def test_large_parent_does_not_trigger_per_child_discovery(size, monkeypatch):
    user, workspace, app, track, _ = await graph()
    context = get_default_context()
    database = context.database
    assert hasattr(database, "bulk_save")
    row = (await database.find("node", {"id": track.id}, limit=1))[0]
    incoming = await database.find("edge", {"target": track.id}, limit=10)
    model = (
        await track.nodes(
            edge=[HAS_OPERATIONAL_MODEL],
            node=[OperationalModel],
            direction="out",
            limit=1,
        )
    )[0]
    model_row = (await database.find("node", {"id": model.id}, limit=1))[0]
    model_edge = (
        await database.find("edge", {"source": track.id, "target": model.id}, limit=1)
    )[0]
    prefix = uuid.uuid4().hex
    nodes, edges = [], []
    for number in range(size):
        key = f"n.Track.metadata-{prefix}-{number:04d}"
        model_key = f"n.OperationalModel.metadata-{prefix}-{number:04d}"
        node = copy.deepcopy(row)
        node["id"] = key
        node["context"]["title"] = f"Renewals {number:04d}"
        nodes.append(node)
        definition = copy.deepcopy(model_row)
        definition["id"] = model_key
        definition["context"]["name"] = f"Renewals schema {number:04d}"
        definition["context"]["name_fold"] = definition["context"]["name"].casefold()
        nodes.append(definition)
        for old in incoming:
            edge = copy.deepcopy(old)
            edge["id"] = (
                f"e.{edge['entity']}.metadata-{prefix}-{number:04d}-{len(edges)}"
            )
            edge["target"] = key
            edges.append(edge)
        edge = copy.deepcopy(model_edge)
        edge.update(
            id=f"e.HAS_OPERATIONAL_MODEL.metadata-{prefix}-{number:04d}",
            source=key,
            target=model_key,
        )
        edges.append(edge)
    # Fixture-only bulk insertion reproduces all rooted source/ownership/schema
    # edges; production mutations remain on the normal Node/service pathways.
    assert await database.bulk_save(context._get_collection_name("n"), nodes) == len(
        nodes
    )
    assert await database.bulk_save(context._get_collection_name("e"), edges) == len(
        edges
    )
    await ready(workspace.id)
    assert (
        await database.count(
            "object", {"entity": index.DOCUMENT, "context.workspace_id": workspace.id}
        )
        == size + 2
    )
    original = index.locate_resources
    sizes = []

    async def observed(kind, ids, **kwargs):
        sizes.append(len(ids))
        return await original(kind, ids, **kwargs)

    monkeypatch.setattr(index, "locate_resources", observed)
    start = time.perf_counter()
    result = await index.indexed_inventory(
        user_id=user.id,
        workspace_id=workspace.id,
        query="Renewals",
        offset=0,
        limit=5,
        ranking_mode="lexical",
    )
    assert [resource.id for resource in result["resources"]] == [app.id]
    assert not result["has_more"]
    assert len(result["tracks"]) <= 4
    assert sum(sizes) <= 5, sizes
    print(
        f"Metadata population {size}: live admitted sources={sum(sizes)}, lookup={time.perf_counter()-start:.3f}s"
    )

    # The same large hierarchy must not be hydrated again to read one Track.
    from app.models.nodes import App
    from app.services.app_operations.context import OperationContext

    materialized = []
    original_page = App.nodes_page

    async def measured_page(here, **kwargs):
        page = await original_page(here, **kwargs)
        if here.id == app.id:
            materialized.append(len(page[0]))
        return page

    monkeypatch.setattr(App, "nodes_page", measured_page)
    ctx = OperationContext(
        user_id=user.id,
        workspace_id=workspace.id,
        app_id=app.id,
        scope="tool",
        read_only=True,
    )
    selected = await ctx._tracks_by_title(f"Renewals {size-1:04d}")
    assert [track.title for track in selected] == [f"Renewals {size-1:04d}"]
    assert sum(materialized) == 1
