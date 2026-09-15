"""ADR-009 — MCP connector adapter acceptance tests.

Covers:
1. Mount + discover → tools in workspace registry immediately (no restart)
2. Invoke under policy (owner + connector HAS_POLICY) and fail-closed without
3. Health probe healthy / error
4. Credentials redaction on wire
5. finalize_install registration-class regression (hot register_workspace_tools)
6. Unmount drops registry tools
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
    """Load app.main once so JVSPATIAL_* env is set before the first test.

    conftest's per-test env-leak guard otherwise ERROR-fails the first
    authenticated_client consumer in an isolated file run (pre-existing
    pattern when app.main is first imported mid-test).
    """
    from tests.conftest import get_app

    get_app()


def _stdio_auth(*, bad: bool = False) -> Dict[str, Any]:
    if bad:
        return {
            "transport": "stdio",
            "command": sys.executable,
            "args": ["-c", "import sys; sys.exit(1)"],
            "env": {"MCP_SECRET": "super-secret-token"},
        }
    return {
        "transport": "stdio",
        "command": sys.executable,
        "args": [str(_FIXTURE)],
        "env": {"MCP_SECRET": "super-secret-token"},
    }


@pytest.fixture(autouse=True)
def _clear_ws_tools():
    """Avoid cross-test leakage of the in-process workspace tool registry."""
    yield
    # Best-effort clear of any workspace ids we might have used.
    # clear_workspace_registrations is keyed; tests pass concrete ids.


@pytest.mark.asyncio
async def test_mount_discovers_and_registers_tools_without_restart(
    authenticated_client: AsyncClient, test_user
):
    """Mount echo MCP → echo/add appear in get_workspace_tools immediately."""
    from app.agentive.connectors.mcp_mount import mount_mcp_connector

    me = await authenticated_client.get("/api/auth/me")
    assert me.status_code == 200, me.text
    user_id = me.json().get("id") or me.json().get("user", {}).get("id")
    assert user_id

    # Prefer HTTP list workspaces if available
    ws_resp = await authenticated_client.get("/api/workspaces")
    assert ws_resp.status_code == 200, ws_resp.text
    workspaces = ws_resp.json().get("workspaces") or ws_resp.json().get("items") or []
    if isinstance(ws_resp.json(), list):
        workspaces = ws_resp.json()
    assert workspaces, "expected a personal workspace"
    workspace_id = (
        workspaces[0]["id"] if isinstance(workspaces[0], dict) else workspaces[0]
    )
    clear_workspace_registrations(workspace_id)

    auth = _stdio_auth()
    connector = await mount_mcp_connector(
        owner_id=user_id,
        workspace_id=workspace_id,
        transport="stdio",
        command=auth["command"],
        args=auth["args"],
        env=auth["env"],
        display_name="echo-fixture",
    )
    try:
        assert connector.subclass_slug == "mcp"
        assert connector.workspace_id == workspace_id
        assert connector.health_status == "ok"  # ADR-009 §8 vocabulary

        tools = get_workspace_tools(workspace_id)
        remote_names = {(spec.get("_mcp_remote_name") or "") for spec in tools.values()}
        assert "echo" in remote_names
        assert "add" in remote_names
        assert all(
            spec.get("_mcp_connector_id") == connector.id
            for spec in tools.values()
            if spec.get("_mcp_connector_id")
        )
    finally:
        from app.agentive.connectors.mcp_mount import unmount_mcp_connector

        await unmount_mcp_connector(connector)
        clear_workspace_registrations(workspace_id)


@pytest.mark.asyncio
async def test_invoke_under_policy_and_fail_closed(
    authenticated_client: AsyncClient, test_user
):
    """Owner + connector Policy → echo succeeds; wipe Policy → fail-closed."""
    from app.agentive.connectors.mcp_mount import (
        mount_mcp_connector,
        unmount_mcp_connector,
    )
    from app.agentive.connectors.mcp_proxy import McpProxyError, invoke_from_spec
    from app.services.hooks.registry import ToolContext
    from app.services.policy_registry import delete_policy, list_policies_for_subject

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
        ctx = ToolContext(
            user_id=user_id, workspace_id=workspace_id, scope=f"tool:{echo_spec['key']}"
        )
        result = await invoke_from_spec(echo_spec, {"message": "hello-mcp"}, ctx)
        # FastMCP echo returns structured or text; accept either.
        if isinstance(result, dict):
            text = result.get("text") or result.get("result") or str(result)
        else:
            text = str(result)
        assert "hello-mcp" in text

        # Fail-closed: delete connector Policies → connector subject denies.
        from app.middleware.permissions_cache import policy_decision_clear_for_subject

        policies = await list_policies_for_subject("connector", connector.id)
        assert policies, "expected materialize_mcp_policies to attach a Policy"
        for p in policies:
            await delete_policy(p.id)
        policy_decision_clear_for_subject("connector", connector.id)

        with pytest.raises(McpProxyError) as ei:
            await invoke_from_spec(echo_spec, {"message": "nope"}, ctx)
        assert ei.value.error_code == "policy_denied"
    finally:
        await unmount_mcp_connector(connector)
        clear_workspace_registrations(workspace_id)


@pytest.fixture
def enc_key(monkeypatch):
    """Configure a throwaway credential-encryption key for this test only.

    ``encrypt_auth_state`` deliberately stores plaintext when no key is
    configured, so without this the at-rest assertion below passed or failed
    depending on whether the developer's .env happened to carry
    ``INTEGRAL_CREDENTIAL_ENC_KEY`` — and CI has no .env at all. The env var
    outranks settings in ``credential_crypto._resolve_key``, so no settings
    cache needs rebuilding. The key-less (plaintext) path is pinned in
    ``tests/test_connector_auth_state_crypto.py``.
    """
    monkeypatch.setenv("INTEGRAL_CREDENTIAL_ENC_KEY", "A" * 43 + "=")  # 32 zero bytes


@pytest.mark.smoke
@pytest.mark.asyncio
async def test_health_and_redaction(
    authenticated_client: AsyncClient, test_user, enc_key
):
    """Healthy after mount; bad command → error; wire redacts env secrets."""
    from app.agentive.connectors.mcp_mount import (
        health_check_mcp_connector,
        mount_mcp_connector,
        unmount_mcp_connector,
    )
    from app.schemas.agentive.connectors import mcp_safe_auth_state

    ws_resp = await authenticated_client.get("/api/workspaces")
    body = ws_resp.json()
    workspaces = body.get("workspaces") or body.get("items") or body
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
        health = await health_check_mcp_connector(connector)
        assert health["status"] == "ok"  # ADR-009 §8 vocabulary
        assert health["tool_count"] >= 2

        # Wire redaction helper (server never returns raw env values).
        safe = mcp_safe_auth_state(dict(connector.auth_state or {}))
        assert safe.get("env", {}).get("MCP_SECRET") == "[redacted]"
        # Server-side auth_state still yields the secret for the subprocess —
        # but at rest it is AES-256-GCM ciphertext, not the plaintext this
        # assertion used to pin (F-7).
        from app.agentive.services.connector_registry_node import decrypt_auth_state

        stored = (connector.auth_state or {}).get("env", {}).get("MCP_SECRET")
        assert stored != "super-secret-token"
        assert stored.startswith("v1:")
        plain = decrypt_auth_state(connector.auth_state or {})
        assert plain["env"]["MCP_SECRET"] == "super-secret-token"

        # Bad transport → error status
        connector.auth_state = _stdio_auth(bad=True)
        await connector.save()
        bad_health = await health_check_mcp_connector(connector)
        assert bad_health["status"] == "error"
        assert bad_health["last_error"]
    finally:
        await unmount_mcp_connector(connector)
        clear_workspace_registrations(workspace_id)


@pytest.mark.asyncio
async def test_finalize_install_registration_class_hot_path():
    """Regression: finalize_install registration class registers tools without restart.

    MCP mount and App finalize_install share ``register_workspace_tools``.
    This test exercises that API the same way ``register_bundle_on_install``
    does, proving tools are live in-process immediately.
    """
    from app.services.hooks.install_hook import register_bundle_on_install
    from app.services.hooks.registry import unregister_bundle_registrations

    workspace_id = "ws-finalize-reg-class-test"
    clear_workspace_registrations(workspace_id)
    canonical = {
        "package": {"slug": "reg_class_probe", "trust_tier": "trusted"},
        "app": {
            "tools": [
                {
                    "key": "probe_tool",
                    "handler_ref": "tools.noop:run",
                    "privileged": False,
                }
            ],
            "hooks": [],
        },
    }
    await register_bundle_on_install(workspace_id, canonical)
    tools = get_workspace_tools(workspace_id)
    assert "probe_tool" in tools
    assert tools["probe_tool"].get("_bundle_slug") == "reg_class_probe"
    # Still live — no restart between register and assert.
    assert get_workspace_tools(workspace_id)["probe_tool"]["key"] == "probe_tool"
    unregister_bundle_registrations(workspace_id, "reg_class_probe")
    clear_workspace_registrations(workspace_id)


@pytest.mark.asyncio
async def test_unmount_removes_tools(authenticated_client: AsyncClient, test_user):
    """Unmount drops MCP tools from the workspace registry."""
    from app.agentive.connectors.mcp_mount import (
        mount_mcp_connector,
        unmount_mcp_connector,
    )

    ws_resp = await authenticated_client.get("/api/workspaces")
    body = ws_resp.json()
    workspaces = body.get("workspaces") or body.get("items") or body
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
    assert any(
        s.get("_mcp_connector_id") == connector.id
        for s in get_workspace_tools(workspace_id).values()
    )
    await unmount_mcp_connector(connector)
    assert not any(
        s.get("_mcp_connector_id") == connector.id
        for s in get_workspace_tools(workspace_id).values()
    )
    clear_workspace_registrations(workspace_id)


@pytest.mark.asyncio
async def test_http_mount_endpoint(authenticated_client: AsyncClient, test_user):
    """POST /agentive/connectors/mcp/mount with scope header mounts + redacts."""
    ws_resp = await authenticated_client.get("/api/workspaces")
    body = ws_resp.json()
    workspaces = body.get("workspaces") or body.get("items") or body
    workspace_id = workspaces[0]["id"]
    clear_workspace_registrations(workspace_id)

    auth = _stdio_auth()
    r = await authenticated_client.post(
        "/api/agentive/connectors/mcp/mount",
        json={
            "transport": "stdio",
            "command": auth["command"],
            "args": auth["args"],
            "env": auth["env"],
            "display_name": "http-mount",
        },
        headers={"X-Integral-Scope": f"ws:{workspace_id}"},
    )
    assert r.status_code in (200, 201), r.text
    envelope = r.json()
    assert envelope["status"] == "mounted"
    data = envelope["connector"]
    assert data["subclass_slug"] == "mcp"
    assert data["health_status"] == "ok"  # ADR-009 §8 vocabulary
    assert (data.get("auth_state") or {}).get("env", {}).get(
        "MCP_SECRET"
    ) == "[redacted]"
    assert any(
        s.get("_mcp_connector_id") == data["id"]
        for s in get_workspace_tools(workspace_id).values()
    )

    # Cleanup via DELETE
    d = await authenticated_client.delete(f"/api/agentive/connectors/{data['id']}")
    assert d.status_code in (200, 204), d.text
    clear_workspace_registrations(workspace_id)
