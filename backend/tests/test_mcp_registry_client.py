"""Tests for official MCP Registry browse client (mocked HTTP)."""

from __future__ import annotations

import pytest

from app.agentive.connectors import mcp_registry_client as client

HTTP_REMOTE_ENTRY = {
    "server": {
        "name": "com.example/demo",
        "title": "Demo Server",
        "description": "A demo MCP server",
        "version": "1.0.0",
        "remotes": [{"type": "streamable-http", "url": "https://mcp.example.com/mcp"}],
    },
    "_meta": {
        "io.modelcontextprotocol.registry/official": {"isLatest": True},
    },
}

AUTH_REMOTE_ENTRY = {
    "server": {
        "name": "ai.smithery/example",
        "title": "Smithery Example",
        "version": "2.0.0",
        "remotes": [
            {
                "type": "streamable-http",
                "url": "https://server.smithery.ai/example/mcp",
                "headers": [
                    {
                        "name": "Authorization",
                        "value": "Bearer {smithery_api_key}",
                        "isSecret": True,
                        "isRequired": True,
                    }
                ],
            }
        ],
    },
    "_meta": {
        "io.modelcontextprotocol.registry/official": {"isLatest": True},
    },
}

STDIO_PACKAGE_ENTRY = {
    "server": {
        "name": "com.example/npm-server",
        "title": "NPM Server",
        "version": "0.1.0",
        "packages": [
            {
                "registryType": "npm",
                "identifier": "@example/mcp-server",
                "version": "0.1.0",
                "transport": {"type": "stdio"},
                "environmentVariables": [
                    {
                        "name": "API_TOKEN",
                        "description": "API token",
                        "isRequired": True,
                        "isSecret": True,
                    }
                ],
            }
        ],
    },
    "_meta": {
        "io.modelcontextprotocol.registry/official": {"isLatest": True},
    },
}


@pytest.fixture(autouse=True)
def _clear_cache():
    client.reset_registry_cache()
    yield
    client.reset_registry_cache()


def test_classify_direct_http():
    tier = client.classify_install_tier(HTTP_REMOTE_ENTRY["server"])
    assert tier == "direct_http"


def test_classify_http_with_auth():
    tier = client.classify_install_tier(AUTH_REMOTE_ENTRY["server"])
    assert tier == "http_with_auth"


def test_classify_stdio_package():
    tier = client.classify_install_tier(STDIO_PACKAGE_ENTRY["server"])
    assert tier == "stdio_package"


def test_normalize_entry():
    entry = client.normalize_registry_entry(HTTP_REMOTE_ENTRY)
    assert entry["name"] == "com.example/demo"
    assert entry["install_tier"] == "direct_http"
    assert entry["remote_url"] == "https://mcp.example.com/mcp"


def test_stdio_manual_recipe_validates_against_wire_schema():
    """manual_recipe must not carry fields absent from McpRegistryManualRecipe."""
    from app.schemas.agentive.connectors import McpRegistrySearchResponse

    entry = client.normalize_registry_entry(STDIO_PACKAGE_ENTRY)
    assert entry["manual_recipe"] is not None
    assert "env_vars" not in entry["manual_recipe"]
    assert entry["env_var_prompts"]
    payload = {"entries": [entry], "next_cursor": None, "count": 1}
    parsed = McpRegistrySearchResponse.model_validate(payload)
    assert parsed.entries[0].install_tier == "stdio_package"


def test_to_mount_request_direct_http():
    entry = client.normalize_registry_entry(HTTP_REMOTE_ENTRY)
    body = client.to_mount_request(entry)
    assert body["transport"] == "streamable_http"
    assert body["url"] == "https://mcp.example.com/mcp"
    assert body["registry_name"] == "com.example/demo"


def test_to_mount_request_http_with_auth_requires_secret():
    entry = client.normalize_registry_entry(AUTH_REMOTE_ENTRY)
    with pytest.raises(ValueError, match="missing required auth header"):
        client.to_mount_request(entry)
    body = client.to_mount_request(
        entry, secrets={"Authorization": "Bearer secret-token"}
    )
    assert body["headers"]["Authorization"] == "Bearer secret-token"


@pytest.mark.parametrize("flag", [False, True])
def test_to_mount_request_refuses_stdio_regardless_of_the_flag(monkeypatch, flag):
    """The flag never enabled anything, and now says so.

    A stdio mount spawns a process on the API host, so the command is
    re-derived from the in-repo catalog by ``catalog_slug``
    (``mcp_client._resolve_trusted_stdio_command``) and anything without one is
    refused. A registry recipe has no catalog entry, so turning
    ``MCP_REGISTRY_ENABLE_STDIO_INSTALL`` on only moved the failure later — the
    operator enabled a setting and then got "connector has no vetted
    catalog_slug" from the discovery step.
    """
    monkeypatch.setattr(client.settings, "MCP_REGISTRY_ENABLE_STDIO_INSTALL", flag)
    entry = client.normalize_registry_entry(STDIO_PACKAGE_ENTRY)
    with pytest.raises(ValueError, match="catalog"):
        client.to_mount_request(entry)


def test_allowlist_blocks_install(monkeypatch):
    monkeypatch.setattr(
        client.settings, "MCP_REGISTRY_INSTALL_ALLOWLIST", "com.allowed/*"
    )
    entry = client.normalize_registry_entry(HTTP_REMOTE_ENTRY)
    assert entry["install_allowed"] is False
    with pytest.raises(ValueError, match="not allowlisted"):
        client.to_mount_request(entry)


def test_empty_allowlist_fails_closed_outside_testing(monkeypatch):
    monkeypatch.setattr(client.settings, "MCP_REGISTRY_INSTALL_ALLOWLIST", "")
    monkeypatch.delenv("TESTING", raising=False)
    assert client.is_registry_install_allowed("com.example/demo") is False
    entry = client.normalize_registry_entry(HTTP_REMOTE_ENTRY)
    assert entry["install_allowed"] is False


def test_empty_allowlist_allows_under_testing(monkeypatch):
    monkeypatch.setattr(client.settings, "MCP_REGISTRY_INSTALL_ALLOWLIST", "")
    monkeypatch.setenv("TESTING", "1")
    assert client.is_registry_install_allowed("com.example/demo") is True


@pytest.mark.asyncio
async def test_search_registry_servers(monkeypatch):
    async def fake_fetch(path, params):
        assert path == "/v0/servers"
        assert params.get("version") == "latest"
        return {
            "servers": [HTTP_REMOTE_ENTRY],
            "metadata": {"nextCursor": "abc", "count": 1},
        }

    monkeypatch.setattr(client, "_fetch_registry", fake_fetch)
    result = await client.search_registry_servers(q="demo", limit=5)
    assert result["count"] == 1
    assert result["entries"][0]["name"] == "com.example/demo"
    assert result["next_cursor"] == "abc"


def test_collapse_to_latest_per_name():
    older = {
        "server": {
            "name": "io.github.firecrawl/firecrawl-mcp-server",
            "title": "Firecrawl MCP Server",
            "version": "3.21.3",
        },
        "_meta": {"io.modelcontextprotocol.registry/official": {"isLatest": False}},
    }
    latest = {
        "server": {
            "name": "io.github.firecrawl/firecrawl-mcp-server",
            "title": "Firecrawl MCP Server",
            "version": "3.22.2",
        },
        "_meta": {"io.modelcontextprotocol.registry/official": {"isLatest": True}},
    }
    other = {
        "server": {
            "name": "com.mcparmory/firecrawl",
            "title": "com.mcparmory/firecrawl",
            "version": "1.0.3",
        },
        "_meta": {"io.modelcontextprotocol.registry/official": {"isLatest": True}},
    }
    collapsed = client._collapse_to_latest_per_name([older, latest, other, older])
    assert [client._server_doc(i)["name"] for i in collapsed] == [
        "io.github.firecrawl/firecrawl-mcp-server",
        "com.mcparmory/firecrawl",
    ]
    assert client._server_doc(collapsed[0])["version"] == "3.22.2"


@pytest.mark.asyncio
async def test_search_collapses_duplicate_versions(monkeypatch):
    older = {
        "server": {
            "name": "com.example/demo",
            "title": "Demo Server",
            "version": "0.9.0",
            "remotes": [{"type": "streamable-http", "url": "https://old.example/mcp"}],
        },
        "_meta": {"io.modelcontextprotocol.registry/official": {"isLatest": False}},
    }

    async def fake_fetch(path, params):
        assert params.get("version") == "latest"
        return {
            "servers": [older, HTTP_REMOTE_ENTRY],
            "metadata": {},
        }

    monkeypatch.setattr(client, "_fetch_registry", fake_fetch)
    result = await client.search_registry_servers(q="demo", limit=20)
    assert result["count"] == 1
    assert result["entries"][0]["version"] == "1.0.0"
    assert result["entries"][0]["is_latest"] is True


@pytest.mark.asyncio
async def test_get_registry_server_latest(monkeypatch):
    async def fake_fetch(path, params):
        return {"servers": [HTTP_REMOTE_ENTRY, AUTH_REMOTE_ENTRY]}

    monkeypatch.setattr(client, "_fetch_registry", fake_fetch)
    entry = await client.get_registry_server("com.example/demo")
    assert entry["name"] == "com.example/demo"


@pytest.mark.asyncio
async def test_registry_search_api(authenticated_client, monkeypatch):
    async def fake_search(**kwargs):
        return {
            "entries": [client.normalize_registry_entry(HTTP_REMOTE_ENTRY)],
            "next_cursor": None,
            "count": 1,
        }

    monkeypatch.setattr(
        "app.agentive.connectors.mcp_registry_client.search_registry_servers",
        fake_search,
    )

    ws_resp = await authenticated_client.get("/api/workspaces")
    assert ws_resp.status_code == 200, ws_resp.text
    ws_payload = ws_resp.json()
    if isinstance(ws_payload, list):
        ws_id = ws_payload[0]["id"]
    else:
        ws_id = (ws_payload.get("workspaces") or ws_payload.get("items") or [{}])[0][
            "id"
        ]

    resp = await authenticated_client.get(
        "/api/agentive/connectors/mcp/registry/search",
        params={"q": "demo"},
        headers={"X-Integral-Scope": f"ws:{ws_id}"},
    )
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["count"] == 1
    assert data["entries"][0]["name"] == "com.example/demo"


async def _workspace_scope_header(authenticated_client) -> dict:
    ws_resp = await authenticated_client.get("/api/workspaces")
    assert ws_resp.status_code == 200, ws_resp.text
    ws_payload = ws_resp.json()
    if isinstance(ws_payload, list):
        ws_id = ws_payload[0]["id"]
    else:
        ws_id = (ws_payload.get("workspaces") or ws_payload.get("items") or [{}])[0][
            "id"
        ]
    return {"X-Integral-Scope": f"ws:{ws_id}"}


@pytest.mark.asyncio
async def test_registry_preview_uses_name_query_param(
    authenticated_client, monkeypatch
):
    """Slash-delimited registry names must not be path-captured (…/name/preview → 404)."""
    slashed = "io.github.AIops-tools/inference-aiops"

    async def fake_get(name: str):
        assert name == slashed
        return client.normalize_registry_entry(HTTP_REMOTE_ENTRY)

    monkeypatch.setattr(
        "app.agentive.connectors.mcp_registry_client.get_registry_server",
        fake_get,
    )

    headers = await _workspace_scope_header(authenticated_client)
    resp = await authenticated_client.get(
        "/api/agentive/connectors/mcp/registry/preview",
        params={"name": slashed},
        headers=headers,
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["registry_name"] == "com.example/demo"


@pytest.mark.asyncio
async def test_registry_preview_path_no_longer_swallows_name(
    authenticated_client, monkeypatch
):
    """Old /servers/{name}/preview route treated '/preview' as part of the name."""

    async def fake_get(name: str):
        raise AssertionError(f"must not look up path leftover {name!r}")

    monkeypatch.setattr(
        "app.agentive.connectors.mcp_registry_client.get_registry_server",
        fake_get,
    )

    headers = await _workspace_scope_header(authenticated_client)
    resp = await authenticated_client.get(
        "/api/agentive/connectors/mcp/registry/servers/"
        "io.github.AIops-tools/inference-aiops/preview",
        headers=headers,
    )
    assert resp.status_code == 404
