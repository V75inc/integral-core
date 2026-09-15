"""REST handler tests for /agentive/routines — Background Tasks surface.

Uses in-process handler invocation (same bootstrap as test_routine_tasks.py)
so the suite does not depend on full ASGI app import.
"""

from __future__ import annotations

import pytest

from app.agentive.api.routines import (
    cancel_routine,
    delete_routine,
    get_routine,
    get_routine_activity,
    list_routines,
    patch_routine,
)
from app.agentive.services.routine_tasks import create_routine_task
from app.agentive.tooling.invoke import invoke_route_in_process
from app.api.errors import ResourceNotFoundError


async def _bootstrap(email: str):
    """Create AuthUser + User + personal workspace + ChatThread.

    Returns (auth_user_id, workspace_id, thread).
    """
    from jvspatial.api.auth.models import UserCreate

    from app.api.auth import _get_auth_service
    from app.models.nodes import User
    from app.services.app_graph import catalog_user
    from app.services.chat_threads import create_thread, update_provider_session
    from app.services.personal_workspace import ensure_personal_workspace

    auth_service = _get_auth_service()
    user_response = await auth_service.register_user(
        UserCreate(email=email, password="testpassword123")
    )
    auth_user_id = user_response.id
    user_node = await User.create(user_id=auth_user_id, display_name="Routines API")
    await catalog_user(user_node)
    ws = await ensure_personal_workspace(user_node)
    workspace_id = ws.id if ws else None

    thread = await create_thread(
        user_id=auth_user_id,
        provider_id="jvagent",
        title="Routines API thread",
        workspace_id=workspace_id,
    )
    await update_provider_session(thread, f"sess-{auth_user_id}")
    return auth_user_id, workspace_id, thread


async def _seed(user_id: str, workspace_id: str, thread_id: str, **kwargs):
    return await create_routine_task(
        user_id=user_id,
        workspace_id=workspace_id or "ws-test",
        thread_id=thread_id,
        instruction=kwargs.get("instruction", "Daily digest."),
        cron=kwargs.get("cron", "0 8 * * *"),
        timezone=kwargs.get("timezone", "UTC"),
    )


@pytest.mark.asyncio
async def test_list_routines_returns_owned_only():
    """Caller only sees their own routines."""
    owner_id, owner_ws, owner_thread = await _bootstrap(
        "routines-api-owner@example.com"
    )
    other_id, other_ws, other_thread = await _bootstrap(
        "routines-api-other@example.com"
    )

    mine = await _seed(owner_id, owner_ws, owner_thread.id, instruction="Mine.")
    await _seed(other_id, other_ws, other_thread.id, instruction="Theirs.")

    data = await invoke_route_in_process(
        list_routines, principal_id=owner_id, scope=owner_ws
    )
    assert not (isinstance(data, dict) and data.get("error")), data
    ids = {r["id"] for r in data["routines"]}
    assert mine.id in ids
    assert all(r["instruction"] != "Theirs." for r in data["routines"])


@pytest.mark.asyncio
async def test_list_routines_status_filter():
    """Default excludes cancelled; status=cancelled returns them."""
    user_id, workspace_id, thread = await _bootstrap("routines-api-filter@example.com")
    active = await _seed(user_id, workspace_id, thread.id, instruction="Active one.")
    cancelled = await _seed(
        user_id, workspace_id, thread.id, instruction="Cancelled one."
    )
    cancelled.status = "cancelled"
    await cancelled.save()

    default = await invoke_route_in_process(
        list_routines, principal_id=user_id, scope=workspace_id
    )
    default_ids = {r["id"] for r in default["routines"]}
    assert active.id in default_ids
    assert cancelled.id not in default_ids

    filtered = await invoke_route_in_process(
        list_routines,
        principal_id=user_id,
        scope=workspace_id,
        status="cancelled",
    )
    filtered_ids = {r["id"] for r in filtered["routines"]}
    assert cancelled.id in filtered_ids
    assert active.id not in filtered_ids


@pytest.mark.asyncio
async def test_get_patch_cancel_routine():
    """Get → pause → edit instruction/cron → cancel round-trip."""
    user_id, workspace_id, thread = await _bootstrap("routines-api-crud@example.com")
    routine = await _seed(user_id, workspace_id, thread.id, instruction="Editable.")

    got = await invoke_route_in_process(
        get_routine,
        principal_id=user_id,
        scope=workspace_id,
        routine_id=routine.id,
    )
    assert got["instruction"] == "Editable."
    assert got["status"] == "active"

    paused = await invoke_route_in_process(
        patch_routine,
        principal_id=user_id,
        scope=workspace_id,
        routine_id=routine.id,
        json_body={"status": "paused"},
    )
    assert paused["status"] == "paused"

    edited = await invoke_route_in_process(
        patch_routine,
        principal_id=user_id,
        scope=workspace_id,
        routine_id=routine.id,
        json_body={
            "instruction": "Updated instruction.",
            "cron": "0 9 * * *",
            "timezone": "America/New_York",
        },
    )
    assert edited["instruction"] == "Updated instruction."
    assert edited["cron"] == "0 9 * * *"
    assert edited["timezone"] == "America/New_York"
    assert edited["next_run_at"] is not None

    cancelled = await invoke_route_in_process(
        cancel_routine,
        principal_id=user_id,
        scope=workspace_id,
        routine_id=routine.id,
    )
    assert cancelled == {"id": routine.id, "status": "cancelled"}


@pytest.mark.asyncio
async def test_foreign_routine_returns_not_found():
    """Other user's routine id raises ResourceNotFoundError (no leak)."""
    owner_id, owner_ws, owner_thread = await _bootstrap(
        "routines-api-foreign-owner@example.com"
    )
    other_id, other_ws, _other_thread = await _bootstrap(
        "routines-api-foreign-other@example.com"
    )
    foreign = await _seed(owner_id, owner_ws, owner_thread.id, instruction="Secret.")

    with pytest.raises(ResourceNotFoundError):
        await invoke_route_in_process(
            get_routine,
            principal_id=other_id,
            scope=other_ws,
            routine_id=foreign.id,
        )

    with pytest.raises(ResourceNotFoundError):
        await invoke_route_in_process(
            patch_routine,
            principal_id=other_id,
            scope=other_ws,
            routine_id=foreign.id,
            json_body={"status": "paused"},
        )

    with pytest.raises(ResourceNotFoundError):
        await invoke_route_in_process(
            cancel_routine,
            principal_id=other_id,
            scope=other_ws,
            routine_id=foreign.id,
        )

    with pytest.raises(ResourceNotFoundError):
        await invoke_route_in_process(
            delete_routine,
            principal_id=other_id,
            scope=other_ws,
            routine_id=foreign.id,
        )


@pytest.mark.asyncio
async def test_delete_routine_hard_removes_node():
    """DELETE drops the RoutineTask; subsequent get raises not-found."""
    from app.agentive.nodes import RoutineTask

    user_id, workspace_id, thread = await _bootstrap("routines-api-delete@example.com")
    routine = await _seed(user_id, workspace_id, thread.id, instruction="Gone soon.")

    deleted = await invoke_route_in_process(
        delete_routine,
        principal_id=user_id,
        scope=workspace_id,
        routine_id=routine.id,
    )
    assert deleted == {"id": routine.id, "deleted": True}
    assert await RoutineTask.get(routine.id) is None

    with pytest.raises(ResourceNotFoundError):
        await invoke_route_in_process(
            get_routine,
            principal_id=user_id,
            scope=workspace_id,
            routine_id=routine.id,
        )


@pytest.mark.asyncio
async def test_routine_activity_endpoint():
    """Activity payload includes thread_id and an empty/event list shape."""
    user_id, workspace_id, thread = await _bootstrap(
        "routines-api-activity@example.com"
    )
    routine = await _seed(
        user_id,
        workspace_id,
        thread.id,
        instruction="Create a Post in n.Track.abc123 titled demo.",
    )

    data = await invoke_route_in_process(
        get_routine_activity,
        principal_id=user_id,
        scope=workspace_id,
        routine_id=routine.id,
    )
    assert data["routine_id"] == routine.id
    assert data["thread_id"] == thread.id
    assert isinstance(data["events"], list)
    assert isinstance(data["write_scope_links"], list)
