"""Microsoft 365 mail catalog entry stays read-only and does not log tokens."""

import json

import pytest

from app.agentive.api.connectors import (
    _audit_snapshot,
    _catalog_install_microsoft_mail_oauth,
)
from app.agentive.connectors.mcp_client import _allowed_spawn_env_keys
from app.api.errors import BadRequestError
from app.connectors.catalog_loader import get_catalog_entry
from app.services.connectors.microsoft_mail_oauth import (
    SCOPES,
    absolute_redirect,
    consent_url,
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
