"""Current platform role removal fences administrative effects."""

import asyncio
import json
from contextvars import Context
from types import SimpleNamespace

import pytest
from jvspatial.core.context import GraphContext, scoped_default_context_async
from jvspatial.db.factory import create_database
from starlette.requests import Request

from app.api.admin_users import admin_patch_user
from app.api.errors import InsufficientPermissionsError
from app.services.app_graph import ensure_integral_app_graph
from app.services.app_operations.transaction_scope import postgres_graph_transaction
from tests.test_access_lifecycle import verified_subject
from tests.test_resource_location import graph

pytestmark = [pytest.mark.postgres, pytest.mark.contract, pytest.mark.asyncio]


def request(actor, name):
    async def receive():
        return {
            "type": "http.request",
            "body": json.dumps({"display_name": name}).encode(),
        }

    result = Request(
        {"type": "http", "headers": [], "method": "PATCH", "path": "/api/admin/users"},
        receive,
    )
    result.state.user = SimpleNamespace(id=actor.user_id, roles=["admin"])
    return result


async def test_held_platform_demotion_prevents_later_admin_profile_write(
    postgres_raw_db,
):
    second = create_database(db_type="postgres", min_size=1, max_size=1)
    task = None
    try:
        async with scoped_default_context_async(GraphContext(database=postgres_raw_db)):
            await ensure_integral_app_graph(include_library=False)
            _, workspace, _, _, _ = await graph()
            actor = await verified_subject(workspace)
            target = await verified_subject(workspace)
            await postgres_raw_db.find_one_and_update(
                "object",
                {"id": actor.user_id},
                {"$set": {"context.roles": ["admin", "user"]}},
            )
            result = await admin_patch_user(
                request(actor, "Allowed current admin"), target.id
            )
            assert result["user"]["display_name"] == "Allowed current admin"
            for collection in ("node", "edge", "object"):
                await second.find(collection, {"id": ""}, limit=1)
            started = asyncio.Event()

            async def follow():
                async with scoped_default_context_async(GraphContext(database=second)):
                    started.set()
                    return await admin_patch_user(
                        request(actor, "Denied stale admin"), target.id
                    )

            async with postgres_graph_transaction(postgres_raw_db) as transaction:
                await transaction.find_one_and_update(
                    "object",
                    {"id": actor.user_id},
                    {"$set": {"context.roles": ["user"]}},
                )
                task = asyncio.create_task(follow(), context=Context())
                await asyncio.wait_for(started.wait(), 15)
                with pytest.raises(asyncio.TimeoutError):
                    await asyncio.wait_for(asyncio.shield(task), 0.15)
            with pytest.raises(InsufficientPermissionsError):
                await asyncio.wait_for(task, 15)
            row = await postgres_raw_db.get("node", target.id)
            assert row["context"]["display_name"] == "Allowed current admin"
    finally:
        if task is not None and not task.done():
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
        await second.close()
