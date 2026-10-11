"""Cloudflare catalog admission without provider consent or execution."""

from types import SimpleNamespace
from urllib.parse import parse_qs, urlparse

import pytest

from app.connectors.catalog_loader import get_catalog_entry, reset_catalog_cache

pytestmark = pytest.mark.smoke


def test_cloudflare_catalog_uses_official_dynamic_oauth_endpoint():
    reset_catalog_cache()
    entry = get_catalog_entry("cloudflare")
    assert entry["display_name"] == "Cloudflare"
    assert entry["category"] == "mcp_server"
    assert entry["kind"] == "mcp"
    assert entry["transport"] == "streamable_http"
    assert entry["url"] == "https://mcp.cloudflare.com/mcp"
    assert entry["auth"] == {"type": "none", "fields": []}
    assert entry["oauth"] is None  # Provider metadata is discovered at mount.
    assert entry["vetted"] is True
    assert entry["hidden"] is False
    assert entry["read_only_tools"] == []


@pytest.mark.parametrize("remote_name", ["search", "execute", "delete_zone"])
def test_cloudflare_tools_do_not_bypass_approval(remote_name):
    from app.agentive.connectors.mcp_tool_class import is_write_tool

    reset_catalog_cache()
    assert is_write_tool(
        {
            "_mcp_remote_name": remote_name,
            "_mcp_annotations": {"readOnlyHint": True},
        },
        auth_state={"catalog_slug": "cloudflare"},
    )


@pytest.mark.asyncio
async def test_cloudflare_catalog_install_stops_at_owner_consent(
    authenticated_client, monkeypatch
):
    """Use real mount/persistence; mock only the external OAuth handshake."""
    from app.agentive.connectors import mcp_adapter, mcp_oauth

    reset_catalog_cache()
    seen = {}

    async def validate_url(url):
        assert url == "https://mcp.cloudflare.com/mcp"

    async def probe(url, headers):
        seen["probe"] = (url, headers)
        return SimpleNamespace(status_code=401, needs_oauth=True, response=None)

    async def start(url, *, probe_response, redirect_uri):
        seen["redirect_uri"] = redirect_uri
        return {
            "status": "pending",
            "authorization_endpoint": "https://idp.example.test/authorize",
            "client_id": "synthetic-cloudflare-client",
            "redirect_uri": redirect_uri,
            "code_challenge": "synthetic-challenge",
            "code_verifier": "synthetic-private-verifier",
        }

    async def refuse_discovery(*args, **kwargs):
        raise AssertionError("Pending consent must not discover provider tools")

    monkeypatch.setattr(mcp_adapter, "validate_outbound_http_url", validate_url)
    monkeypatch.setattr(mcp_oauth, "probe_mcp_authorization", probe)
    monkeypatch.setattr(mcp_oauth, "start_mcp_oauth_session", start)
    monkeypatch.setattr(mcp_adapter, "discover", refuse_discovery)
    workspace_response = await authenticated_client.get("/api/workspaces")
    assert workspace_response.status_code == 200
    payload = workspace_response.json()
    workspaces = payload if isinstance(payload, list) else payload.get("workspaces", [])
    assert workspaces
    workspace_id = workspaces[0]["id"]
    headers = {"X-Integral-Scope": f"ws:{workspace_id}"}
    catalog = await authenticated_client.get(
        "/api/agentive/connectors/catalog", headers=headers
    )
    assert catalog.status_code == 200
    assert any(row["slug"] == "cloudflare" for row in catalog.json()["entries"])
    response = await authenticated_client.post(
        "/api/agentive/connectors/catalog/cloudflare/install",
        headers=headers,
        json={"label": "Disposable cloud qualification"},
    )
    assert response.status_code == 200, response.text
    result = response.json()
    assert result["action"] == "oauth"
    assert seen["probe"] == ("https://mcp.cloudflare.com/mcp", None)
    consent = urlparse(result["consent_url"])
    assert consent.netloc == "idp.example.test"
    params = parse_qs(consent.query)
    assert params["state"] == [result["state"]]
    assert params["redirect_uri"] == [seen["redirect_uri"]]
    assert params["code_challenge_method"] == ["S256"]
    connector = result["connector"]
    assert connector["workspace_id"] == workspace_id
    assert connector["connection_mode"] == "per_user"
    assert connector["label"] == "Disposable cloud qualification"
    assert connector["health_status"] == "unknown"
    assert connector["last_error"] is None
    assert connector["auth_state"]["catalog_slug"] == "cloudflare"
    assert "synthetic-private-verifier" not in response.text
