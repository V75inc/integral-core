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
    from app.services import agent_insights, membership_write_scope, resource_location

    result = {"action": "update_view", "view_id": "view-1", "name": "Board"}
    complete_calls = []
    effect_calls = []
    authority_calls = []

    @asynccontextmanager
    async def authority(workspace_id, *, principal_ids):
        authority_calls.append((workspace_id, principal_ids))
        yield

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
    monkeypatch.setattr(membership_write_scope, "membership_write_scope", authority)
    monkeypatch.setattr(
        resource_location,
        "locate_resource",
        AsyncMock(return_value=SimpleNamespace(workspace_id="workspace-1")),
    )

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
    assert authority_calls == [("workspace-1", ("user-1",))]
    assert effect_calls == [
        {
            "user_id": "user-1",
            "workspace_id": "workspace-1",
            "kind": "save_view",
            "targets": {
                "track_id": "track-1",
                "name": "Board",
                "view_type": "kanban",
                "config": {"group_by": "custom_fields.stage"},
            },
        }
    ]
    assert complete_calls == [{"track_id": "track-1", "view_id": "view-1"}]


@pytest.mark.asyncio
async def test_operational_model_executor_forwards_full_approved_payload(monkeypatch):
    from app.agentive import staging_executors
    from app.agentive.services import staging_model_commands
    from app.services import operational_model_authoring

    result = {"action": "update_view", "view_id": "view-1"}
    payload = {
        "action": "update_view",
        "track_id": "track-1",
        "view_id": "view-1",
        "name": "Board",
        "config": {"group_by": "stage"},
        "is_default": False,
    }
    calls = []

    async def apply(**kwargs):
        execute = kwargs.pop("execute")
        calls.append(kwargs)
        return await execute()

    implementation = AsyncMock(return_value=result)
    monkeypatch.setattr(staging_model_commands, "apply_model_command", apply)
    monkeypatch.setattr(
        operational_model_authoring, "modify_operational_model", implementation
    )

    actual = await staging_executors._x_modify_operational_model("user-1", payload)

    assert actual is result
    assert calls == [
        {
            "user_id": "user-1",
            "payload": payload,
            "kind": "modify_operational_model.update_view",
        }
    ]
    implementation.assert_awaited_once_with(user_id="user-1", app_id=None, **payload)


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


@pytest.mark.asyncio
@pytest.mark.parametrize("status", [401, 403, 404, 400, 429, 500, 503, None])
async def test_receipt_readback_distinguishes_denial_from_outage(monkeypatch, status):
    """Only classified denial/absence may become an applied-but-withheld result."""
    from app.agentive import staging_executors
    from app.agentive.services.staging_command_apply import _current_record_readback
    from app.exceptions import AuthorizationUnavailableError

    handler = AsyncMock()
    call = AsyncMock(return_value={"error": True, "status_code": status})
    monkeypatch.setattr(staging_executors, "_call_endpoint", call)
    if status in {401, 403, 404}:
        assert (
            await _current_record_readback(handler, "user-1", view_id="view-1") is None
        )
    else:
        with pytest.raises(AuthorizationUnavailableError):
            await _current_record_readback(handler, "user-1", view_id="view-1")
    call.assert_awaited_once_with(handler, "user-1", view_id="view-1")
