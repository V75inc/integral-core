"""An exact typed mutation must not hydrate its App's entire Track inventory."""

import pytest

from app.models.nodes import App
from app.services.app_operations.context import OperationContext
from tests.test_resource_location import graph

pytestmark = [pytest.mark.asyncio, pytest.mark.smoke]


async def test_exact_operation_target_does_not_enumerate_app_tracks(monkeypatch):
    from app.services.entry_create import create_entry_in_track

    owner, workspace, app, track, _ = await graph()
    await create_entry_in_track(track=track, user_id=owner.id, title="Seed type")
    original = App.nodes
    broad_calls = []

    async def bounded(self, *args, **kwargs):
        if kwargs.get("node") == ["Track"] and not kwargs.get("limit"):
            broad_calls.append(self.id)
            raise AssertionError("Unbounded App Track hydration")
        return await original(self, *args, **kwargs)

    monkeypatch.setattr(App, "nodes", bounded)
    context = OperationContext(
        user_id=owner.id,
        workspace_id=workspace.id,
        app_id=app.id,
        scope=f"app:{app.id}",
    )
    created = await context.create_entry(
        track_id=track.id, entry_type_key="post", title="Exact target", custom_fields={}
    )
    assert created is not None
    assert await context.update_entry_fields(created.id, {}, title="Updated target")
    updated, reason = await context.conditional_update_entry_fields(
        created.id, state_field="missing", expected_state="not-present", updates={}
    )
    assert updated is False and reason == "state_conflict"
    assert broad_calls == []
