"""Independent PostgreSQL authority changes precede shared Entry effects."""

import asyncio
from contextvars import Context

import pytest
from jvspatial.core.context import GraphContext, scoped_default_context_async
from jvspatial.db.factory import create_database

from app.api.errors import InsufficientPermissionsError, ResourceNotFoundError
from app.models.edges import COLLABORATES_ON, CONTAINS
from app.models.nodes import Entry, EntryType
from app.services.app_graph import (
    ensure_integral_app_graph,
    ensure_track_attached_operational_model,
)
from app.services.app_operations.transaction_scope import postgres_graph_transaction
from app.services.authorization_scope import authorization_scope
from app.services.entry_create import create_entry_in_track
from app.services.public_entry_effect import public_entry_write_scope
from app.services.share_links import mint_share_link
from tests.test_access_lifecycle import verified_subject
from tests.test_resource_location import graph

pytestmark = [pytest.mark.postgres, pytest.mark.contract, pytest.mark.asyncio]


async def typed_graph():
    await ensure_integral_app_graph(include_library=False)
    owner, workspace, app, track, _ = await graph()
    model = await ensure_track_attached_operational_model(track)
    entry_type = await EntryType.create(
        name="Post", track_id=track.id, form_schema={"fields": []}
    )
    await model.connect(entry_type, edge=CONTAINS)
    return owner, workspace, track, entry_type


async def test_held_primary_disable_prevents_shared_entry_creation(postgres_raw_db):
    second = create_database(db_type="postgres", min_size=1, max_size=1)
    task = None
    try:
        async with scoped_default_context_async(GraphContext(database=postgres_raw_db)):
            _, workspace, track, entry_type = await typed_graph()
            editor = await verified_subject(workspace)
            await editor.connect(track, edge=COLLABORATES_ON, role="editor")
            for collection in ("node", "edge", "object"):
                await second.find(collection, {"id": ""}, limit=1)
            started = asyncio.Event()

            async def follow():
                async with scoped_default_context_async(GraphContext(database=second)):
                    started.set()
                    return await create_entry_in_track(
                        track=track,
                        user_id=editor.id,
                        type_id=entry_type.id,
                        title="Denied disabled writer",
                    )

            async with postgres_graph_transaction(postgres_raw_db) as transaction:
                await transaction.find_one_and_update(
                    "object",
                    {"id": editor.user_id},
                    {"$set": {"context.is_active": False}},
                )
                task = asyncio.create_task(follow(), context=Context())
                await asyncio.wait_for(started.wait(), timeout=15)
                with pytest.raises(asyncio.TimeoutError):
                    await asyncio.wait_for(asyncio.shield(task), timeout=0.15)
            with pytest.raises(InsufficientPermissionsError):
                await asyncio.wait_for(task, timeout=15)
            assert (
                await postgres_raw_db.find(
                    "node",
                    {
                        "entity": Entry._entity_name(),
                        "context.title": "Denied disabled writer",
                    },
                )
                == []
            )
    finally:
        if task is not None and not task.done():
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
        await second.close()


async def test_held_link_revocation_prevents_public_entry_creation(postgres_raw_db):
    second = create_database(db_type="postgres", min_size=1, max_size=1)
    task = None
    try:
        async with scoped_default_context_async(GraphContext(database=postgres_raw_db)):
            owner, workspace, track, entry_type = await typed_graph()
            result = await mint_share_link(
                owner.id, "track", track.id, "viewer", intent="public"
            )
            link_id, token = result["share_link"]["id"], result["token"]
            await postgres_raw_db.find_one_and_update(
                "node",
                {"id": link_id},
                {"$set": {"context.public_permissions": {"create_entries": True}}},
            )
            async with public_entry_write_scope(token, "create_entries"):
                allowed = await create_entry_in_track(
                    track=track,
                    user_id="public",
                    type_id=entry_type.id,
                    title="Allowed public writer",
                )
            assert await postgres_raw_db.get("node", allowed.id) is not None
            for collection in ("node", "edge", "object"):
                await second.find(collection, {"id": ""}, limit=1)
            started = asyncio.Event()

            async def follow():
                async with scoped_default_context_async(GraphContext(database=second)):
                    started.set()
                    async with public_entry_write_scope(token, "create_entries"):
                        return await create_entry_in_track(
                            track=track,
                            user_id="public",
                            type_id=entry_type.id,
                            title="Denied revoked public writer",
                        )

            async with postgres_graph_transaction(postgres_raw_db) as transaction:
                async with authorization_scope(workspace.id, authority_mutation=True):
                    await transaction.find_one_and_update(
                        "node",
                        {"id": link_id},
                        {"$set": {"context.revoked_at": "2026-10-11T00:00:00+00:00"}},
                    )
                    task = asyncio.create_task(follow(), context=Context())
                    await asyncio.wait_for(started.wait(), timeout=15)
                    with pytest.raises(asyncio.TimeoutError):
                        await asyncio.wait_for(asyncio.shield(task), timeout=0.15)
            with pytest.raises(ResourceNotFoundError):
                await asyncio.wait_for(task, timeout=15)
            assert (
                await postgres_raw_db.find(
                    "node",
                    {
                        "entity": Entry._entity_name(),
                        "context.title": "Denied revoked public writer",
                    },
                )
                == []
            )
    finally:
        if task is not None and not task.done():
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
        await second.close()
