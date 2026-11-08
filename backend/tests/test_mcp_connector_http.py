"""HTTP contracts for MCP mount / refresh / health on the connectors API.

Service-level adapter tests already cover discover + policy. This file
locks the REST surface the Phase 8 coverage gate actually measures:

- mount requires workspace scope and redacts secrets
- typed errors use ``details.reason`` / ``details.validation`` (not ``error``)
- refresh is an audited ``connector.update`` (D-05)
- health probes the mounted server
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any, Dict

import pytest
from httpx import AsyncClient

from app.services.hooks.registry import clear_workspace_registrations

_FIXTURE = Path(__file__).resolve().parent / "fixtures" / "mcp_echo_server.py"


def _stdio_auth() -> Dict[str, Any]:
    return {
        "transport": "stdio",
        "command": sys.executable,
        "args": [str(_FIXTURE)],
        "env": {"MCP_SECRET": "super-secret-token"},
    }


async def _workspace_id(client: AsyncClient) -> str:
    ws_resp = await client.get("/api/workspaces")
    assert ws_resp.status_code == 200, ws_resp.text
    body = ws_resp.json()
    workspaces = body.get("workspaces") or body.get("items") or body
    if isinstance(workspaces, dict):
        workspaces = workspaces.get("workspaces") or []
    return workspaces[0]["id"]


async def _audit_events_for_resource(client: AsyncClient, resource_id: str) -> list:
    r = await client.get("/api/audit-log")
    assert r.status_code == 200, r.text
    events = r.json().get("events", [])
    return [e for e in events if e.get("resource_id") == resource_id]


@pytest.mark.asyncio
async def test_mount_invalid_body_uses_validation_not_error(
    authenticated_client: AsyncClient,
):
    workspace_id = await _workspace_id(authenticated_client)
    r = await authenticated_client.post(
        "/api/agentive/connectors/mcp/mount",
        json={"transport": "not-a-transport", "extra_forged": True},
        headers={"X-Integral-Scope": f"ws:{workspace_id}"},
    )
    assert r.status_code == 400, r.text
    details = r.json().get("details") or {}
    assert "error" not in details
    assert "validation" in details


@pytest.mark.asyncio
async def test_mount_failed_discover_uses_reason(
    authenticated_client: AsyncClient,
):
    """Discover failure is a typed BadRequest with details.reason."""
    workspace_id = await _workspace_id(authenticated_client)
    r = await authenticated_client.post(
        "/api/agentive/connectors/mcp/mount",
        json={
            "transport": "stdio",
            "command": sys.executable,
            "args": ["-c", "import sys; sys.exit(1)"],
        },
        headers={"X-Integral-Scope": f"ws:{workspace_id}"},
    )
    assert r.status_code == 400, r.text
    details = r.json().get("details") or {}
    assert "error" not in details
    assert "reason" in details


@pytest.mark.asyncio
async def test_refresh_emits_connector_update_change_event(
    authenticated_client: AsyncClient,
):
    """Refresh re-registers tools and is visible on the audit log as connector.update."""
    workspace_id = await _workspace_id(authenticated_client)
    clear_workspace_registrations(workspace_id)
    auth = _stdio_auth()
    mounted = await authenticated_client.post(
        "/api/agentive/connectors/mcp/mount",
        json={
            "transport": "stdio",
            "command": auth["command"],
            "args": auth["args"],
            "env": auth["env"],
            "display_name": "refresh-audit",
        },
        headers={"X-Integral-Scope": f"ws:{workspace_id}"},
    )
    assert mounted.status_code in (200, 201), mounted.text
    mount_body = mounted.json()
    assert mount_body["status"] == "mounted"
    connector_id = mount_body["connector"]["id"]
    try:
        refreshed = await authenticated_client.post(
            f"/api/agentive/connectors/{connector_id}/mcp/refresh",
        )
        assert refreshed.status_code == 200, refreshed.text
        assert refreshed.json()["subclass_slug"] == "mcp"
        env = (refreshed.json().get("auth_state") or {}).get("env") or {}
        assert env.get("MCP_SECRET") == "[redacted]"

        matches = await _audit_events_for_resource(authenticated_client, connector_id)
        updates = [e for e in matches if e.get("action") == "connector.update"]
        assert updates, f"refresh produced no connector.update event: {matches!r}"
    finally:
        await authenticated_client.delete(f"/api/agentive/connectors/{connector_id}")
        clear_workspace_registrations(workspace_id)


@pytest.mark.asyncio
async def test_refresh_rejects_non_mcp_connector(authenticated_client: AsyncClient):
    created = await authenticated_client.post(
        "/api/agentive/connectors",
        json={"kind": "custom"},
    )
    assert created.status_code in (200, 201), created.text
    connector_id = created.json()["id"]
    r = await authenticated_client.post(
        f"/api/agentive/connectors/{connector_id}/mcp/refresh",
    )
    assert r.status_code == 400, r.text
    assert "not an MCP mount" in (r.json().get("message") or "")


@pytest.mark.asyncio
async def test_health_probe_after_stdio_mount(authenticated_client: AsyncClient):
    workspace_id = await _workspace_id(authenticated_client)
    clear_workspace_registrations(workspace_id)
    auth = _stdio_auth()
    mounted = await authenticated_client.post(
        "/api/agentive/connectors/mcp/mount",
        json={
            "transport": "stdio",
            "command": auth["command"],
            "args": auth["args"],
            "env": auth["env"],
        },
        headers={"X-Integral-Scope": f"ws:{workspace_id}"},
    )
    assert mounted.status_code in (200, 201), mounted.text
    mount_body = mounted.json()
    assert mount_body["status"] == "mounted"
    connector_id = mount_body["connector"]["id"]
    try:
        health = await authenticated_client.get(
            f"/api/agentive/connectors/{connector_id}/health",
        )
        assert health.status_code == 200, health.text
        body = health.json()
        assert body["connector_id"] == connector_id
        assert body["status"] == "ok"  # one vocabulary now, not two
        assert body["tool_count"] >= 1
    finally:
        await authenticated_client.delete(f"/api/agentive/connectors/{connector_id}")
        clear_workspace_registrations(workspace_id)


@pytest.mark.asyncio
async def test_mount_rejects_private_http_url(authenticated_client: AsyncClient):
    """SSRF: streamable_http mounts must not target loopback/private hosts."""
    workspace_id = await _workspace_id(authenticated_client)
    r = await authenticated_client.post(
        "/api/agentive/connectors/mcp/mount",
        json={
            "transport": "streamable_http",
            "url": "http://127.0.0.1:9/mcp",
        },
        headers={"X-Integral-Scope": f"ws:{workspace_id}"},
    )
    assert r.status_code == 400, r.text


@pytest.mark.asyncio
async def test_mount_stdio_forbidden_outside_testing(
    authenticated_client: AsyncClient, monkeypatch
):
    monkeypatch.setattr(
        "app.agentive.api.connectors._freeform_stdio_mounts_allowed",
        lambda: False,
    )
    workspace_id = await _workspace_id(authenticated_client)
    r = await authenticated_client.post(
        "/api/agentive/connectors/mcp/mount",
        json={
            "transport": "stdio",
            "command": sys.executable,
            "args": [str(_FIXTURE)],
        },
        headers={"X-Integral-Scope": f"ws:{workspace_id}"},
    )
    assert r.status_code == 400, r.text
    details = r.json().get("details") or {}
    assert details.get("reason") == "stdio_forbidden"


@pytest.mark.asyncio
async def test_registry_requires_name_query(authenticated_client: AsyncClient):
    workspace_id = await _workspace_id(authenticated_client)
    r = await authenticated_client.get(
        "/api/agentive/connectors/mcp/registry/servers",
        headers={"X-Integral-Scope": f"ws:{workspace_id}"},
    )
    assert r.status_code == 400, r.text
    assert "name" in (r.json().get("message") or "")


@pytest.mark.asyncio
async def test_registry_preview_does_not_persist(
    authenticated_client: AsyncClient, monkeypatch
):
    from app.agentive.connectors import mcp_registry_client as reg

    entry = {
        "name": "com.example/demo",
        "title": "Demo",
        "description": "demo",
        "version": "1.0.0",
        "install_tier": "direct_http",
        "install_allowed": True,
        "is_latest": True,
        "remote_url": "https://mcp.example.com/mcp",
        "repository_url": None,
        "auth_header_prompts": [],
        "env_var_prompts": [],
        "manual_recipe": None,
    }

    async def fake_get(_name: str):
        return entry

    monkeypatch.setattr(reg, "get_registry_server", fake_get)
    monkeypatch.setattr(
        reg,
        "build_mount_preview",
        lambda _e: {
            "registry_name": "com.example/demo",
            "registry_version": "1.0.0",
            "install_tier": "direct_http",
            "display_name": "Demo",
            "transport": "streamable_http",
            "url": "https://mcp.example.com/mcp",
            "command": None,
            "args": [],
            "auth_header_prompts": [],
            "env_var_prompts": [],
            "manual_recipe": None,
            "can_auto_mount": True,
        },
    )

    workspace_id = await _workspace_id(authenticated_client)
    before = await authenticated_client.get("/api/agentive/connectors")
    assert before.status_code == 200, before.text
    before_ids = {c["id"] for c in before.json()["connectors"]}

    preview = await authenticated_client.get(
        "/api/agentive/connectors/mcp/registry/preview",
        params={"name": "com.example/demo"},
        headers={"X-Integral-Scope": f"ws:{workspace_id}"},
    )
    assert preview.status_code == 200, preview.text
    data = preview.json()
    assert data["registry_name"] == "com.example/demo"
    assert data["can_auto_mount"] is True

    after = await authenticated_client.get("/api/agentive/connectors")
    after_ids = {c["id"] for c in after.json()["connectors"]}
    assert after_ids == before_ids


@pytest.mark.asyncio
@pytest.mark.parametrize("oauth_kind", ["discovered", "catalog", "none"])
async def test_reauthorize_keeps_the_existing_mount(
    authenticated_client: AsyncClient, test_user, monkeypatch, oauth_kind
):
    from types import SimpleNamespace
    from unittest.mock import AsyncMock
    from urllib.parse import parse_qs, urlsplit

    from app.agentive.connectors import mcp_oauth
    from app.agentive.nodes import Connector
    from app.agentive.services.connector_registry_node import (
        create_connector,
        decrypt_auth_state,
    )
    from app.connectors import catalog_loader

    workspace_id = await _workspace_id(authenticated_client)
    url = "https://mcp.example/mcp"
    authorize_url = "https://auth.example/authorize"
    auth_state = {"transport": "streamable_http", "url": url}
    probe = AsyncMock(
        return_value=SimpleNamespace(
            needs_oauth=oauth_kind == "discovered", response=object()
        )
    )
    monkeypatch.setattr(mcp_oauth, "probe_mcp_authorization", probe)
    start = AsyncMock(
        return_value={
            "client_id": "test-client",
            "redirect_uri": "https://integral.example/callback",
            "code_challenge": "fixture-challenge",
            "authorization_endpoint": authorize_url,
        }
    )
    monkeypatch.setattr(mcp_oauth, "start_mcp_oauth_session", start)
    if oauth_kind == "catalog":
        auth_state["catalog_slug"] = "test-catalog"
        monkeypatch.setattr(
            catalog_loader,
            "get_catalog_entry",
            lambda slug: {
                "oauth": {
                    "authorization_endpoint": authorize_url,
                    "token_endpoint": "https://auth.example/token",
                }
            },
        )
        monkeypatch.setattr(
            mcp_oauth,
            "google_oauth_client_credentials",
            lambda: ("test-client", "test-secret"),
        )

    connector = await create_connector(
        owner=test_user.id, kind="mcp", workspace_id=workspace_id, auth_state=auth_state
    )
    connector.subclass_slug = "mcp"
    await connector.save()

    response = await authenticated_client.post(
        f"/api/agentive/connectors/{connector.id}/mcp/reauthorize",
        headers={"X-Integral-Scope": f"ws:{workspace_id}"},
    )
    refreshed = await Connector.get(connector.id)
    assert refreshed is not None
    assert refreshed.workspace_id == workspace_id
    saved_auth = decrypt_auth_state(refreshed.auth_state)
    if oauth_kind == "none":
        assert response.status_code == 400, response.text
        assert "did not ask for authorization" in response.json()["message"]
        assert "oauth" not in saved_auth
        start.assert_not_awaited()
        return

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["connector_id"] == connector.id
    consent = urlsplit(body["consent_url"])
    assert (consent.netloc, consent.path) == ("auth.example", "/authorize")
    assert parse_qs(consent.query)["state"] == [body["state"]]
    assert saved_auth["oauth"]["state"] == body["state"]
    assert saved_auth["oauth"]["status"] == mcp_oauth.OAUTH_STATUS_PENDING
    if oauth_kind == "catalog":
        probe.assert_not_awaited()
        start.assert_not_awaited()
    else:
        probe.assert_awaited_once_with(url)
        start.assert_awaited_once()
