"""Independent storage admission ordering for member provisioning."""

import asyncio
from contextvars import Context

import pytest
from jvspatial.api.auth.models import User as AuthUser
from jvspatial.core.context import GraphContext, scoped_default_context_async
from jvspatial.db.factory import create_database

from app.api.errors import InsufficientPermissionsError
from app.models.edges import IS_MEMBER_OF
from app.services.app_operations.transaction_scope import postgres_graph_transaction
from app.services.workspace_membership import add_member
from tests.test_invitation_grant_outcomes import recipient_fixture

pytestmark = [pytest.mark.postgres, pytest.mark.contract, pytest.mark.asyncio]


async def test_held_identity_disable_prevents_later_direct_membership(
    postgres_raw_db,
):
    second = create_database(db_type="postgres", min_size=1, max_size=1)
    task = None
    try:
        async with scoped_default_context_async(GraphContext(database=postgres_raw_db)):
            from app.services.app_graph import ensure_integral_app_graph

            await ensure_integral_app_graph(include_library=False)
            owner, workspace, _, recipient, email = await recipient_fixture()
            recipient.email_verified = True
            recipient.verified_email = email
            await recipient.save()
            for collection in ("node", "edge", "object"):
                await second.find(collection, {"id": ""}, limit=1)
            started = asyncio.Event()

            async def follow():
                async with scoped_default_context_async(GraphContext(database=second)):
                    started.set()
                    return await add_member(
                        workspace_id=workspace.id,
                        actor_id=owner.id,
                        member_id=recipient.id,
                    )

            async with postgres_graph_transaction(postgres_raw_db) as transaction:
                await transaction.find_one_and_update(
                    "object",
                    {"id": recipient.user_id, "entity": AuthUser._entity_name()},
                    {"$set": {"context.is_active": False}},
                )
                task = asyncio.create_task(follow(), context=Context())
                await asyncio.wait_for(started.wait(), timeout=15)
                with pytest.raises(asyncio.TimeoutError):
                    await asyncio.wait_for(asyncio.shield(task), timeout=0.15)
            with pytest.raises(InsufficientPermissionsError):
                await asyncio.wait_for(task, timeout=15)
            rows = await postgres_raw_db.find(
                "edge",
                {
                    "source": recipient.id,
                    "target": workspace.id,
                    "entity": IS_MEMBER_OF._entity_name(),
                },
                limit=2,
            )
            assert rows == []
            identity = await postgres_raw_db.find_one(
                "object", {"id": recipient.user_id}
            )
            assert identity["context"]["is_active"] is False
    finally:
        if task is not None and not task.done():
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
        await second.close()
