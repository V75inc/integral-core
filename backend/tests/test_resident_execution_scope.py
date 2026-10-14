"""The resident scope tool reads current execution facts, never UI preference."""

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.api.errors import InsufficientPermissionsError
from app.services import resident_execution_scope as service

pytestmark = [pytest.mark.smoke, pytest.mark.asyncio]


@pytest.mark.parametrize(
    "role,flags",
    [("owner", (True, True)), ("admin", (False, True)), ("guest", (False, False))],
)
async def test_bound_workspace_and_current_creation_flags(monkeypatch, role, flags):
    workspace = SimpleNamespace(id="bound", name="Bound workspace", kind="organization")
    user = SimpleNamespace(id="principal", active_workspace_id="saved-other")
    monkeypatch.setattr(service, "can_access_workspace", AsyncMock(return_value=role))
    monkeypatch.setattr(service.Workspace, "get", AsyncMock(return_value=workspace))
    monkeypatch.setattr(service, "get_user_node", AsyncMock(return_value=user))
    creation = AsyncMock(return_value=flags)
    monkeypatch.setattr(service, "member_creation_flags", creation)
    result = await service.get_execution_scope_for_dispatch(
        user_id="principal", workspace_id="bound"
    )
    assert result == {
        "user_id": "principal",
        "workspace": {"id": "bound", "name": "Bound workspace", "kind": "organization"},
        "role": role,
        "can_create_apps": flags[0],
        "can_create_tracks": flags[1],
    }
    if role == "owner":
        creation.assert_not_awaited()
    else:
        creation.assert_awaited_once_with(user, "bound")


@pytest.mark.parametrize("workspace_id,role", [(None, "owner"), ("bound", "none")])
async def test_missing_or_revoked_scope_never_falls_back(
    monkeypatch, workspace_id, role
):
    monkeypatch.setattr(service, "can_access_workspace", AsyncMock(return_value=role))
    monkeypatch.setattr(
        service.Workspace, "get", AsyncMock(return_value=SimpleNamespace(id="bound"))
    )
    with pytest.raises(InsufficientPermissionsError):
        await service.get_execution_scope_for_dispatch(
            user_id="principal", workspace_id=workspace_id
        )


async def test_tool_binding_excludes_model_identity_arguments():
    from app.agentive.tooling.bindings import TOOL_BINDINGS

    binding = TOOL_BINDINGS["integral_get_scope"]
    assert binding.handler_ref is None
    assert binding.service_ref() is service.get_execution_scope_for_dispatch
    assert (
        binding.service_param_map({"user_id": "other", "workspace_id": "other"}) == {}
    )


async def test_dispatch_uses_bound_workspace_despite_other_saved_preference():
    from app.agentive.tooling.dispatch import dispatch_tool
    from app.models.edges import IS_MEMBER_OF
    from app.models.nodes import Workspace
    from app.services.app_graph import catalog_workspace
    from tests.test_resource_location import graph

    user, bound, _, _, _ = await graph()
    other = await Workspace.create(name="Other scope", kind="organization")
    await catalog_workspace(other)
    membership = await user.connect(
        other,
        edge=IS_MEMBER_OF,
        role="member",
        can_create_apps=False,
        can_create_tracks=True,
    )
    user.active_workspace_id = other.id
    user.active_workspace_id_explicit = True
    await user.save()
    result = await dispatch_tool(
        "integral_get_scope", {}, principal_id=user.id, scope=bound.id
    )
    assert not result.is_error, result
    assert result.data["workspace"]["id"] == bound.id
    assert result.data["can_create_apps"] is True
    result = await dispatch_tool(
        "integral_get_scope", {}, principal_id=user.id, scope=other.id
    )
    assert not result.is_error, result
    assert result.data["can_create_apps"] is False
    assert result.data["can_create_tracks"] is True
    await membership.delete()
    revoked = await dispatch_tool(
        "integral_get_scope", {}, principal_id=user.id, scope=other.id
    )
    assert revoked.is_error
