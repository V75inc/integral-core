"""Connector scoping: per-user vs shared connections.

- Row resolution: personal wins, shared fallback, none -> not_connected.
- Canonical keys: registered alongside row keys; survive until the last
  row of the slug unregisters (native + MCP).
- Install: label/mode accepted, shared is admin-only, duplicate shared
  refused, per-user is member-installable.
- Visibility: members see shared rows (LIST/GET/health), never чужі
  per-user rows; re-auth stays owner-only.
- Policy: members may tool.invoke + connector.read on shared rows only.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

import pytest

from app.agentive.connectors.connector_resolution import (
    ResolutionError,
    is_shared_row,
    resolve_connector_row,
    row_display_name,
    row_slug,
)

# ---------------------------------------------------------------------------
# Fakes
# ---------------------------------------------------------------------------


class FakeRow:
    def __init__(
        self,
        id: str,
        owner: str,
        subclass_slug: str = "",
        auth_state: Optional[Dict[str, Any]] = None,
        connection_mode: str = "per_user",
        workspace_id: str = "ws-1",
        label: str = "",
        health_status: str = "ok",
        last_error: Optional[str] = None,
    ):
        self.id = id
        self.owner = owner
        self.subclass_slug = subclass_slug
        self.auth_state = dict(auth_state or {})
        self.connection_mode = connection_mode
        self.workspace_id = workspace_id
        self.label = label
        self.health_status = health_status
        self.last_error = last_error
        self.updated_at = "2026-09-14T00:00:00+00:00"


def _patch_find(monkeypatch, rows: List[FakeRow]):
    from app.agentive import nodes as agentive_nodes

    async def fake_find(query):
        assert query.get("workspace_id") == "ws-1"
        return list(rows)

    monkeypatch.setattr(agentive_nodes.Connector, "find", fake_find)


# ---------------------------------------------------------------------------
# Resolution
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_personal_row_wins_over_shared(monkeypatch):
    _patch_find(
        monkeypatch,
        [
            FakeRow(
                "shared-1",
                "admin",
                "drive_native",
                {"catalog_slug": "drive_native"},
                "shared",
            ),
            FakeRow(
                "mine-1",
                "u1",
                "drive_native",
                {"catalog_slug": "drive_native"},
                "per_user",
            ),
        ],
    )
    row = await resolve_connector_row(
        workspace_id="ws-1", slug="drive_native", principal_id="u1"
    )
    assert row.id == "mine-1"


@pytest.mark.asyncio
async def test_shared_fallback_without_personal_row(monkeypatch):
    _patch_find(
        monkeypatch,
        [
            FakeRow(
                "mine-1",
                "u1",
                "drive_native",
                {"catalog_slug": "drive_native"},
                "per_user",
            ),
            FakeRow(
                "shared-1",
                "admin",
                "drive_native",
                {"catalog_slug": "drive_native"},
                "shared",
            ),
        ],
    )
    row = await resolve_connector_row(
        workspace_id="ws-1", slug="drive_native", principal_id="u2"
    )
    assert row.id == "shared-1"


@pytest.mark.asyncio
async def test_no_row_is_not_connected(monkeypatch):
    _patch_find(
        monkeypatch,
        [FakeRow("mine-1", "u1", "drive_native", {"catalog_slug": "drive_native"})],
    )
    with pytest.raises(ResolutionError) as exc:
        await resolve_connector_row(
            workspace_id="ws-1", slug="drive_native", principal_id="u2"
        )
    assert exc.value.error_code == "not_connected"
    assert "Settings" in exc.value.message


@pytest.mark.asyncio
async def test_teammate_row_never_resolves_for_others(monkeypatch):
    _patch_find(
        monkeypatch,
        [FakeRow("theirs-1", "u9", "gmail", {"catalog_slug": "gmail"})],
    )
    with pytest.raises(ResolutionError):
        await resolve_connector_row(
            workspace_id="ws-1", slug="gmail", principal_id="u1"
        )


@pytest.mark.asyncio
async def test_mcp_row_matches_by_catalog_slug(monkeypatch):
    _patch_find(
        monkeypatch,
        [FakeRow("mcp-1", "u1", "mcp", {"catalog_slug": "notion"})],
    )
    row = await resolve_connector_row(
        workspace_id="ws-1", slug="notion", principal_id="u1"
    )
    assert row.id == "mcp-1"


@pytest.mark.asyncio
async def test_resolve_rejects_blank_inputs():
    with pytest.raises(ResolutionError) as exc:
        await resolve_connector_row(workspace_id="", slug="gmail", principal_id="u1")
    assert exc.value.error_code == "misconfigured"


def test_row_helpers():
    assert is_shared_row(FakeRow("a", "u", connection_mode="shared")) is True
    assert is_shared_row(FakeRow("a", "u")) is False
    assert is_shared_row(FakeRow("a", "u", connection_mode="")) is False
    assert row_slug(FakeRow("a", "u", "mcp", {"catalog_slug": "notion"})) == "notion"
    assert row_slug(FakeRow("a", "u", "drive_native")) == "drive_native"
    assert (
        row_display_name(FakeRow("a", "u", "drive_native", {}, label="Team Drive"))
        == "Team Drive"
    )
    assert row_display_name(FakeRow("a", "u", "drive_native")) == "Google Drive"
    assert (
        row_display_name(FakeRow("a", "u", "mcp", {})) == "External tool server (MCP)"
    )
    assert row_display_name(FakeRow("a", "u", "weird_thing")) == "weird thing"


# ---------------------------------------------------------------------------
# Canonical keys — native refcounts
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _reset_native_registry():
    from app.agentive.connectors import native_tool_registry as reg
    from app.services.hooks.registry import _TOOLS

    reg.reset_canonical_rows_for_tests()
    _TOOLS.clear()
    yield
    reg.reset_canonical_rows_for_tests()
    _TOOLS.clear()


def test_native_canonical_keys_registered_alongside_row_keys():
    from app.agentive.connectors import native_tool_registry as reg
    from app.services.hooks.registry import get_workspace_tools

    reg.register_native_tools("ws-1", "conn-a", "drive_native")
    tools = get_workspace_tools("ws-1")
    assert "drive_native__search_files" in tools
    assert (
        tools["drive_native__search_files"]["_native_connector_slug"] == "drive_native"
    )
    assert "_native_connector_id" not in tools["drive_native__search_files"]
    assert "native__conn_a__search_files" in tools


def test_native_canonical_survives_until_last_row_leaves():
    from app.agentive.connectors import native_tool_registry as reg
    from app.services.hooks.registry import get_workspace_tools

    reg.register_native_tools("ws-1", "conn-a", "drive_native")
    reg.register_native_tools("ws-1", "conn-b", "drive_native")
    reg.unregister_native_tools("ws-1", "conn-a", "drive_native")
    assert "drive_native__search_files" in get_workspace_tools("ws-1")
    reg.unregister_native_tools("ws-1", "conn-b", "drive_native")
    assert "drive_native__search_files" not in get_workspace_tools("ws-1")
    # Row keys of the remaining/leaving rows behave independently.
    assert "native__conn_a__search_files" not in get_workspace_tools("ws-1")


def test_native_unregister_without_slug_keeps_canonical():
    from app.agentive.connectors import native_tool_registry as reg
    from app.services.hooks.registry import get_workspace_tools

    reg.register_native_tools("ws-1", "conn-a", "drive_native")
    reg.unregister_native_tools("ws-1", "conn-a")
    assert "native__conn_a__search_files" not in get_workspace_tools("ws-1")
    assert "drive_native__search_files" in get_workspace_tools("ws-1")


# ---------------------------------------------------------------------------
# Canonical keys — MCP builders + refcounts
# ---------------------------------------------------------------------------


def test_mcp_canonical_specs_and_keys():
    from app.agentive.connectors import mcp_mount as mount
    from app.agentive.connectors.mcp_client import RemoteTool

    tools = [
        RemoteTool(
            name="fetch",
            description="Fetch a page.",
            input_schema={"type": "object", "properties": {}},
        )
    ]
    specs = mount.canonical_mcp_specs_from_tools("notion", tools)
    assert len(specs) == 1
    assert specs[0]["key"] == "mcp__notion__fetch"
    assert specs[0]["_mcp_connector_slug"] == "notion"
    assert specs[0]["_mcp_remote_name"] == "fetch"
    assert "_mcp_connector_id" not in specs[0]
    assert mount.canonical_mcp_bundle_slug("notion") == "mcp:canonical:notion"


def test_mcp_canonical_specs_from_discovered_dicts():
    from app.agentive.connectors import mcp_mount as mount

    specs = mount.canonical_mcp_specs_from_discovered(
        "notion",
        [
            {
                "name": "fetch",
                "description": "d",
                "input_schema": {"type": "object"},
                "annotations": {"a": 1},
            },
            {"name": ""},
            "junk",
        ],
    )
    assert [s["key"] for s in specs] == ["mcp__notion__fetch"]
    assert specs[0]["_mcp_annotations"] == {"a": 1}


def test_mcp_canonical_refcounts():
    from app.agentive.connectors import mcp_mount as mount
    from app.agentive.connectors.mcp_client import RemoteTool
    from app.services.hooks.registry import get_workspace_tools

    mount.reset_canonical_mcp_rows_for_tests()
    tools = [RemoteTool(name="fetch", description="d", input_schema={})]
    mount.register_mcp_tools("ws-1", "c-a", tools, catalog_slug="notion")
    mount.register_mcp_tools("ws-1", "c-b", tools, catalog_slug="notion")
    mount.unregister_mcp_tools("ws-1", "c-a", "notion")
    assert "mcp__notion__fetch" in get_workspace_tools("ws-1")
    mount.unregister_mcp_tools("ws-1", "c-b", "notion")
    assert "mcp__notion__fetch" not in get_workspace_tools("ws-1")
    mount.reset_canonical_mcp_rows_for_tests()


def test_mcp_catalog_slug_for_connector():
    from app.agentive.connectors import mcp_mount as mount

    assert (
        mount.catalog_slug_for_connector(
            FakeRow("c", "u", "mcp", {"catalog_slug": "x"})
        )
        == "x"
    )
    assert mount.catalog_slug_for_connector(FakeRow("c", "u", "mcp", {})) == ""


# ---------------------------------------------------------------------------
# platform_configured
# ---------------------------------------------------------------------------


def test_platform_configured_google(monkeypatch):
    from app.agentive.api import connectors as api
    from app.config import settings

    entry = {
        "slug": "drive_native",
        "auth": {"fields": [{"name": "client_id"}, {"name": "client_secret"}]},
    }
    monkeypatch.setattr(settings, "GOOGLE_OAUTH_CLIENT_ID", "cid")
    monkeypatch.setattr(settings, "GOOGLE_OAUTH_CLIENT_SECRET", "")
    monkeypatch.delenv("GOOGLE_OAUTH_CLIENT_ID", raising=False)
    monkeypatch.delenv("GOOGLE_OAUTH_CLIENT_SECRET", raising=False)
    monkeypatch.delenv("GMAIL_OAUTH_CLIENT_ID", raising=False)
    monkeypatch.delenv("GMAIL_OAUTH_CLIENT_SECRET", raising=False)
    assert api._platform_configured_fields(entry) == ["client_id"]


def test_platform_configured_empty_without_env(monkeypatch):
    from app.agentive.api import connectors as api
    from app.config import settings

    entry = {
        "slug": "gmail",
        "auth": {"fields": [{"name": "client_id"}, {"name": "client_secret"}]},
    }
    monkeypatch.setattr(settings, "GMAIL_OAUTH_CLIENT_ID", "")
    monkeypatch.setattr(settings, "GMAIL_OAUTH_CLIENT_SECRET", "")
    monkeypatch.setattr(settings, "GOOGLE_OAUTH_CLIENT_ID", "")
    monkeypatch.setattr(settings, "GOOGLE_OAUTH_CLIENT_SECRET", "")
    monkeypatch.delenv("GMAIL_OAUTH_CLIENT_ID", raising=False)
    monkeypatch.delenv("GMAIL_OAUTH_CLIENT_SECRET", raising=False)
    monkeypatch.delenv("GOOGLE_OAUTH_CLIENT_ID", raising=False)
    monkeypatch.delenv("GOOGLE_OAUTH_CLIENT_SECRET", raising=False)
    assert api._platform_configured_fields(entry) == []


def test_gmail_shares_google_platform_keys(monkeypatch):
    from app.agentive.api import connectors as api
    from app.config import settings
    from app.services.connectors import gmail_oauth

    entry = {
        "slug": "gmail",
        "auth": {"fields": [{"name": "client_id"}, {"name": "client_secret"}]},
    }
    # GOOGLE_* alone lights up Gmail's Advanced Options + OAuth fallback.
    monkeypatch.setattr(settings, "GMAIL_OAUTH_CLIENT_ID", "")
    monkeypatch.setattr(settings, "GMAIL_OAUTH_CLIENT_SECRET", "")
    monkeypatch.setattr(settings, "GOOGLE_OAUTH_CLIENT_ID", "cid")
    monkeypatch.setattr(settings, "GOOGLE_OAUTH_CLIENT_SECRET", "csec")
    monkeypatch.delenv("GMAIL_OAUTH_CLIENT_ID", raising=False)
    monkeypatch.delenv("GMAIL_OAUTH_CLIENT_SECRET", raising=False)
    monkeypatch.delenv("GOOGLE_OAUTH_CLIENT_ID", raising=False)
    monkeypatch.delenv("GOOGLE_OAUTH_CLIENT_SECRET", raising=False)
    assert api._platform_configured_fields(entry) == ["client_id", "client_secret"]
    assert gmail_oauth._env_client_id() == "cid"
    assert gmail_oauth._env_client_secret() == "csec"


# ---------------------------------------------------------------------------
# API: install gating, visibility, policy (two users)
# ---------------------------------------------------------------------------


async def _workspace_id_for(authenticated_client) -> str:
    ws_resp = await authenticated_client.get("/api/workspaces")
    assert ws_resp.status_code == 200, ws_resp.text
    ws_payload = ws_resp.json()
    if isinstance(ws_payload, list):
        return ws_payload[0]["id"]
    return (ws_payload.get("workspaces") or ws_payload.get("items") or [{}])[0]["id"]


async def _member_client(test_user2, ws_id):
    """JWT client for user2 + IS_MEMBER_OF member edge on ws_id."""
    return await _user_client_with_role(test_user2, ws_id, "member")


async def _admin_client(test_user2, ws_id):
    """JWT client for user2 + IS_MEMBER_OF admin edge on ws_id."""
    return await _user_client_with_role(test_user2, ws_id, "admin")


async def _user_client_with_role(test_user2, ws_id, role: str):
    import jwt as pyjwt
    from httpx import ASGITransport, AsyncClient

    from app.config import settings
    from app.models.edges import IS_MEMBER_OF
    from app.models.nodes import Workspace

    user_id = getattr(test_user2, "user_id", None) or getattr(test_user2, "id", "")
    ws = await Workspace.get(ws_id)
    assert ws is not None
    await test_user2.connect(ws, edge=IS_MEMBER_OF, role=role)
    token = pyjwt.encode(
        {
            "user_id": user_id,
            "email": getattr(test_user2, "email", "") or "test2@example.com",
            "name": getattr(test_user2, "display_name", "") or "Test User 2",
            "roles": ["user"],
            "permissions": [],
        },
        settings.SECRET_KEY,
        algorithm=settings.ALGORITHM,
    )

    from tests.conftest import get_app

    client = AsyncClient(
        transport=ASGITransport(app=get_app()),
        base_url="http://test",
        timeout=10.0,
        headers={"Authorization": f"Bearer {token}"},
    )
    return client, user_id


def _ws_headers(ws_id: str) -> dict:
    return {"X-Integral-Scope": f"ws:{ws_id}"}


async def _org_ws(test_user, label: str) -> str:
    """Organization workspace with test_user as owner (for shared tests)."""
    from app.models.edges import IS_MEMBER_OF
    from app.models.nodes import Workspace
    from app.utils.time import utc_now_iso

    ws = await Workspace.create(
        kind="organization", name=f"Org {label}", name_fold=f"org {label}"
    )
    await test_user.connect(ws, edge=IS_MEMBER_OF, role="owner", added_at=utc_now_iso())
    return ws.id


@pytest.mark.asyncio
async def test_install_label_and_mode_stored(authenticated_client):
    ws_id = await _workspace_id_for(authenticated_client)
    resp = await authenticated_client.post(
        "/api/agentive/connectors/catalog/github_issues/install",
        json={
            "secrets": {"owner": "acme", "repo": "w"},
            "label": "Team Issues",
            "connection_mode": "per_user",
        },
        headers=_ws_headers(ws_id),
    )
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["connector"]["label"] == "Team Issues"
    assert data["connector"]["connection_mode"] == "per_user"


@pytest.mark.asyncio
async def test_install_shared_and_duplicate_refused(authenticated_client, test_user):
    ws_id = await _org_ws(test_user, "dup")
    first = await authenticated_client.post(
        "/api/agentive/connectors/catalog/github_issues/install",
        json={
            "secrets": {"owner": "acme", "repo": "w"},
            "label": "Shared Issues",
            "connection_mode": "shared",
        },
        headers=_ws_headers(ws_id),
    )
    assert first.status_code == 200, first.text
    assert first.json()["connector"]["connection_mode"] == "shared"
    second = await authenticated_client.post(
        "/api/agentive/connectors/catalog/github_issues/install",
        json={"secrets": {"owner": "acme", "repo": "w2"}, "connection_mode": "shared"},
        headers=_ws_headers(ws_id),
    )
    assert second.status_code == 409, second.text
    assert "already exists" in second.text


@pytest.mark.asyncio
async def test_install_invalid_mode_rejected(authenticated_client):
    ws_id = await _workspace_id_for(authenticated_client)
    resp = await authenticated_client.post(
        "/api/agentive/connectors/catalog/github_issues/install",
        json={"secrets": {"owner": "a", "repo": "b"}, "connection_mode": "everyone"},
        headers=_ws_headers(ws_id),
    )
    assert resp.status_code == 400, resp.text


@pytest.mark.asyncio
async def test_shared_install_refused_in_personal_workspace(
    authenticated_client, test_user
):
    from app.services.personal_workspace import ensure_personal_workspace

    personal = await ensure_personal_workspace(test_user)
    assert (getattr(personal, "kind", "") or "") == "personal"
    resp = await authenticated_client.post(
        "/api/agentive/connectors/catalog/github_issues/install",
        json={"secrets": {"owner": "a", "repo": "b"}, "connection_mode": "shared"},
        headers=_ws_headers(personal.id),
    )
    assert resp.status_code == 400, resp.text
    assert "per-user" in resp.text


@pytest.mark.asyncio
async def test_member_shared_install_forbidden_per_user_allowed(
    authenticated_client, test_user, test_user2
):
    ws_id = await _org_ws(test_user, "member-install")
    member, _ = await _member_client(test_user2, ws_id)
    try:
        denied = await member.post(
            "/api/agentive/connectors/catalog/github_issues/install",
            json={"secrets": {"owner": "a", "repo": "b"}, "connection_mode": "shared"},
            headers=_ws_headers(ws_id),
        )
        assert denied.status_code == 403, denied.text
        ok = await member.post(
            "/api/agentive/connectors/catalog/github_issues/install",
            json={"secrets": {"owner": "me", "repo": "mine"}},
            headers=_ws_headers(ws_id),
        )
        assert ok.status_code == 200, ok.text
        assert ok.json()["connector"]["connection_mode"] == "per_user"
    finally:
        await member.aclose()


@pytest.mark.asyncio
async def test_member_sees_shared_not_others_per_user(
    authenticated_client, test_user, test_user2
):
    ws_id = await _org_ws(test_user, "visibility")
    admin_headers = _ws_headers(ws_id)
    mine = await authenticated_client.post(
        "/api/agentive/connectors/catalog/github_issues/install",
        json={"secrets": {"owner": "a", "repo": "mine"}},
        headers=admin_headers,
    )
    assert mine.status_code == 200, mine.text
    mine_id = mine.json()["connector"]["id"]
    shared = await authenticated_client.post(
        "/api/agentive/connectors/catalog/github_issues/install",
        json={
            "secrets": {"owner": "a", "repo": "shared"},
            "connection_mode": "shared",
            "label": "Team Board",
        },
        headers=admin_headers,
    )
    assert shared.status_code == 200, shared.text
    shared_id = shared.json()["connector"]["id"]

    member, _ = await _member_client(test_user2, ws_id)
    try:
        listed = await member.get(
            "/api/agentive/connectors", headers=_ws_headers(ws_id)
        )
        assert listed.status_code == 200, listed.text
        ids = {c["id"] for c in listed.json()["connectors"]}
        assert shared_id in ids
        assert mine_id not in ids

        got_shared = await member.get(
            f"/api/agentive/connectors/{shared_id}", headers=_ws_headers(ws_id)
        )
        assert got_shared.status_code == 200, got_shared.text
        assert got_shared.json()["connection_mode"] == "shared"
        assert got_shared.json()["label"] == "Team Board"

        got_mine = await member.get(
            f"/api/agentive/connectors/{mine_id}", headers=_ws_headers(ws_id)
        )
        assert got_mine.status_code == 404, got_mine.text

        health = await member.get(
            f"/api/agentive/connectors/{shared_id}/health",
            headers=_ws_headers(ws_id),
        )
        assert health.status_code == 200, health.text
    finally:
        await member.aclose()


@pytest.mark.asyncio
async def test_owner_can_rename_label(authenticated_client):
    ws_id = await _workspace_id_for(authenticated_client)
    created = await authenticated_client.post(
        "/api/agentive/connectors/catalog/github_issues/install",
        json={"secrets": {"owner": "a", "repo": "r"}, "label": "Old"},
        headers=_ws_headers(ws_id),
    )
    cid = created.json()["connector"]["id"]
    patched = await authenticated_client.patch(
        f"/api/agentive/connectors/{cid}",
        json={"label": "New"},
        headers=_ws_headers(ws_id),
    )
    assert patched.status_code == 200, patched.text
    assert patched.json()["label"] == "New"
    blank = await authenticated_client.patch(
        f"/api/agentive/connectors/{cid}",
        json={"label": "  "},
        headers=_ws_headers(ws_id),
    )
    assert blank.status_code == 400, blank.text


@pytest.mark.asyncio
async def test_policy_member_may_invoke_shared_only(
    authenticated_client, test_user, test_user2
):
    from app.schemas.policy import Resource, Subject
    from app.services.policy_engine import evaluate as policy_evaluate

    ws_id = await _org_ws(test_user, "policy")
    admin_headers = _ws_headers(ws_id)
    shared = await authenticated_client.post(
        "/api/agentive/connectors/catalog/github_issues/install",
        json={"secrets": {"owner": "a", "repo": "s"}, "connection_mode": "shared"},
        headers=admin_headers,
    )
    shared_id = shared.json()["connector"]["id"]
    mine = await authenticated_client.post(
        "/api/agentive/connectors/catalog/github_issues/install",
        json={"secrets": {"owner": "a", "repo": "m"}},
        headers=admin_headers,
    )
    mine_id = mine.json()["connector"]["id"]

    member, member_id = await _member_client(test_user2, ws_id)
    try:
        allowed = await policy_evaluate(
            subject=Subject(kind="human", id=member_id),
            action="tool.invoke",
            resource=Resource(
                kind="connector", id=shared_id, scope=f"connector:{shared_id}"
            ),
        )
        assert allowed.allowed is True
        denied = await policy_evaluate(
            subject=Subject(kind="human", id=member_id),
            action="tool.invoke",
            resource=Resource(
                kind="connector", id=mine_id, scope=f"connector:{mine_id}"
            ),
        )
        assert denied.allowed is False
        ghost = await policy_evaluate(
            subject=Subject(kind="human", id="ghost-user"),
            action="tool.invoke",
            resource=Resource(
                kind="connector", id=shared_id, scope=f"connector:{shared_id}"
            ),
        )
        assert ghost.allowed is False
    finally:
        await member.aclose()


@pytest.mark.asyncio
async def test_tools_endpoint_lists_canonical_then_row(authenticated_client):
    from app.agentive.connectors.native_tool_registry import register_native_tools

    ws_id = await _workspace_id_for(authenticated_client)
    installed = await authenticated_client.post(
        "/api/agentive/connectors/catalog/drive_native/install",
        json={"secrets": {"client_id": "cid", "client_secret": "csec"}},
        headers=_ws_headers(ws_id),
    )
    assert installed.status_code == 200, installed.text
    conn_id = installed.json()["connector"]["id"]
    # Simulate post-OAuth registration (callback path registers these).
    register_native_tools(ws_id, conn_id, "drive_native")

    resp = await authenticated_client.get(
        f"/api/agentive/connectors/{conn_id}/tools", headers=_ws_headers(ws_id)
    )
    assert resp.status_code == 200, resp.text
    tools = resp.json()["tools"]
    # Canonical keys only — legacy per-row aliases (native__<rowid>__) stay
    # dispatchable but are hidden so one stable key names each tool.
    assert len(tools) == 12
    assert tools[0]["scope"] == "canonical"
    assert tools[0]["key"] == "drive_native__create_file"
    assert all(t["scope"] == "canonical" for t in tools)
    assert not any(t["key"].startswith("native__") for t in tools)
    by_name = {t["name"]: t for t in tools}
    assert by_name["search_files"]["write"] is False
    assert by_name["create_file"]["write"] is True
    assert all(t["input_schema"].get("type") == "object" for t in tools)


@pytest.mark.asyncio
async def test_tools_endpoint_member_shared_only(
    authenticated_client, test_user, test_user2
):
    from app.agentive.connectors.native_tool_registry import register_native_tools

    ws_id = await _org_ws(test_user, "tools")
    admin_headers = _ws_headers(ws_id)
    shared = await authenticated_client.post(
        "/api/agentive/connectors/catalog/drive_native/install",
        json={
            "secrets": {"client_id": "cid", "client_secret": "csec"},
            "connection_mode": "shared",
            "label": "Team Drive",
        },
        headers=admin_headers,
    )
    shared_id = shared.json()["connector"]["id"]
    mine = await authenticated_client.post(
        "/api/agentive/connectors/catalog/drive_native/install",
        json={"secrets": {"client_id": "cid", "client_secret": "csec"}},
        headers=admin_headers,
    )
    mine_id = mine.json()["connector"]["id"]
    register_native_tools(ws_id, shared_id, "drive_native")

    member, _ = await _member_client(test_user2, ws_id)
    try:
        ok = await member.get(
            f"/api/agentive/connectors/{shared_id}/tools",
            headers=_ws_headers(ws_id),
        )
        assert ok.status_code == 200, ok.text
        # Static fallback: shared row registered above, so canonical keys show.
        assert any(t["key"] == "drive_native__search_files" for t in ok.json()["tools"])
        denied = await member.get(
            f"/api/agentive/connectors/{mine_id}/tools",
            headers=_ws_headers(ws_id),
        )
        assert denied.status_code == 404, denied.text
        missing = await member.get(
            f"/api/agentive/connectors/{mine_id}/tools",
            headers=_ws_headers(ws_id),
        )
        assert missing.status_code == 404, missing.text
    finally:
        await member.aclose()


@pytest.mark.asyncio
async def test_admin_manages_shared_row(authenticated_client, test_user, test_user2):
    ws_id = await _org_ws(test_user, "admin-manage")
    admin_headers = _ws_headers(ws_id)
    created = await authenticated_client.post(
        "/api/agentive/connectors/catalog/github_issues/install",
        json={
            "secrets": {"owner": "a", "repo": "s"},
            "connection_mode": "shared",
            "label": "Team",
        },
        headers=admin_headers,
    )
    cid = created.json()["connector"]["id"]

    admin, _ = await _admin_client(test_user2, ws_id)
    try:
        renamed = await admin.patch(
            f"/api/agentive/connectors/{cid}",
            json={"label": "Team Renamed"},
            headers=_ws_headers(ws_id),
        )
        assert renamed.status_code == 200, renamed.text
        assert renamed.json()["label"] == "Team Renamed"

        deleted = await admin.delete(
            f"/api/agentive/connectors/{cid}", headers=_ws_headers(ws_id)
        )
        assert deleted.status_code == 204, deleted.text
    finally:
        await admin.aclose()


@pytest.mark.asyncio
async def test_member_cannot_delete_or_patch_shared(
    authenticated_client, test_user, test_user2
):
    ws_id = await _org_ws(test_user, "member-blocked")
    created = await authenticated_client.post(
        "/api/agentive/connectors/catalog/github_issues/install",
        json={"secrets": {"owner": "a", "repo": "s"}, "connection_mode": "shared"},
        headers=_ws_headers(ws_id),
    )
    cid = created.json()["connector"]["id"]

    member, _ = await _member_client(test_user2, ws_id)
    try:
        assert (
            await member.delete(
                f"/api/agentive/connectors/{cid}", headers=_ws_headers(ws_id)
            )
        ).status_code == 404
        assert (
            await member.patch(
                f"/api/agentive/connectors/{cid}",
                json={"label": "Hijack"},
                headers=_ws_headers(ws_id),
            )
        ).status_code == 404
    finally:
        await member.aclose()


@pytest.mark.asyncio
async def test_policy_admin_sync_and_update_on_shared(
    authenticated_client, test_user, test_user2
):
    from app.schemas.policy import Resource, Subject
    from app.services.policy_engine import evaluate as policy_evaluate

    async def _shared_row(label: str):
        ws = await _org_ws(test_user, label)
        created = await authenticated_client.post(
            "/api/agentive/connectors/catalog/github_issues/install",
            json={
                "secrets": {"owner": "a", "repo": "s"},
                "connection_mode": "shared",
            },
            headers=_ws_headers(ws),
        )
        assert created.status_code == 200, created.text
        return ws, created.json()["connector"]["id"]

    # Member edge only: invoke/read allowed, management denied.
    ws_member, shared_member = await _shared_row("policy-member")
    member, member_id = await _member_client(test_user2, ws_member)
    try:
        for action in ("tool.invoke", "connector.read"):
            allowed = await policy_evaluate(
                subject=Subject(kind="human", id=member_id),
                action=action,
                resource=Resource(
                    kind="connector",
                    id=shared_member,
                    scope=f"connector:{shared_member}",
                ),
            )
            assert allowed.allowed is True, action
        for action in ("connector.update", "connector.delete", "connector.sync"):
            denied = await policy_evaluate(
                subject=Subject(kind="human", id=member_id),
                action=action,
                resource=Resource(
                    kind="connector",
                    id=shared_member,
                    scope=f"connector:{shared_member}",
                ),
            )
            assert denied.allowed is False, action
        ghost = await policy_evaluate(
            subject=Subject(kind="human", id="ghost-user"),
            action="tool.invoke",
            resource=Resource(
                kind="connector", id=shared_member, scope=f"connector:{shared_member}"
            ),
        )
        assert ghost.allowed is False
    finally:
        await member.aclose()

    # Admin edge only (separate workspace — one edge per user per workspace).
    ws_admin, shared_admin = await _shared_row("policy-admin")
    admin, admin_id = await _admin_client(test_user2, ws_admin)
    try:
        for action in (
            "tool.invoke",
            "connector.read",
            "connector.update",
            "connector.delete",
            "connector.sync",
        ):
            allowed = await policy_evaluate(
                subject=Subject(kind="human", id=admin_id),
                action=action,
                resource=Resource(
                    kind="connector", id=shared_admin, scope=f"connector:{shared_admin}"
                ),
            )
            assert allowed.allowed is True, action
    finally:
        await admin.aclose()
