"""Saved-view agent mutations participate in the common durable effect fence."""

from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest


@pytest.mark.asyncio
async def test_save_view_records_result_inside_staged_effect(monkeypatch):
    from app.agentive import staging_executors
    from app.agentive.services import staging_execution
    from app.models.nodes import Track
    from app.services import agent_insights

    result = {"action": "update_view", "view_id": "view-1", "name": "Board"}
    complete_calls = []
    effect_calls = []

    async def get_track(track_id):
        assert track_id == "track-1"
        return SimpleNamespace(workspace_id="workspace-1")

    @asynccontextmanager
    async def staged_effect(**kwargs):
        effect_calls.append(kwargs)

        async def complete(value):
            complete_calls.append(value)

        yield complete

    monkeypatch.setattr(Track, "get", get_track)
    monkeypatch.setattr(agent_insights, "save_view", AsyncMock(return_value=result))
    monkeypatch.setattr(staging_execution, "staged_command_effect", staged_effect)

    actual = await staging_executors._x_save_view(
        "user-1",
        {
            "track_id": "track-1",
            "name": "Board",
            "view_type": "kanban",
            "config": {"group_by": "custom_fields.stage"},
        },
    )

    assert actual is result
    assert effect_calls == [
        {
            "user_id": "user-1",
            "workspace_id": "workspace-1",
            "kind": "save_view",
            "targets": {"track_id": "track-1"},
        }
    ]
    assert complete_calls == [result]


@pytest.mark.asyncio
async def test_operational_model_view_mutation_records_staged_result(monkeypatch):
    from app.agentive import staging_executors
    from app.agentive.services import staging_execution
    from app.models.nodes import Track
    from app.services import operational_model_authoring

    result = {"action": "update_view", "view_id": "view-1"}
    complete_calls = []
    effect_calls = []

    async def get_track(track_id):
        assert track_id == "track-1"
        return SimpleNamespace(workspace_id="workspace-1")

    @asynccontextmanager
    async def staged_effect(**kwargs):
        effect_calls.append(kwargs)

        async def complete(value):
            complete_calls.append(value)

        yield complete

    monkeypatch.setattr(Track, "get", get_track)
    monkeypatch.setattr(
        operational_model_authoring,
        "modify_operational_model",
        AsyncMock(return_value=result),
    )
    monkeypatch.setattr(staging_execution, "staged_command_effect", staged_effect)

    actual = await staging_executors._x_modify_operational_model(
        "user-1",
        {"action": "update_view", "track_id": "track-1", "view_id": "view-1"},
    )

    assert actual is result
    assert effect_calls == [
        {
            "user_id": "user-1",
            "workspace_id": "workspace-1",
            "kind": "modify_operational_model.update_view",
            "targets": {
                "action": "update_view",
                "track_id": "track-1",
                "view_id": "view-1",
            },
        }
    ]
    assert complete_calls == [result]


@pytest.mark.asyncio
async def test_failed_existing_view_update_never_falls_through_to_create(monkeypatch):
    from app.agentive import staging_executors
    from app.services import agent_insights, operational_model_authoring

    failure = {"error": "bad_request", "detail": "Invalid view configuration"}
    monkeypatch.setattr(
        agent_insights,
        "get_scoped_view",
        AsyncMock(return_value=SimpleNamespace(track_id="track-1")),
    )
    monkeypatch.setattr(
        staging_executors, "_call_endpoint", AsyncMock(return_value=failure)
    )
    create_view = AsyncMock()
    monkeypatch.setattr(
        operational_model_authoring, "modify_operational_model", create_view
    )

    actual = await agent_insights.save_view(
        user_id="user-1",
        track_id="track-1",
        view_id="view-1",
        name="Existing board",
        view_type="kanban",
        config={"kanban_columns": ["invalid"]},
    )

    assert actual is failure
    create_view.assert_not_awaited()
