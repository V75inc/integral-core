"""Hosted availability never grants another tenant's workspace or records."""

import pytest
from starlette.requests import Request

from app.api.errors import InsufficientPermissionsError
from app.config import settings
from app.middleware.permissions_cache import isolated_permissions_cache
from app.models.edges import COLLABORATES_ON, IS_MEMBER_OF
from app.runtime.composition import require_capability, runtime
from app.services.hooks.registry import ToolContext
from app.services.permissions import resolve_role
from app.services.request_scope import resolve_workspace_id_from_request
from tests.test_resource_location import graph

pytestmark = [pytest.mark.contract, pytest.mark.postgres, pytest.mark.asyncio]


@pytest.mark.parametrize("preset", [None, "hosted"])
async def test_workspace_gate_and_tool_scope_survive_hosted_composition(
    postgres_raw_db, monkeypatch, preset
):
    monkeypatch.setattr(settings, "INTEGRAL_DEPLOYMENT_PROFILE", "server")
    monkeypatch.setattr(settings, "INTEGRAL_RUNTIME_PRESET", preset)
    assert runtime().preset == "hosted"
    require_capability("collaboration")
    owner, workspace, _, _, own_entry = await graph()
    _, foreign_workspace, _, _, foreign_entry = await graph()
    with isolated_permissions_cache():
        assert await resolve_role(owner.id, "entry", own_entry.id) == "editor"
        assert await resolve_role(owner.id, "entry", foreign_entry.id) is None
    await owner.connect(foreign_entry, edge=COLLABORATES_ON, role="viewer")
    with isolated_permissions_cache():
        assert await resolve_role(owner.id, "entry", foreign_entry.id) is None
    request = Request(
        {
            "type": "http",
            "headers": [(b"x-integral-scope", f"ws:{foreign_workspace.id}".encode())],
        }
    )
    with pytest.raises(InsufficientPermissionsError):
        await resolve_workspace_id_from_request(request, owner.id)
    await owner.connect(foreign_workspace, edge=IS_MEMBER_OF, role="guest")
    with isolated_permissions_cache():
        assert await resolve_role(owner.id, "entry", foreign_entry.id) == "viewer"
    tool = ToolContext(
        user_id=owner.id, workspace_id=workspace.id, scope=f"ws:{workspace.id}"
    )
    assert await tool.get_entry(foreign_entry.id) is None
    assert (await tool.get_entry(own_entry.id)).id == own_entry.id
