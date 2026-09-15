"""In-repo connector catalog loader + install API."""

from __future__ import annotations

from urllib.parse import unquote

import pytest

from app.config import settings as _settings  # noqa: F401
from app.connectors import catalog_loader as loader


@pytest.fixture(autouse=True)
def _reset_catalog():
    loader.reset_catalog_cache()
    yield
    loader.reset_catalog_cache()


def test_load_catalog_includes_native_seeds():
    entries = loader.load_catalog()
    slugs = {e["slug"] for e in entries}
    assert slugs == {
        "calendly",
        "gmail",
        "github_issues",
        "google_drive",
        "google_gmail",
        "google_sheets",
        "notion",
        "quickbooks",
        "quickbooks_mcp",
    }
    gmail = loader.get_catalog_entry("gmail")
    assert gmail["auth"]["type"] == "oauth2"
    assert gmail["vetted"] is True
    gmail_fields = {f["name"] for f in gmail["auth"]["fields"]}
    assert "client_id" in gmail_fields
    assert "client_secret" in gmail_fields
    qb = loader.get_catalog_entry("quickbooks")
    qb_fields = {f["name"] for f in qb["auth"]["fields"]}
    assert qb_fields == {"client_id", "client_secret", "environment"}
    gh = loader.get_catalog_entry("github_issues")
    names = {f["name"] for f in gh["auth"]["fields"]}
    assert names == {"owner", "repo"}
    calendly = loader.get_catalog_entry("calendly")
    assert calendly["kind"] == "mcp"
    assert calendly["category"] == "mcp_server"
    assert calendly["url"] == "https://mcp.calendly.com/"
    assert calendly["transport"] == "streamable_http"
    notion = loader.get_catalog_entry("notion")
    assert notion["kind"] == "mcp"
    assert notion["category"] == "mcp_server"
    assert notion["url"] == "https://mcp.notion.com/mcp"
    assert notion["transport"] == "streamable_http"
    drive = loader.get_catalog_entry("google_drive")
    assert drive["kind"] == "mcp"
    assert drive["category"] == "mcp_server"
    assert drive["url"] == "https://drivemcp.googleapis.com/mcp/v1"
    assert drive["transport"] == "streamable_http"
    assert drive["auth"]["type"] == "oauth2"
    drive_fields = {f["name"] for f in drive["auth"]["fields"]}
    assert drive_fields == {"client_id", "client_secret"}
    assert drive["oauth"]["authorization_endpoint"].startswith(
        "https://accounts.google.com/"
    )
    assert "https://www.googleapis.com/auth/drive.readonly" in drive["oauth"]["scopes"]
    gmail_mcp = loader.get_catalog_entry("google_gmail")
    assert gmail_mcp["kind"] == "mcp"
    assert gmail_mcp["url"] == "https://gmailmcp.googleapis.com/mcp/v1"
    assert gmail_mcp["auth"]["type"] == "oauth2"
    assert (
        "https://www.googleapis.com/auth/gmail.compose" in gmail_mcp["oauth"]["scopes"]
    )
    sheets = loader.get_catalog_entry("google_sheets")
    assert sheets["kind"] == "mcp"
    assert sheets["url"] == "https://sheetsmcp.googleapis.com/mcp/v1"
    assert sheets["auth"]["type"] == "oauth2"
    assert "https://www.googleapis.com/auth/spreadsheets" in sheets["oauth"]["scopes"]
    qb_mcp = loader.get_catalog_entry("quickbooks_mcp")
    assert qb_mcp["kind"] == "mcp"
    assert qb_mcp["category"] == "mcp_package"
    assert qb_mcp["transport"] == "stdio"
    assert qb_mcp["command"] == "npx"
    assert "github:intuit/quickbooks-online-mcp-server" in qb_mcp["args"]
    assert qb_mcp["auth"]["type"] == "oauth2"
    qb_mcp_fields = {f["name"] for f in qb_mcp["auth"]["fields"]}
    assert "client_id" in qb_mcp_fields
    assert "QUICKBOOKS_REFRESH_TOKEN" not in qb_mcp_fields
    disable_fields = {
        f["name"]: f
        for f in qb_mcp["auth"]["fields"]
        if f["name"].startswith("QUICKBOOKS_DISABLE_")
    }
    assert set(disable_fields) == {
        "QUICKBOOKS_DISABLE_WRITE",
        "QUICKBOOKS_DISABLE_UPDATE",
        "QUICKBOOKS_DISABLE_DELETE",
    }
    # Ships read-only: the DISABLE_* toggles default ON (see
    # test_quickbooks_mcp_ships_read_only for why).
    assert all(
        f["control"] == "toggle" and f["default"] == "true"
        for f in disable_fields.values()
    )
    assert qb_mcp["oauth"] is None
    assert qb_mcp["env_defaults"]["QUICKBOOKS_DISABLE_WRITE"] == "true"
    assert qb_mcp["icon"] == "quickbooks"


def test_unknown_catalog_slug_raises():
    with pytest.raises(KeyError):
        loader.get_catalog_entry("not-a-real-connector")


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


def test_invalid_catalog_yaml_raises(tmp_path):
    (tmp_path / "broken.yaml").write_text(
        "display_name: Only a name\n", encoding="utf-8"
    )
    with pytest.raises(ValueError, match="slug"):
        loader.load_catalog(catalog_dir=tmp_path)


def test_slug_must_match_filename(tmp_path):
    (tmp_path / "foo.yaml").write_text(
        """
slug: bar
display_name: Bar
category: native
kind: sync
icon: mcp
auth:
  type: none
  fields: []
""",
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="must match filename"):
        loader.load_catalog(catalog_dir=tmp_path)


@pytest.mark.asyncio
async def test_catalog_list_api(authenticated_client):
    headers = await _workspace_scope_header(authenticated_client)
    resp = await authenticated_client.get(
        "/api/agentive/connectors/catalog",
        headers=headers,
    )
    assert resp.status_code == 200, resp.text
    data = resp.json()
    slugs = {e["slug"] for e in data["entries"]}
    assert "gmail" in slugs
    assert "quickbooks" in slugs
    assert "github_issues" in slugs
    assert "calendly" in slugs
    assert "notion" in slugs
    assert "google_drive" in slugs
    assert "google_gmail" in slugs
    assert "google_sheets" in slugs
    assert "quickbooks_mcp" in slugs
    assert data["total"] == 9


@pytest.mark.asyncio
async def test_catalog_unknown_slug_404(authenticated_client):
    headers = await _workspace_scope_header(authenticated_client)
    resp = await authenticated_client.get(
        "/api/agentive/connectors/catalog/not-a-real-connector",
        headers=headers,
    )
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_catalog_get_gmail_slug(authenticated_client):
    headers = await _workspace_scope_header(authenticated_client)
    resp = await authenticated_client.get(
        "/api/agentive/connectors/catalog/gmail",
        headers=headers,
    )
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["slug"] == "gmail"
    assert data["auth"]["type"] == "oauth2"
    assert data["vetted"] is True


@pytest.mark.asyncio
async def test_catalog_install_unknown_slug_404(authenticated_client):
    headers = await _workspace_scope_header(authenticated_client)
    resp = await authenticated_client.post(
        "/api/agentive/connectors/catalog/not-a-real-connector/install",
        json={},
        headers=headers,
    )
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_catalog_install_github_issues(authenticated_client):
    headers = await _workspace_scope_header(authenticated_client)
    resp = await authenticated_client.post(
        "/api/agentive/connectors/catalog/github_issues/install",
        json={"secrets": {"owner": "acme", "repo": "widgets"}},
        headers=headers,
    )
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["action"] == "created"
    assert data["slug"] == "github_issues"
    assert data["connector"]["subclass_slug"] == "github_issues"
    assert data["connector"]["auth_state"]["owner"] == "acme"
    assert data["connector"]["auth_state"]["repo"] == "widgets"
    assert data["connector"]["auth_state"]["catalog_slug"] == "github_issues"


@pytest.mark.asyncio
async def test_catalog_install_gmail_returns_oauth(authenticated_client, monkeypatch):
    def fake_consent(user_id, **kwargs):
        return "https://accounts.google.com/fake", "state-token"

    monkeypatch.setenv("GMAIL_OAUTH_CLIENT_ID", "g-client")
    monkeypatch.setattr(
        "app.services.connectors.gmail_oauth.build_consent_url",
        fake_consent,
    )
    headers = await _workspace_scope_header(authenticated_client)
    resp = await authenticated_client.post(
        "/api/agentive/connectors/catalog/gmail/install",
        json={},
        headers=headers,
    )
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["action"] == "oauth"
    assert data["consent_url"] == "https://accounts.google.com/fake"
    assert data["connector"]["subclass_slug"] == "gmail"
    assert data["connector"]["auth_state"]["catalog_slug"] == "gmail"


@pytest.mark.asyncio
async def test_catalog_install_quickbooks_accepts_ui_credentials(
    authenticated_client, monkeypatch
):
    seen = {}

    def fake_consent(user_id, **kwargs):
        seen["client_id"] = kwargs.get("client_id")
        seen["connector_id"] = kwargs.get("connector_id")
        return "https://appcenter.intuit.com/connect/oauth2?fake=1", "state-token"

    monkeypatch.delenv("QUICKBOOKS_CLIENT_ID", raising=False)
    monkeypatch.delenv("QUICKBOOKS_CLIENT_SECRET", raising=False)
    monkeypatch.setattr(
        "app.services.connectors.quickbooks_oauth.build_consent_url",
        fake_consent,
    )
    headers = await _workspace_scope_header(authenticated_client)
    resp = await authenticated_client.post(
        "/api/agentive/connectors/catalog/quickbooks/install",
        json={
            "secrets": {
                "client_id": "ui-client-id",
                "client_secret": "ui-client-secret",
                "environment": "sandbox",
            }
        },
        headers=headers,
    )
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["action"] == "oauth"
    assert data["consent_url"].startswith("https://appcenter.intuit.com/")
    assert seen["client_id"] == "ui-client-id"
    assert seen["connector_id"] == data["connector"]["id"]
    assert data["connector"]["subclass_slug"] == "quickbooks"
    assert data["connector"]["auth_state"]["client_id"] == "ui-client-id"
    assert "client_secret" not in data["connector"]["auth_state"]
    assert data["connector"]["auth_state"]["environment"] == "sandbox"


@pytest.mark.asyncio
async def test_catalog_install_quickbooks_requires_client_id_without_env(
    authenticated_client, monkeypatch
):
    monkeypatch.delenv("QUICKBOOKS_CLIENT_ID", raising=False)
    monkeypatch.setattr(
        "app.services.connectors.quickbooks_oauth._env_client_id",
        lambda: "",
    )
    headers = await _workspace_scope_header(authenticated_client)
    resp = await authenticated_client.post(
        "/api/agentive/connectors/catalog/quickbooks/install",
        json={},
        headers=headers,
    )
    assert resp.status_code == 400, resp.text
    assert "Client ID" in resp.text


@pytest.mark.asyncio
async def test_catalog_mcp_install_does_not_call_registry(
    authenticated_client, monkeypatch, tmp_path
):
    yaml_path = tmp_path / "echo.yaml"
    yaml_path.write_text(
        """
slug: echo
display_name: Echo MCP
description: Test MCP
category: mcp_server
kind: mcp
icon: mcp
transport: streamable_http
url: https://mcp.example.test/mcp
auth:
  type: none
  fields: []
""",
        encoding="utf-8",
    )

    monkeypatch.setattr(loader, "CATALOG_DIR", tmp_path)
    loader.reset_catalog_cache()

    called = {"registry": False}

    async def boom(*args, **kwargs):
        called["registry"] = True
        raise AssertionError("must not hit official MCP registry")

    monkeypatch.setattr(
        "app.agentive.connectors.mcp_registry_client.search_registry_servers",
        boom,
    )

    async def fake_mount(*, owner_id, workspace_id, url, headers=None):
        class _C:
            id = "c-mcp"
            kind = "mcp"
            owner = "u"
            auth_state = {
                "url": "https://mcp.example.test/mcp",
                "transport": "streamable_http",
            }
            sync_cursor = None
            mapping_profile = None
            permissions = []
            capabilities = ["mcp.invoke"]
            subclass_slug = "mcp"
            sync_interval_seconds = 300
            last_synced_at = None
            workspace_id = None
            health_status = "healthy"
            last_error = None
            last_health_at = None
            created_at = "2026-08-27T00:00:00Z"
            updated_at = "2026-08-27T00:00:00Z"

            async def save(self):
                return None

        assert url == "https://mcp.example.test/mcp"
        assert owner_id
        node = _C()
        node.auth_state = {"url": url, "transport": "streamable_http"}
        node.workspace_id = workspace_id
        return node

    monkeypatch.setattr(
        "app.agentive.connectors.mcp_adapter.mount_mcp_connector",
        fake_mount,
    )

    headers = await _workspace_scope_header(authenticated_client)
    resp = await authenticated_client.post(
        "/api/agentive/connectors/catalog/echo/install",
        json={},
        headers=headers,
    )
    assert resp.status_code == 200, resp.text
    assert called["registry"] is False
    data = resp.json()
    assert data["action"] == "created"
    assert data["connector"]["auth_state"]["catalog_slug"] == "echo"
    assert data["connector"]["auth_state"]["display_name"] == "Echo MCP"


@pytest.mark.asyncio
async def test_catalog_http_mcp_install_returns_oauth_when_pending(
    authenticated_client, monkeypatch, tmp_path
):
    (tmp_path / "echo.yaml").write_text(
        """
slug: echo
display_name: Echo MCP
description: Test MCP
category: mcp_server
kind: mcp
icon: mcp
transport: streamable_http
url: https://mcp.example.test/mcp
auth:
  type: none
  fields: []
""",
        encoding="utf-8",
    )
    monkeypatch.setattr(loader, "CATALOG_DIR", tmp_path)
    loader.reset_catalog_cache()

    async def fake_mount(*, owner_id, workspace_id, url, headers=None):
        class _C:
            id = "c-oauth"
            kind = "mcp"
            owner = "u"
            auth_state: dict = {}
            sync_cursor = None
            mapping_profile = None
            permissions = []
            capabilities = ["mcp.invoke"]
            subclass_slug = "mcp"
            sync_interval_seconds = 300
            last_synced_at = None
            workspace_id = None
            health_status = "unknown"
            last_error = None
            last_health_at = None
            created_at = "2026-08-27T00:00:00Z"
            updated_at = "2026-08-27T00:00:00Z"

            async def save(self):
                return None

        node = _C()
        node.auth_state = {
            "url": url,
            "transport": "streamable_http",
            "oauth": {
                "status": "pending",
                "state": "signed-state",
                "authorization_endpoint": "https://idp.example/authorize",
                "client_id": "cid",
                "redirect_uri": (
                    "http://localhost:9006/settings/connectors/mcp/oauth/callback"
                ),
                "code_challenge": "challenge",
            },
        }
        node.workspace_id = workspace_id
        return node

    monkeypatch.setattr(
        "app.agentive.connectors.mcp_adapter.mount_mcp_connector",
        fake_mount,
    )

    headers = await _workspace_scope_header(authenticated_client)
    resp = await authenticated_client.post(
        "/api/agentive/connectors/catalog/echo/install",
        json={},
        headers=headers,
    )
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["action"] == "oauth"
    assert data["consent_url"].startswith("https://idp.example/authorize")
    assert "state=signed-state" in data["consent_url"]
    assert data["state"] == "signed-state"
    assert data["connector"]["id"] == "c-oauth"


@pytest.mark.asyncio
async def test_catalog_get_calendly_slug(authenticated_client):
    headers = await _workspace_scope_header(authenticated_client)
    resp = await authenticated_client.get(
        "/api/agentive/connectors/catalog/calendly",
        headers=headers,
    )
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["slug"] == "calendly"
    assert data["kind"] == "mcp"
    assert data["url"] == "https://mcp.calendly.com/"
    assert data["transport"] == "streamable_http"


@pytest.mark.asyncio
async def test_catalog_get_notion_slug(authenticated_client):
    headers = await _workspace_scope_header(authenticated_client)
    resp = await authenticated_client.get(
        "/api/agentive/connectors/catalog/notion",
        headers=headers,
    )
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["slug"] == "notion"
    assert data["kind"] == "mcp"
    assert data["url"] == "https://mcp.notion.com/mcp"
    assert data["transport"] == "streamable_http"


@pytest.mark.asyncio
async def test_catalog_get_google_drive_slug(authenticated_client):
    headers = await _workspace_scope_header(authenticated_client)
    resp = await authenticated_client.get(
        "/api/agentive/connectors/catalog/google_drive",
        headers=headers,
    )
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["slug"] == "google_drive"
    assert data["kind"] == "mcp"
    assert data["url"] == "https://drivemcp.googleapis.com/mcp/v1"
    assert data["transport"] == "streamable_http"
    assert data["auth"]["type"] == "oauth2"


@pytest.mark.asyncio
async def test_catalog_get_google_gmail_slug(authenticated_client):
    headers = await _workspace_scope_header(authenticated_client)
    resp = await authenticated_client.get(
        "/api/agentive/connectors/catalog/google_gmail",
        headers=headers,
    )
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["slug"] == "google_gmail"
    assert data["kind"] == "mcp"
    assert data["url"] == "https://gmailmcp.googleapis.com/mcp/v1"
    assert data["auth"]["type"] == "oauth2"


@pytest.mark.asyncio
async def test_catalog_get_google_sheets_slug(authenticated_client):
    headers = await _workspace_scope_header(authenticated_client)
    resp = await authenticated_client.get(
        "/api/agentive/connectors/catalog/google_sheets",
        headers=headers,
    )
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["slug"] == "google_sheets"
    assert data["kind"] == "mcp"
    assert data["url"] == "https://sheetsmcp.googleapis.com/mcp/v1"
    assert data["auth"]["type"] == "oauth2"


@pytest.mark.asyncio
async def test_catalog_install_google_drive_returns_oauth(
    authenticated_client, monkeypatch
):
    probed = {"called": False}

    async def boom_probe(*args, **kwargs):
        probed["called"] = True
        raise AssertionError("Drive install must not probe MCP for 401")

    monkeypatch.setattr(
        "app.agentive.connectors.mcp_oauth.probe_mcp_authorization",
        boom_probe,
    )
    monkeypatch.setattr(
        "app.agentive.connectors.mcp_oauth.google_oauth_client_credentials",
        lambda: ("g-client", "g-secret"),
    )
    headers = await _workspace_scope_header(authenticated_client)
    resp = await authenticated_client.post(
        "/api/agentive/connectors/catalog/google_drive/install",
        json={},
        headers=headers,
    )
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["action"] == "oauth"
    assert data["slug"] == "google_drive"
    assert data["consent_url"].startswith("https://accounts.google.com/")
    assert "access_type=offline" in data["consent_url"]
    assert "prompt=consent" in data["consent_url"]
    assert "drive.readonly" in data["consent_url"]
    assert "/settings/connectors/mcp/oauth/callback" in unquote(data["consent_url"])
    assert data["connector"]["kind"] == "mcp"
    assert data["connector"]["subclass_slug"] == "mcp"
    assert data["connector"]["auth_state"]["catalog_slug"] == "google_drive"
    assert data["connector"]["auth_state"]["oauth"]["status"] == "pending"
    assert probed["called"] is False


@pytest.mark.asyncio
async def test_catalog_install_google_gmail_requests_gmail_scopes(
    authenticated_client, monkeypatch
):
    monkeypatch.setattr(
        "app.agentive.connectors.mcp_oauth.google_oauth_client_credentials",
        lambda: ("g-client", "g-secret"),
    )
    headers = await _workspace_scope_header(authenticated_client)
    resp = await authenticated_client.post(
        "/api/agentive/connectors/catalog/google_gmail/install",
        json={},
        headers=headers,
    )
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["action"] == "oauth"
    assert data["slug"] == "google_gmail"
    assert "gmail.readonly" in data["consent_url"]
    assert "gmail.compose" in data["consent_url"]
    assert data["connector"]["kind"] == "mcp"


@pytest.mark.asyncio
async def test_catalog_install_google_sheets_requests_sheets_scopes(
    authenticated_client, monkeypatch
):
    monkeypatch.setattr(
        "app.agentive.connectors.mcp_oauth.google_oauth_client_credentials",
        lambda: ("g-client", "g-secret"),
    )
    headers = await _workspace_scope_header(authenticated_client)
    resp = await authenticated_client.post(
        "/api/agentive/connectors/catalog/google_sheets/install",
        json={},
        headers=headers,
    )
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["action"] == "oauth"
    assert data["slug"] == "google_sheets"
    assert "spreadsheets" in data["consent_url"]
    assert data["connector"]["kind"] == "mcp"


@pytest.mark.asyncio
async def test_catalog_install_google_drive_requires_client_id(
    authenticated_client, monkeypatch
):
    monkeypatch.setattr(
        "app.agentive.connectors.mcp_oauth.google_oauth_client_credentials",
        lambda: ("", ""),
    )
    headers = await _workspace_scope_header(authenticated_client)
    resp = await authenticated_client.post(
        "/api/agentive/connectors/catalog/google_drive/install",
        json={},
        headers=headers,
    )
    assert resp.status_code == 400, resp.text
    assert "Client ID" in resp.text


def test_stdio_mcp_requires_command(tmp_path):
    (tmp_path / "pkg.yaml").write_text(
        """
slug: pkg
display_name: Pkg
category: mcp_package
kind: mcp
icon: mcp
transport: stdio
auth:
  type: env
  fields: []
""",
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="command"):
        loader.load_catalog(catalog_dir=tmp_path)


def test_env_defaults_must_be_mapping(tmp_path):
    (tmp_path / "pkg.yaml").write_text(
        """
slug: pkg
display_name: Pkg
category: mcp_server
kind: mcp
icon: mcp
transport: streamable_http
url: https://mcp.example.test/mcp
auth:
  type: none
  fields: []
env_defaults: not-a-map
""",
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="env_defaults"):
        loader.load_catalog(catalog_dir=tmp_path)


@pytest.mark.asyncio
async def test_catalog_get_quickbooks_mcp_slug(authenticated_client):
    headers = await _workspace_scope_header(authenticated_client)
    resp = await authenticated_client.get(
        "/api/agentive/connectors/catalog/quickbooks_mcp",
        headers=headers,
    )
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["slug"] == "quickbooks_mcp"
    assert data["kind"] == "mcp"
    assert data["category"] == "mcp_package"
    assert data["transport"] == "stdio"
    assert data["command"] == "npx"
    assert data["auth"]["type"] == "oauth2"
    assert "env_defaults" not in data


@pytest.mark.asyncio
async def test_catalog_install_quickbooks_mcp_returns_oauth(
    authenticated_client, monkeypatch
):
    def fake_consent(user_id, **kwargs):
        return "https://appcenter.intuit.com/connect/oauth2?fake=1", "state-token"

    monkeypatch.setenv("QUICKBOOKS_CLIENT_ID", "env-client")
    monkeypatch.setattr(
        "app.services.connectors.quickbooks_oauth.build_consent_url",
        fake_consent,
    )
    headers = await _workspace_scope_header(authenticated_client)
    resp = await authenticated_client.post(
        "/api/agentive/connectors/catalog/quickbooks_mcp/install",
        json={"secrets": {"environment": "sandbox"}},
        headers=headers,
    )
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["action"] == "oauth"
    assert data["consent_url"].startswith("https://appcenter.intuit.com/")
    assert data["slug"] == "quickbooks_mcp"
    assert data["connector"]["kind"] == "mcp"
    assert data["connector"]["subclass_slug"] == "mcp"
    assert data["connector"]["auth_state"]["catalog_slug"] == "quickbooks_mcp"
    assert "refresh_token" not in (data["connector"]["auth_state"] or {})


@pytest.mark.asyncio
async def test_catalog_install_quickbooks_mcp_requires_client_id(
    authenticated_client, monkeypatch
):
    monkeypatch.delenv("QUICKBOOKS_CLIENT_ID", raising=False)
    monkeypatch.setattr(
        "app.services.connectors.quickbooks_oauth._env_client_id",
        lambda: "",
    )
    headers = await _workspace_scope_header(authenticated_client)
    resp = await authenticated_client.post(
        "/api/agentive/connectors/catalog/quickbooks_mcp/install",
        json={},
        headers=headers,
    )
    assert resp.status_code == 400, resp.text
    assert "Client ID" in resp.text


@pytest.mark.asyncio
async def test_quickbooks_callback_finishes_mcp_stdio_mount(
    authenticated_client, monkeypatch, tmp_path
):
    seen: dict = {}

    async def fake_complete(connector, **kwargs):
        seen["command"] = kwargs.get("command")
        seen["env"] = dict(kwargs.get("env") or {})
        connector.kind = "mcp"
        connector.subclass_slug = "mcp"
        connector.auth_state = {
            "transport": "stdio",
            "command": kwargs.get("command"),
            "args": list(kwargs.get("args") or []),
            "env": dict(kwargs.get("env") or {}),
            "catalog_slug": "quickbooks_mcp",
            "display_name": "QuickBooks MCP",
        }
        connector.health_status = "healthy"
        await connector.save()
        return connector

    monkeypatch.setattr(
        "app.agentive.connectors.mcp_mount.complete_stdio_mount",
        fake_complete,
    )
    monkeypatch.setattr(
        "app.connectors.stdio_env.TOKEN_STORE_ROOT",
        tmp_path / "mcp-stdio",
    )
    monkeypatch.setenv("QUICKBOOKS_CLIENT_ID", "mock-client-id")
    monkeypatch.setenv("QUICKBOOKS_CLIENT_SECRET", "mock-client-secret")
    monkeypatch.setenv("SECRET_KEY", "mock-secret-key")

    import httpx as _httpx

    from tests.domain_apps.fixtures.quickbooks_mock import (
        REALM_ID,
        TOKEN_EXCHANGE_RESPONSE,
        quickbooks_mock_transport,
    )

    transport = quickbooks_mock_transport()
    original_init = _httpx.AsyncClient.__init__

    def patched_init(self, *args, **kwargs):
        kwargs.setdefault("transport", transport)
        return original_init(self, *args, **kwargs)

    monkeypatch.setattr(_httpx.AsyncClient, "__init__", patched_init)

    headers = await _workspace_scope_header(authenticated_client)
    install = await authenticated_client.post(
        "/api/agentive/connectors/catalog/quickbooks_mcp/install",
        json={"secrets": {"client_id": "mock-client-id"}},
        headers=headers,
    )
    assert install.status_code == 200, install.text
    state = install.json()["state"]
    connector_id = install.json()["connector"]["id"]

    cb = await authenticated_client.post(
        "/api/agentive/connectors/quickbooks/callback",
        json={"code": "mock-code", "state": state, "realmId": REALM_ID},
        headers=headers,
    )
    assert cb.status_code == 200, cb.text
    body = cb.json()
    assert body["connected"] is True
    assert body["realm_id"] == REALM_ID
    assert TOKEN_EXCHANGE_RESPONSE["refresh_token"] not in str(body)
    assert seen["command"] == "npx"
    assert seen["env"]["QUICKBOOKS_REALM_ID"] == REALM_ID
    # Read-only by default; the spawn env carries the catalog's toggle.
    assert seen["env"]["QUICKBOOKS_DISABLE_WRITE"] == "true"
    files = list((tmp_path / "mcp-stdio").rglob("*.env"))
    assert len(files) == 1
    assert "QUICKBOOKS_REFRESH_TOKEN=" in files[0].read_text(encoding="utf-8")
    assert connector_id == body["connector_id"]


def test_quickbooks_mcp_ships_read_only():
    """Ship QuickBooks MCP read-only: create/update/delete disabled.

    Mounted MCP tools are invoked straight through ``mcp_proxy`` with no
    staging/bless step, while ADR-010 §6 requires human approval before
    anything leaves the workspace. Shipping the DISABLE_* toggles off meant a
    fresh install handed the resident unreviewed write access to a customer's
    accounting system. Both halves matter: ``env_defaults`` is what the spawn
    env carries, and the ``auth.fields`` defaults are what the install sheet
    pre-selects — flipping only one lets the other re-enable writes.
    """
    from app.connectors import catalog_loader as loader

    entry = loader.get_catalog_entry("quickbooks_mcp")

    env_defaults = entry.get("env_defaults") or {}
    for key in (
        "QUICKBOOKS_DISABLE_WRITE",
        "QUICKBOOKS_DISABLE_UPDATE",
        "QUICKBOOKS_DISABLE_DELETE",
    ):
        assert env_defaults.get(key) == "true", f"{key} must default to disabled"

    fields = {
        str(f.get("name")): f
        for f in ((entry.get("auth") or {}).get("fields") or [])
        if isinstance(f, dict)
    }
    for key in (
        "QUICKBOOKS_DISABLE_WRITE",
        "QUICKBOOKS_DISABLE_UPDATE",
        "QUICKBOOKS_DISABLE_DELETE",
    ):
        assert fields[key].get("default") == "true", f"{key} field default drifted"
