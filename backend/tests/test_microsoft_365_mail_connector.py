"""Microsoft 365 mail catalog entry stays read-only and does not log tokens."""

import json
from datetime import datetime, timedelta, timezone

import httpx
import pytest

from app.agentive.api.connectors import (
    _audit_snapshot,
    _catalog_install_microsoft_mail_oauth,
)
from app.agentive.connectors.mcp_client import (
    _allowed_spawn_env_keys,
    _remote_tool_error,
)
from app.agentive.connectors.microsoft_mail import ensure_fresh_access_token
from app.agentive.services.connector_registry_node import decrypt_auth_state
from app.api.errors import BadRequestError, ConnectorAuthError
from app.connectors.catalog_loader import get_catalog_entry
from app.services.connectors.microsoft_mail_oauth import (
    SCOPES,
    absolute_redirect,
    access_token_is_fresh,
    consent_url,
    refresh_access_token,
    spawn_env,
)

_WRITE_MARKERS = ("send", "reply", "draft", "delete", "move", "update", "create")


def test_catalog_loads_read_only_mail_tools():
    """The catalog pins the read-only mail server and omits send tools."""
    entry = get_catalog_entry("microsoft_365_mail")
    assert entry["kind"] == "mcp"
    assert entry["transport"] == "stdio"
    assert entry["command"] == "npx"
    assert "@softeria/ms-365-mcp-server@0.170.2" in entry["args"]
    assert "--read-only" in entry["args"]
    assert "--org-mode" in entry["args"]
    assert "--preset" not in entry["args"]
    enabled = entry["args"][entry["args"].index("--enabled-tools") + 1]
    assert "list-shared-mailbox-messages" in enabled
    names = set(entry["read_only_tools"])
    assert "list-shared-mailbox-messages" in names
    assert "get-shared-mailbox-message" in names
    assert not any(any(marker in name for marker in _WRITE_MARKERS) for name in names)
    assert "Mail.Send" not in " ".join(entry["args"])
    assert "Mail.Send" not in SCOPES


def test_consent_url_is_tenant_scoped_and_read_only():
    """Consent is the v2 authorize endpoint for one tenant, without Mail.Send."""
    url = consent_url(
        tenant="tenant-1",
        client_id="client-1",
        redirect_uri="https://app.example/callback",
        state="signed-state",
    )
    assert url.startswith(
        "https://login.microsoftonline.com/tenant-1/oauth2/v2.0/authorize?"
    )
    assert "Mail.Read" in url
    assert "Mail.Read.Shared" in url
    assert "Mail.Send" not in url


def test_blank_redirect_is_rejected_before_entra():
    """An empty redirect is not an absolute URI and must not be sent to Entra."""
    with pytest.raises(ValueError, match="absolute"):
        absolute_redirect("")
    with pytest.raises(ValueError):
        consent_url(
            tenant="tenant-1",
            client_id="client-1",
            redirect_uri="",
            state="signed-state",
        )


def test_spawn_env_carries_the_access_token_and_not_the_refresh_token():
    """The stdio child receives the access token, and the refresh token stays off env."""
    env = spawn_env(
        access_token="access-1",
        client_id="client-1",
        client_secret="secret-1",
        tenant_id="tenant-1",
    )
    assert env["MS365_MCP_OAUTH_TOKEN"] == "access-1"
    assert "refresh_token" not in env
    allowed = _allowed_spawn_env_keys({"catalog_slug": "microsoft_365_mail"})
    assert "MS365_MCP_OAUTH_TOKEN" in allowed
    assert "MS365_MCP_CLIENT_SECRET" in allowed


@pytest.mark.asyncio
async def test_install_requires_tenant_and_client_id():
    """Install refuses to start until tenant and client id are present."""
    entry = get_catalog_entry("microsoft_365_mail")
    with pytest.raises(BadRequestError) as missing:
        await _catalog_install_microsoft_mail_oauth(
            user_id="user-1",
            workspace_id="ws-1",
            entry=entry,
            secrets={},
        )
    assert "client_id" in str(missing.value)
    with pytest.raises(BadRequestError) as missing_tenant:
        await _catalog_install_microsoft_mail_oauth(
            user_id="user-1",
            workspace_id="ws-1",
            entry=entry,
            secrets={"client_id": "client-1", "client_secret": "secret-1"},
        )
    assert "tenant" in str(missing_tenant.value)


def test_audit_snapshot_omits_token_material():
    """Change-event snapshots do not include connector auth state."""

    class _Connector:
        owner = "user-1"
        kind = "mcp"
        subclass_slug = "mcp"
        sync_interval_seconds = 300
        capabilities = ["mcp.client"]
        permissions = []
        auth_state = {
            "refresh_token": "refresh-secret",
            "env": {"MS365_MCP_OAUTH_TOKEN": "access-secret"},
        }

    snapshot = _audit_snapshot(_Connector())
    encoded = json.dumps(snapshot)
    assert "refresh-secret" not in encoded
    assert "access-secret" not in encoded
    assert "auth_state" not in snapshot


def _mail_auth(**overrides):
    auth = {
        "catalog_slug": "microsoft_365_mail",
        "refresh_token": "refresh-1",
        "env": {
            "MS365_MCP_OAUTH_TOKEN": "old-access",
            "MS365_MCP_CLIENT_ID": "client-1",
            "MS365_MCP_CLIENT_SECRET": "secret-1",
            "MS365_MCP_TENANT_ID": "tenant-1",
        },
    }
    auth.update(overrides)
    return auth


def test_missing_expiry_is_not_treated_as_a_live_token():
    """A mount that never stored expiry must refresh before the next read."""
    assert access_token_is_fresh(_mail_auth()) is False
    later = datetime.now(timezone.utc) + timedelta(hours=1)
    assert (
        access_token_is_fresh(
            _mail_auth(access_token_expires_at=later.isoformat()),
        )
        is True
    )


def test_graph_invalid_token_is_an_authentication_failure():
    """Graph's InvalidAuthenticationToken body is a 401, not a generic read error."""

    class _Block:
        text = '{"error_code":"InvalidAuthenticationToken","http_status":401}'

    class _Result:
        content = [_Block()]

    err = _remote_tool_error(_Result())
    assert err.error_code == "remote_authentication_failed"
    assert "401" in str(err)


@pytest.mark.asyncio
async def test_refresh_replaces_the_spawn_token_and_keeps_a_rotated_refresh_token():
    """A stale mailbox read mints a new access token before the stdio spawn."""
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        seen["body"] = request.content.decode()
        return httpx.Response(
            200,
            json={
                "access_token": "new-access",
                "expires_in": 3600,
                "refresh_token": "refresh-2",
            },
        )

    class _Connector:
        id = "n.Connector.mail"
        auth_state = _mail_auth()
        saves = 0

        async def save(self):
            self.saves += 1

    connector = _Connector()
    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as client:
        updated = await ensure_fresh_access_token(connector, http_client=client)
    assert seen["url"].endswith("/tenant-1/oauth2/v2.0/token")
    assert "grant_type=refresh_token" in seen["body"]
    assert "refresh_token=refresh-1" in seen["body"]
    assert updated["env"]["MS365_MCP_OAUTH_TOKEN"] == "new-access"
    assert updated["refresh_token"] == "refresh-2"
    assert updated["access_token_expires_at"]
    stored = decrypt_auth_state(connector.auth_state)
    assert stored["env"]["MS365_MCP_OAUTH_TOKEN"] == "new-access"
    assert stored["refresh_token"] == "refresh-2"
    assert connector.saves == 1


@pytest.mark.asyncio
async def test_a_fresh_access_token_is_not_refreshed():
    """A token outside the skew window does not call Entra."""

    def handler(request: httpx.Request) -> httpx.Response:
        raise AssertionError(request.url)

    later = (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat()

    class _Connector:
        id = "n.Connector.fresh"
        auth_state = _mail_auth(access_token_expires_at=later)

        async def save(self):
            raise AssertionError("fresh token must not be rewritten")

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as client:
        updated = await ensure_fresh_access_token(_Connector(), http_client=client)
    assert updated["env"]["MS365_MCP_OAUTH_TOKEN"] == "old-access"


@pytest.mark.asyncio
async def test_rejected_refresh_requires_reauth_and_a_server_error_does_not():
    """invalid_grant asks for a new consent. A 503 stays retryable."""

    async def _status(status: int):
        transport = httpx.MockTransport(lambda request: httpx.Response(status, json={}))
        async with httpx.AsyncClient(transport=transport) as client:
            with pytest.raises(ConnectorAuthError) as caught:
                await refresh_access_token(
                    tenant="tenant-1",
                    client_id="client-1",
                    client_secret="secret-1",
                    refresh_token="refresh-1",
                    http_client=client,
                )
        return caught.value

    rejected = await _status(400)
    assert rejected.details["reauth_required"] is True
    unavailable = await _status(503)
    assert unavailable.details["reauth_required"] is False
