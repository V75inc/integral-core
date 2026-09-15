"""Unit tests for outbound MCP OAuth helpers (I-CON-06)."""

from __future__ import annotations

import httpx
import pytest

from app.agentive.connectors.mcp_oauth import (
    OAUTH_STATUS_AUTHORIZED,
    authorization_url_for_oauth,
    probe_mcp_authorization,
    redact_mcp_auth_state,
    sign_mcp_oauth_state,
    verify_mcp_oauth_state,
)


def test_redact_mcp_auth_state_keeps_status_drops_secrets():
    redacted = redact_mcp_auth_state(
        {
            "url": "https://mcp.example/mcp",
            "transport": "streamable_http",
            "headers": {"Authorization": "Bearer secret"},
            "oauth": {
                "status": OAUTH_STATUS_AUTHORIZED,
                "client_secret": "s",
                "code_verifier": "v",
                "tokens": {"access_token": "tok"},
            },
        }
    )
    assert redacted["url"] == "https://mcp.example/mcp"
    assert redacted["transport"] == "streamable_http"
    assert redacted["oauth"] == {"status": OAUTH_STATUS_AUTHORIZED}
    blob = str(redacted)
    assert "secret" not in blob
    assert "tok" not in blob
    assert "headers" not in redacted


def test_sign_and_verify_mcp_oauth_state_roundtrip():
    state = sign_mcp_oauth_state("user-1", "conn-1")
    assert verify_mcp_oauth_state(state, "user-1") == "conn-1"
    assert verify_mcp_oauth_state(state, "user-2") is None
    assert verify_mcp_oauth_state("not-a-token", "user-1") is None


def test_authorization_url_for_oauth_includes_pkce():
    url = authorization_url_for_oauth(
        {
            "authorization_endpoint": "https://idp.example/authorize",
            "client_id": "cid",
            "redirect_uri": (
                "http://localhost:9006/settings/connectors/mcp/oauth/callback"
            ),
            "code_challenge": "challenge",
            "scope": "openid",
            "include_resource": True,
            "resource": "https://mcp.example/mcp",
        },
        "signed-state",
    )
    assert url.startswith("https://idp.example/authorize?")
    assert "client_id=cid" in url
    assert "state=signed-state" in url
    assert "code_challenge=challenge" in url
    assert "code_challenge_method=S256" in url
    assert "resource=" in url


def test_authorization_url_for_oauth_includes_extra_google_params():
    url = authorization_url_for_oauth(
        {
            "authorization_endpoint": "https://accounts.google.com/o/oauth2/v2/auth",
            "client_id": "cid",
            "redirect_uri": (
                "http://localhost:9006/settings/connectors/mcp/oauth/callback"
            ),
            "code_challenge": "challenge",
            "scope": "https://www.googleapis.com/auth/drive.readonly",
            "include_resource": False,
            "extra_authorize_params": {
                "access_type": "offline",
                "prompt": "consent",
            },
        },
        "signed-state",
    )
    assert "access_type=offline" in url
    assert "prompt=consent" in url
    assert "resource=" not in url


def test_start_pre_registered_oauth_session_uses_pkce_not_resource():
    from app.agentive.connectors.mcp_oauth import start_pre_registered_oauth_session

    oauth = start_pre_registered_oauth_session(
        redirect_uri="http://localhost:9006/settings/connectors/mcp/oauth/callback",
        client_id="cid",
        client_secret="secret",
        authorization_endpoint="https://accounts.google.com/o/oauth2/v2/auth",
        token_endpoint="https://oauth2.googleapis.com/token",
        scopes=["https://www.googleapis.com/auth/drive.file"],
        extra_authorize_params={"access_type": "offline"},
    )
    assert oauth["status"] == "pending"
    assert oauth["include_resource"] is False
    assert oauth["code_verifier"]
    assert oauth["code_challenge"]
    assert oauth["scope"] == "https://www.googleapis.com/auth/drive.file"
    assert oauth["extra_authorize_params"]["access_type"] == "offline"


def test_map_drive_mcp_403_asks_to_enable_api():
    from app.agentive.connectors.mcp_adapter import map_mcp_discover_error
    from app.api.errors import BadRequestError

    request = httpx.Request("POST", "https://drivemcp.googleapis.com/mcp/v1")
    response = httpx.Response(
        403,
        request=request,
        json={
            "error": {
                "code": 403,
                "message": "Drive MCP API has not been used in project 123",
            }
        },
    )
    inner = httpx.HTTPStatusError("403", request=request, response=response)
    grouped = ExceptionGroup("unhandled errors in a TaskGroup", [inner])
    mapped = map_mcp_discover_error(grouped, "https://drivemcp.googleapis.com/mcp/v1")
    assert isinstance(mapped, BadRequestError)
    assert "drivemcp.googleapis.com" in mapped.message
    assert "Drive MCP API has not been used" in mapped.message


def test_map_sheets_mcp_403_asks_to_enable_api():
    from app.agentive.connectors.mcp_adapter import map_mcp_discover_error
    from app.api.errors import BadRequestError

    request = httpx.Request("POST", "https://sheetsmcp.googleapis.com/mcp/v1")
    response = httpx.Response(
        403, request=request, json={"error": {"message": "denied"}}
    )
    inner = httpx.HTTPStatusError("403", request=request, response=response)
    mapped = map_mcp_discover_error(inner, "https://sheetsmcp.googleapis.com/mcp/v1")
    assert isinstance(mapped, BadRequestError)
    assert "sheetsmcp.googleapis.com" in mapped.message
    assert "denied" in mapped.message


@pytest.mark.asyncio
async def test_probe_401_without_authorization_needs_oauth():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, headers={"WWW-Authenticate": "Bearer"})

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as client:
        probe = await probe_mcp_authorization("https://mcp.example/mcp", client=client)
    assert probe.status_code == 401
    assert probe.needs_oauth is True


@pytest.mark.asyncio
async def test_probe_401_with_authorization_does_not_need_oauth():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, headers={"WWW-Authenticate": "Bearer"})

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as client:
        probe = await probe_mcp_authorization(
            "https://mcp.example/mcp",
            {"Authorization": "Bearer token"},
            client=client,
        )
    assert probe.status_code == 401
    assert probe.needs_oauth is False


@pytest.mark.asyncio
async def test_mcp_oauth_callback_endpoint_rejects_bad_state(authenticated_client):
    resp = await authenticated_client.post(
        "/api/agentive/connectors/mcp/oauth/callback",
        json={"code": "not-a-real-code", "state": "not-a-real-state"},
    )
    assert resp.status_code == 401, resp.text
    assert "state" in resp.text.lower() or "auth" in resp.text.lower()


def test_mcp_request_headers_prefers_oauth_access_token():
    from app.agentive.connectors.mcp_oauth import mcp_request_headers

    headers = mcp_request_headers(
        {
            "url": "https://mcp.notion.com/mcp",
            "headers": {"Authorization": "Bearer stale"},
            "oauth": {
                "status": "authorized",
                "tokens": {
                    "access_token": "fresh-token",
                    "token_type": "Bearer",
                },
            },
        }
    )
    assert headers["Authorization"] == "Bearer fresh-token"


def test_mcp_safe_auth_state_redacts_oauth_tokens():
    from app.schemas.agentive.connectors import mcp_safe_auth_state

    safe = mcp_safe_auth_state(
        {
            "url": "https://mcp.notion.com/mcp",
            "oauth": {
                "status": "authorized",
                "tokens": {"access_token": "secret-token"},
                "client_secret": "s",
            },
        }
    )
    assert safe["oauth"] == {"status": "authorized"}
    assert "secret-token" not in str(safe)


def test_discovered_tools_persist_on_auth_state():
    """Connector has no discovered_tools field — tools live on auth_state."""
    from types import SimpleNamespace

    from app.agentive.connectors.mcp_adapter import (
        _discovered_tools,
        _persistable_discovered,
        _set_discovered_tools,
    )

    connector = SimpleNamespace(auth_state={"url": "https://mcp.notion.com/mcp"})
    _set_discovered_tools(
        connector,
        _persistable_discovered(
            [
                {
                    "remote_name": "search",
                    "description": "Search Notion",
                    "input_schema": {"type": "object"},
                }
            ]
        ),
    )
    # ``title`` and ``annotations`` ride along so a restart does not drop the
    # remote's own metadata — the approval card's "the server reports this as
    # read-only" note reads ``annotations`` and was unreachable without them.
    assert connector.auth_state["discovered_tools"] == [
        {
            "name": "search",
            "title": "",
            "description": "Search Notion",
            "input_schema": {"type": "object"},
            "annotations": {},
        }
    ]
    assert _discovered_tools(connector)[0]["name"] == "search"
    assert not hasattr(connector, "discovered_tools")
