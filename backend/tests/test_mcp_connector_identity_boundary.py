"""Connector login/profile aliases must preserve live authority and errors."""

from unittest.mock import AsyncMock

import pytest

from app.agentive.connectors.connector_resolution import resolve_connector_row
from app.agentive.connectors.mcp_mount import materialize_mcp_policies
from app.agentive.connectors.mcp_proxy import McpProxyError, invoke_from_spec
from app.agentive.services.connector_registry_node import create_connector
from app.agentive.tooling.dispatch import _tool_error_from_exception
from app.schemas.policy import Resource, Subject
from app.services.hooks.registry import ToolContext
from app.services.policy_engine import evaluate
from tests.test_access_lifecycle import verified_subject
from tests.test_resource_location import graph

pytestmark = [pytest.mark.asyncio, pytest.mark.smoke]


@pytest.mark.parametrize("owner_form", ["auth", "profile"])
@pytest.mark.parametrize("caller_form", ["auth", "profile"])
async def test_connector_identity_aliases_round_trip(
    owner_form, caller_form, monkeypatch
):
    _, workspace, _, _, _ = await graph()
    owner = await verified_subject(workspace)
    owner_id = owner.user_id if owner_form == "auth" else owner.id
    caller_id = owner.user_id if caller_form == "auth" else owner.id
    connector = await create_connector(
        owner=owner_id,
        workspace_id=workspace.id,
        kind="mcp",
        auth_state={"catalog_slug": "fixture-search"},
        skip_default_policy=True,
    )
    await materialize_mcp_policies(connector=connector, actor_id=owner_id)
    decision = await evaluate(
        subject=Subject(kind="connector", id=connector.id),
        action="tool.invoke",
        resource=Resource(
            kind="connector", id=connector.id, scope=f"connector:{connector.id}"
        ),
        _dry_run=True,
    )
    assert decision.allowed, decision.reason
    resolved = await resolve_connector_row(
        workspace_id=workspace.id,
        slug="fixture-search",
        principal_id=caller_id,
    )
    assert resolved.id == connector.id
    remote = AsyncMock(return_value={"answer": "verified"})
    monkeypatch.setattr("app.agentive.connectors.mcp_proxy.call_remote_tool", remote)
    result = await invoke_from_spec(
        {"_mcp_connector_slug": "fixture-search", "_mcp_remote_name": "search"},
        {"query": "example"},
        ToolContext(
            user_id=caller_id,
            workspace_id=workspace.id,
            scope=workspace.id,
            actor_kind="agent",
        ),
    )
    assert result == {"answer": "verified"}
    remote.assert_awaited_once()


@pytest.mark.parametrize(
    "code", ["policy_denied", "not_connected", "remote_error", "misconfigured"]
)
async def test_connector_errors_retain_their_typed_code(code):
    error = _tool_error_from_exception(
        McpProxyError("Connector operation refused", code)
    )
    assert error.is_error and error.error_code == code
    assert error.message == "Connector operation refused"


@pytest.fixture
async def mounted_identity_connector():
    _, workspace, _, _, _ = await graph()
    owner = await verified_subject(workspace)
    connector = await create_connector(
        owner=owner.user_id,
        workspace_id=workspace.id,
        kind="mcp",
        auth_state={"catalog_slug": "fixture-search"},
        skip_default_policy=True,
    )
    await materialize_mcp_policies(connector=connector, actor_id=owner.user_id)
    return workspace, owner, connector


@pytest.mark.parametrize(
    "violation",
    [
        "other_user",
        "foreign_workspace",
        "revoked_policy",
        "removed_member",
        "disabled_owner",
        "forged_metadata",
    ],
)
async def test_denials_never_reach_remote(
    mounted_identity_connector, monkeypatch, violation
):
    from jvspatial.api.auth.models import User as AuthUser

    from app.models.edges import IS_MEMBER_OF
    from app.services.policy_registry import delete_policy, list_policies_for_subject

    workspace, owner, connector = mounted_identity_connector
    remote = AsyncMock(return_value={"unexpected": True})
    monkeypatch.setattr("app.agentive.connectors.mcp_proxy.call_remote_tool", remote)
    principal, home = owner.user_id, workspace.id
    arguments = {"query": "example"}
    expected = "policy_denied"
    if violation == "other_user":
        principal = (await verified_subject(workspace)).user_id
    elif violation == "foreign_workspace":
        _, foreign, _, _, _ = await graph()
        home = foreign.id
        expected = "scope_mismatch"
    elif violation == "revoked_policy":
        for policy in await list_policies_for_subject("connector", connector.id):
            await delete_policy(policy.id)
    elif violation == "removed_member":
        context = await owner.get_context()
        for edge in await context.find_edges_between(
            owner.id, workspace.id, edge_class=IS_MEMBER_OF
        ):
            await edge.delete()
    elif violation == "disabled_owner":
        auth = await AuthUser.get(owner.user_id)
        auth.is_active = False
        await auth.save()
    elif violation == "forged_metadata":
        arguments["_mcp_connector_id"] = "n.Connector.foreign"
        expected = "invalid_arguments"
    with pytest.raises(McpProxyError) as error:
        await invoke_from_spec(
            {"_mcp_connector_id": connector.id, "_mcp_remote_name": "search"},
            arguments,
            ToolContext(
                user_id=principal, workspace_id=home, scope=home, actor_kind="agent"
            ),
        )
    assert error.value.error_code == expected
    remote.assert_not_awaited()


async def test_shared_member_invokes_but_cannot_manage(
    mounted_identity_connector, monkeypatch
):
    from app.agentive.api.connectors import _require_owner_or_shared_admin
    from app.api.errors import ResourceNotFoundError

    workspace, owner, connector = mounted_identity_connector
    member = await verified_subject(workspace)
    connector.connection_mode = "shared"
    await connector.save()
    remote = AsyncMock(return_value={"answer": "shared"})
    monkeypatch.setattr("app.agentive.connectors.mcp_proxy.call_remote_tool", remote)
    assert await invoke_from_spec(
        {"_mcp_connector_slug": "fixture-search", "_mcp_remote_name": "search"},
        {"query": "example"},
        ToolContext(
            user_id=member.user_id, workspace_id=workspace.id, scope=workspace.id
        ),
    ) == {"answer": "shared"}
    with pytest.raises(ResourceNotFoundError):
        await _require_owner_or_shared_admin(connector, member.user_id)
    await _require_owner_or_shared_admin(connector, owner.id)


async def test_discovery_and_management_accept_both_owner_aliases(
    mounted_identity_connector,
):
    from app.agentive.api.connectors import _require_owner_or_shared_admin
    from app.agentive.services.connector_registry_node import list_connectors_for_owner

    workspace, owner, connector = mounted_identity_connector
    for principal in (owner.id, owner.user_id):
        assert connector.id in {
            c.id for c in await list_connectors_for_owner(principal)
        }
        await _require_owner_or_shared_admin(connector, principal)
        assert (
            await evaluate(
                subject=Subject(kind="human", id=principal),
                action="connector.read",
                resource=Resource(
                    kind="connector", id=connector.id, scope=f"user:{owner.user_id}"
                ),
                _dry_run=True,
            )
        ).allowed


async def test_remote_failure_health_and_recovery(
    mounted_identity_connector, monkeypatch
):
    from app.agentive.nodes import Connector

    workspace, owner, connector = mounted_identity_connector
    remote = AsyncMock(
        side_effect=[
            RuntimeError("Remote MCP tool reported an error"),
            {"answer": "recovered"},
        ]
    )
    monkeypatch.setattr("app.agentive.connectors.mcp_proxy.call_remote_tool", remote)
    spec = {"_mcp_connector_id": connector.id, "_mcp_remote_name": "search"}
    ctx = ToolContext(
        user_id=owner.user_id, workspace_id=workspace.id, scope=workspace.id
    )
    with pytest.raises(McpProxyError) as error:
        await invoke_from_spec(spec, {"query": "example"}, ctx)
    assert error.value.error_code == "remote_error"
    assert (await Connector.get(connector.id)).health_status == "error"
    assert remote.await_count == 1  # no hidden replay of third-party effects
    assert await invoke_from_spec(spec, {"query": "example"}, ctx) == {
        "answer": "recovered"
    }
    recovered = await Connector.get(connector.id)
    assert recovered.health_status == "ok" and recovered.last_error is None


async def test_stdio_handshake_has_a_bounded_read_timeout():
    import asyncio
    import sys

    from app.agentive.connectors.mcp_client import (
        describe_mcp_failure,
        open_mcp_session,
    )

    async def stalled():
        async with open_mcp_session(
            {
                "transport": "stdio",
                "command": sys.executable,
                "args": ["-u", "-c", "import time; time.sleep(30)"],
            },
            timeout=0.05,
        ):
            pytest.fail("unresponsive MCP server initialized")

    with pytest.raises(Exception) as error:
        await asyncio.wait_for(stalled(), timeout=5)
    assert "timed out" in describe_mcp_failure(error.value).lower()
