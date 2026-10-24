"""SDK discovery ranks the governed index beyond the old first-500 window."""

import pytest

from app.api.errors import BadRequestError
from app.models.edges import CONTAINS, IS_OF_TYPE
from app.models.nodes import Entry, EntryType
from app.services.hooks.registry import ToolContext
from app.services.retrieval.embedding_model import encode_passages, passage_tokenizer
from app.services.retrieval.index_definition import embedding_profile, split_passages
from app.services.retrieval.indexing import index_document
from app.services.retrieval.passage_store import passage_store
from tests.test_resource_location import graph
from tests.test_semantic_indexing_pg import vector_driver

pytestmark = [pytest.mark.integration, pytest.mark.slow, pytest.mark.asyncio]


async def test_app_sdk_discovers_relevant_typed_record_after_first_five_hundred(
    vector_driver,
):
    user, workspace, app, track, _ = await graph()
    wanted = await EntryType.create(
        name="Inspection",
        track_id=track.id,
        form_schema={"_manifest_entry_type_key": "inspection"},
    )
    await track.connect(wanted, edge=CONTAINS)
    other = await EntryType.create(
        name="Other",
        track_id=track.id,
        form_schema={"_manifest_entry_type_key": "other"},
    )
    await track.connect(other, edge=CONTAINS)
    query = "Zephyr compressor seal inspection interval"
    profile = embedding_profile()
    vector = (await encode_passages([query], profile.dimensions))[0]
    opposite = [-x for x in vector]
    tokenizer = await passage_tokenizer()
    store = passage_store(vector_driver, profile)
    target = None
    for i in range(1001):
        is_wanted = i < 501
        title = query if i >= 500 else "Routine maintenance record"
        entry = await Entry.create(
            id=f"n.Entry.z_discovery_{i:04}",
            title=title,
            track_id=track.id,
            type_id=wanted.id if is_wanted else other.id,
        )
        await track.connect(entry, edge=CONTAINS)
        await entry.connect(wanted if is_wanted else other, edge=IS_OF_TYPE)
        document = await index_document(entry)
        passages = split_passages(document["texts"], profile, tokenizer)
        assert await store.replace(
            document, passages, [vector if i >= 500 else opposite for _ in passages]
        )
        if i == 500:
            target = entry
    ctx = ToolContext(user_id=user.id, workspace_id=workspace.id, scope="tool")
    ctx.app_id = app.id
    for mode in ("graph", "semantic", "hybrid"):
        result = await ctx.search_entries(
            query=query,
            track_type=track.id,
            entry_type="inspection",
            mode=mode,
            limit=10,
        )
        assert result["available"], result
        assert (
            result["results"] and result["results"][0]["entry_id"] == target.id
        ), result
        assert all(
            row["entry_id"] < "n.Entry.z_discovery_0501" for row in result["results"]
        )
    narrowed = await ctx.search_entries(
        query=query,
        track_type=track.id,
        entry_type="inspection",
        eligible_entry_ids=["n.Entry.z_discovery_0000"],
        mode="semantic",
    )
    assert all(
        row["entry_id"] == "n.Entry.z_discovery_0000" for row in narrowed["results"]
    )
    assert (
        await ctx.search_entries(
            query=query, track_type=track.id, eligible_entry_ids=[]
        )
    )["results"] == []
    # Selection indexes are explicitly worker-maintained, not built by the query.
    await store.maintain_selection_indexes()
    async with (await store.ensure_schema()).acquire() as connection:
        assert await connection.fetchval(
            "SELECT indisvalid FROM pg_index WHERE indexrelid=to_regclass($1)",
            "passage_type_" + store.suffix,
        )
