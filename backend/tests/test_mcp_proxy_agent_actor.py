"""Regression: the resident (actor_kind="agent") can invoke a mounted MCP tool.

`ba28c1d2` set ``actor_kind="agent"`` on the bundle-tool ToolContext while
``principal_id`` stayed the human's node id, and ``_enforce_invoke_policy``
passed that kind straight through as the policy Subject. ``policy_evaluate``
routes any non-``human`` subject to the graph-local ``HAS_POLICY`` branch,
found no policy on a nonexistent agent with the human's id, and fail-closed —
so every resident-initiated ``mcp__*`` call returned ``policy_denied`` and the
whole MCP-as-connector feature was dead (ADR-009).

The caller gate must evaluate the human principal; ``actor_kind`` is audit
provenance only. The connector-subject grant remains the agent-side control.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any, Dict

import pytest
from httpx import AsyncClient

from app.services.hooks.registry import (
    clear_workspace_registrations,
    get_workspace_tools,
)

_FIXTURE = Path(__file__).resolve().parent / "fixtures" / "mcp_echo_server.py"


@pytest.fixture(scope="module", autouse=True)
def _warm_app_env_before_leak_guard():
    """Load app.main once so JVSPATIAL_* env is set before the first test."""
    from tests.conftest import get_app

    get_app()


def _stdio_auth() -> Dict[str, Any]:
    """Local echo MCP server over stdio (free-form is allowed under TESTING=1)."""
    return {
        "transport": "stdio",
        "command": sys.executable,
        "args": [str(_FIXTURE)],
        "env": {"MCP_SECRET": "super-secret-token"},
    }


@pytest.mark.asyncio
async def test_resident_actor_kind_agent_can_invoke_mcp_tool(
    authenticated_client: AsyncClient,
):
    """actor_kind="agent" must not fail-closed on the caller gate."""
    from app.agentive.connectors.mcp_mount import mount_mcp_connector
    from app.agentive.connectors.mcp_proxy import invoke_from_spec
    from app.services.hooks.registry import ToolContext

    ws_resp = await authenticated_client.get("/api/workspaces")
    assert ws_resp.status_code == 200, ws_resp.text
    body = ws_resp.json()
    workspaces = body.get("workspaces") or body.get("items") or body
    if isinstance(workspaces, dict):
        workspaces = workspaces.get("workspaces") or []
    workspace_id = workspaces[0]["id"]
    clear_workspace_registrations(workspace_id)

    me = await authenticated_client.get("/api/auth/me")
    user_id = me.json().get("id") or me.json().get("user", {}).get("id")
    auth = _stdio_auth()
    connector = await mount_mcp_connector(
        owner_id=user_id,
        workspace_id=workspace_id,
        transport="stdio",
        command=auth["command"],
        args=auth["args"],
        env=auth["env"],
    )
    try:
        tools = get_workspace_tools(workspace_id)
        echo_spec = next(
            s for s in tools.values() if s.get("_mcp_remote_name") == "echo"
        )
        # The resident path: same human principal, agent provenance.
        ctx = ToolContext(
            user_id=user_id,
            workspace_id=workspace_id,
            scope=f"tool:{echo_spec['key']}",
            actor_kind="agent",
        )
        result = await invoke_from_spec(echo_spec, {"message": "hello-agent"}, ctx)
        text = (
            result.get("text") or result.get("result") or str(result)
            if isinstance(result, dict)
            else str(result)
        )
        assert "hello-agent" in text
    finally:
        clear_workspace_registrations(workspace_id)
        try:
            await connector.delete(cascade=False)
        except Exception:  # noqa: BLE001 — best-effort teardown
            pass


@pytest.mark.asyncio
async def test_invoke_without_principal_is_denied():
    """An empty principal must be refused, not silently allowed."""
    from app.agentive.connectors.mcp_proxy import (
        McpProxyError,
        _enforce_invoke_policy,
    )

    with pytest.raises(McpProxyError) as ei:
        await _enforce_invoke_policy(
            connector_id="n.Connector.does-not-matter",
            principal_id="",
            actor_kind="agent",
        )
    assert ei.value.error_code == "policy_denied"
