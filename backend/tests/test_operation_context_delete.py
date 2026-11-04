"""A public delete facade never widens ordinary identity or App scope."""

import pytest
from app.api.errors import InsufficientPermissionsError
from app.models.edges import EXCLUDED_FROM
from app.models.nodes import Entry
from app.services.app_operations.context import OperationContext
from tests.test_resource_location import graph, sibling

pytestmark = pytest.mark.asyncio


async def test_delete_requires_ordinary_authority_owning_scope_and_observed_revision():
    owner, ws, app, track, entry = await graph()
    ctx = OperationContext(
        user_id=owner.id,
        workspace_id=ws.id,
        app_id=app.id,
        scope="tool",
        operation_key="delete_test",
        idempotency_key="original-intent",
        deferred_change_events=[],
    )
    assert not await ctx.delete_entry(
        entry.id, expected_record_revision=entry.record_revision + 1
    )
    assert await Entry.get(entry.id) is not None
    foreign_app, _, foreign_entry = await sibling(owner, ws)
    assert not await ctx.delete_entry(
        foreign_entry.id, expected_record_revision=foreign_entry.record_revision
    )
    # An in-scope role is not authority to delete a different App's record.
    ctx.app_id = "not-owning-app"
    assert not await ctx.delete_entry(
        entry.id, expected_record_revision=entry.record_revision
    )
    ctx.app_id = app.id
    await owner.connect(entry, edge=EXCLUDED_FROM)
    assert not await ctx.delete_entry(
        entry.id, expected_record_revision=entry.record_revision
    )


async def test_readonly_and_delegated_contexts_do_not_gain_delete_adapter():
    owner, ws, app, _, entry = await graph()
    for args in (
        {"read_only": True},
        {"actor_kind": "agent", "actor_id": "agent-test"},
    ):
        ctx = OperationContext(
            user_id=owner.id, workspace_id=ws.id, app_id=app.id, scope="tool", **args
        )
        with pytest.raises(InsufficientPermissionsError):
            await ctx.delete_entry(
                entry.id, expected_record_revision=entry.record_revision
            )
    assert await Entry.get(entry.id) is not None


async def test_delete_defers_events_and_commits_only_after_ordinary_admission():
    owner, ws, app, _, entry = await graph()
    events = []
    ctx = OperationContext(
        user_id=owner.id,
        workspace_id=ws.id,
        app_id=app.id,
        scope="tool",
        operation_key="delete_test",
        idempotency_key="original-intent",
        deferred_change_events=events,
    )
    assert await ctx.delete_entry(
        entry.id, expected_record_revision=entry.record_revision
    )
    assert await Entry.find_one({"id": entry.id}) is None
    assert len(events) == 1 and events[0]["action"] == "entry.delete"


async def test_delete_rejects_missing_durable_intent():
    owner, ws, app, _, entry = await graph()
    ctx = OperationContext(
        user_id=owner.id, workspace_id=ws.id, app_id=app.id, scope="tool"
    )
    assert not await ctx.delete_entry(
        entry.id, expected_record_revision=entry.record_revision
    )
    assert await Entry.get(entry.id) is not None
