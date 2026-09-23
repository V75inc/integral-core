"""Native Google connectors (drive_native / sheets_native) unit suite.

Network-free: ``httpx.MockTransport`` stands in for Google, and a stub
connector node stands in for the Connector Node. Covers:

- google_oauth: state sign/verify roundtrip, provider binding, scopes,
  ensure_fresh_token rotation / refresh preservation / reauth flagging.
- drive_native: tool specs + write flags, search/read/export/create/update
  shapes, error mapping, sync_pull + to_entry.
- sheets_native: tool specs, get/update/append/clear/batch shapes, value
  caps, sync_pull + to_entry.
- native_tool_registry: spec keys, register/unregister roundtrip.
- dispatch/staging: native_tool_call executor registered, autonomy key
  narrowing, proxy misconfigured error.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

import httpx
import pytest

from app.agentive.connectors import drive_native, sheets_native
from app.agentive.connectors.drive_native import NativeToolError
from app.agentive.connectors.native_tool_registry import (
    native_bundle_slug,
    register_native_tools,
    specs_for_slug,
    tool_key_for,
    unregister_native_tools,
)
from app.services.connectors import google_oauth

# ---------------------------------------------------------------------------
# Fixtures / fakes
# ---------------------------------------------------------------------------


class FakeConnector:
    """Duck-typed Connector Node (auth_state dict + async save)."""

    def __init__(self, auth_state: Optional[Dict[str, Any]] = None):
        self.id = "conn-test-1"
        self.auth_state = dict(auth_state or {})
        self.sync_cursor: Optional[str] = None
        self.saved = 0

    async def save(self) -> None:
        self.saved += 1


def _fresh_auth(**overrides: Any) -> Dict[str, Any]:
    base: Dict[str, Any] = {
        "access_token": "ya29.fresh",
        "refresh_token": "1//refresh",
        "access_token_expires_at": (
            datetime.now(timezone.utc) + timedelta(hours=1)
        ).isoformat(),
    }
    base.update(overrides)
    return base


def _mock_client(handler) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


# ---------------------------------------------------------------------------
# google_oauth
# ---------------------------------------------------------------------------


def test_state_roundtrip_binds_provider(monkeypatch):
    monkeypatch.setattr(google_oauth.settings, "SECRET_KEY", "test-secret")
    url, state = google_oauth.build_consent_url(
        "user-1", "drive_native", client_id="cid"
    )
    assert url.startswith("https://accounts.google.com/")
    assert google_oauth.verify_state(state, "user-1", "drive_native") is True
    assert google_oauth.verify_state(state, "user-1", "sheets_native") is False
    assert google_oauth.verify_state(state, "user-2", "drive_native") is False
    assert google_oauth.provider_from_state(state) == "drive_native"
    assert google_oauth.connector_id_from_state(state) is None


def test_state_carries_connector_id(monkeypatch):
    monkeypatch.setattr(google_oauth.settings, "SECRET_KEY", "test-secret")
    _, state = google_oauth.build_consent_url(
        "user-1", "sheets_native", client_id="cid", connector_id="conn-9"
    )
    assert google_oauth.connector_id_from_state(state) == "conn-9"


def test_scopes_cover_read_and_write():
    drive = google_oauth.scopes_for_provider("drive_native")
    assert "https://www.googleapis.com/auth/drive" in drive
    sheets = google_oauth.scopes_for_provider("sheets_native")
    assert "https://www.googleapis.com/auth/spreadsheets" in sheets
    assert "https://www.googleapis.com/auth/drive.readonly" in sheets
    with pytest.raises(KeyError):
        google_oauth.scopes_for_provider("nope")


@pytest.mark.asyncio
async def test_ensure_fresh_token_skips_when_valid():
    conn = FakeConnector(_fresh_auth())
    assert await google_oauth.ensure_fresh_token(conn) is True
    assert conn.saved == 0


@pytest.mark.asyncio
async def test_ensure_fresh_token_rotates_and_preserves_refresh(monkeypatch):
    monkeypatch.setenv("GOOGLE_OAUTH_CLIENT_ID", "cid")
    monkeypatch.setenv("GOOGLE_OAUTH_CLIENT_SECRET", "csec")

    async def handler(request: httpx.Request) -> httpx.Response:
        assert "oauth2.googleapis.com/token" in str(request.url)
        # No refresh_token in response — must be preserved.
        return httpx.Response(
            200, json={"access_token": "ya29.new", "expires_in": 3600}
        )

    conn = FakeConnector(
        _fresh_auth(
            access_token_expires_at=(
                datetime.now(timezone.utc) - timedelta(minutes=1)
            ).isoformat()
        )
    )
    ok = await google_oauth.ensure_fresh_token(conn, http_client=_mock_client(handler))
    assert ok is True
    assert conn.auth_state["access_token"] == "ya29.new"
    assert conn.auth_state["refresh_token"] == "1//refresh"
    assert "reauth_required" not in conn.auth_state
    assert conn.saved == 1


@pytest.mark.asyncio
async def test_ensure_fresh_token_flags_reauth_on_invalid_grant(monkeypatch):
    monkeypatch.setenv("GOOGLE_OAUTH_CLIENT_ID", "cid")
    monkeypatch.setenv("GOOGLE_OAUTH_CLIENT_SECRET", "csec")

    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(400, json={"error": "invalid_grant"})

    conn = FakeConnector(
        _fresh_auth(
            access_token_expires_at=(
                datetime.now(timezone.utc) - timedelta(minutes=1)
            ).isoformat()
        )
    )
    assert (
        await google_oauth.ensure_fresh_token(conn, http_client=_mock_client(handler))
        is False
    )
    assert conn.auth_state["reauth_required"] is True


@pytest.mark.asyncio
async def test_ensure_fresh_token_flags_reauth_without_refresh_token():
    conn = FakeConnector({"access_token": "stale"})
    assert await google_oauth.ensure_fresh_token(conn) is False
    assert conn.auth_state["reauth_required"] is True


# ---------------------------------------------------------------------------
# drive_native tools
# ---------------------------------------------------------------------------


def test_drive_tool_specs_write_flags():
    by_name = {t["name"]: t for t in drive_native.TOOL_SPECS}
    assert len(by_name) == 12
    for read_tool in (
        "search_files",
        "list_recent_files",
        "get_file_metadata",
        "read_file_content",
        "download_file_content",
        "get_file_permissions",
    ):
        assert by_name[read_tool]["write"] is False
    for write_tool in (
        "create_file",
        "update_file",
        "trash_file",
        "delete_file_permanently",
        "share_file",
        "delete_permission",
    ):
        assert by_name[write_tool]["write"] is True


@pytest.mark.asyncio
async def test_drive_search_files_shapes_query():
    seen: Dict[str, Any] = {}

    async def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        assert request.headers["Authorization"] == "Bearer tok"
        return httpx.Response(
            200,
            json={"files": [{"id": "f1", "name": "Report", "mimeType": "text/plain"}]},
        )

    out = await drive_native.call_tool(
        "search_files",
        {"query": "Report", "page_size": 5},
        access_token="tok",
        http_client=_mock_client(handler),
    )
    assert out["files"][0]["id"] == "f1"
    assert (
        "trashed%3Dfalse" in seen["url"].replace("=", "%3D") or "trashed" in seen["url"]
    )


@pytest.mark.asyncio
async def test_drive_read_exports_google_doc():
    calls: List[str] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        calls.append(url)
        if "/export" in url:
            return httpx.Response(200, content=b"Hello exported")
        return httpx.Response(
            200,
            json={
                "id": "doc1",
                "name": "Doc",
                "mimeType": "application/vnd.google-apps.document",
            },
        )

    out = await drive_native.call_tool(
        "read_file_content",
        {"file_id": "doc1"},
        access_token="tok",
        http_client=_mock_client(handler),
    )
    assert out["content"] == "Hello exported"
    assert out["exported_as"] == "text/plain"
    assert out["truncated"] is False
    assert any("/export" in c for c in calls)


@pytest.mark.asyncio
async def test_drive_read_truncates_large_files():
    async def handler(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        if "alt=media" in url or "alt" in url:
            return httpx.Response(
                200, content=b"x" * (drive_native._READ_CHAR_LIMIT + 10)
            )
        return httpx.Response(
            200, json={"id": "f9", "name": "big", "mimeType": "text/plain"}
        )

    out = await drive_native.call_tool(
        "read_file_content",
        {"file_id": "f9"},
        access_token="tok",
        http_client=_mock_client(handler),
    )
    assert out["truncated"] is True
    assert len(out["content"]) == drive_native._READ_CHAR_LIMIT


@pytest.mark.asyncio
async def test_drive_create_file_multipart():
    seen: Dict[str, Any] = {}

    async def handler(request: httpx.Request) -> httpx.Response:
        seen["content_type"] = request.headers.get("content-type", "")
        seen["body"] = request.content
        assert "uploadType=multipart" in str(request.url)
        return httpx.Response(200, json={"id": "new1", "name": "note.txt"})

    out = await drive_native.call_tool(
        "create_file",
        {"name": "note.txt", "content": "hi"},
        access_token="tok",
        http_client=_mock_client(handler),
    )
    assert out["id"] == "new1"
    # Drive rejects multipart/form-data with 400 — must be multipart/related.
    assert seen["content_type"].startswith("multipart/related")
    body = seen["body"].decode("utf-8")
    assert '"name": "note.txt"' in body
    assert "Content-Type: application/json" in body
    assert "Content-Type: text/plain" in body
    assert "\r\nhi\r\n" in body


@pytest.mark.asyncio
async def test_drive_update_file_content_multipart():
    seen: Dict[str, Any] = {}

    async def handler(request: httpx.Request) -> httpx.Response:
        seen["method"] = request.method
        seen["content_type"] = request.headers.get("content-type", "")
        seen["body"] = request.content
        return httpx.Response(200, json={"id": "f1", "name": "renamed.txt"})

    out = await drive_native.call_tool(
        "update_file",
        {"file_id": "f1", "name": "renamed.txt", "content": "new body"},
        access_token="tok",
        http_client=_mock_client(handler),
    )
    assert seen["method"] == "PATCH"
    assert seen["content_type"].startswith("multipart/related")
    body = seen["body"].decode("utf-8")
    assert '"name": "renamed.txt"' in body
    assert "\r\nnew body\r\n" in body
    assert out["id"] == "f1"


@pytest.mark.asyncio
async def test_drive_errors_map_to_codes():
    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(404, json={"error": {"message": "File not found: x"}})

    with pytest.raises(NativeToolError) as exc:
        await drive_native.call_tool(
            "get_file_metadata",
            {"file_id": "x"},
            access_token="tok",
            http_client=_mock_client(handler),
        )
    assert exc.value.error_code == "not_found"


@pytest.mark.asyncio
async def test_drive_unknown_tool():
    with pytest.raises(NativeToolError) as exc:
        await drive_native.call_tool("levitate", {}, access_token="tok")
    assert exc.value.error_code == "unknown_tool"


@pytest.mark.asyncio
async def test_drive_share_validates_role():
    with pytest.raises(NativeToolError) as exc:
        await drive_native.call_tool(
            "share_file",
            {"file_id": "f", "role": "superuser", "type": "user"},
            access_token="tok",
        )
    assert exc.value.error_code == "bad_args"


# ---------------------------------------------------------------------------
# drive_native sync mirror
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_drive_sync_pull_yields_records():
    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "files": [
                    {
                        "id": "f1",
                        "name": "A",
                        "mimeType": "text/plain",
                        "modifiedTime": "2026-09-01T00:00:00Z",
                    }
                ]
            },
        )

    sub = drive_native.DriveNativeConnector()
    sub.test_http_client = _mock_client(handler)
    conn = FakeConnector(_fresh_auth())
    records = [r async for r in sub.sync_pull(connector=conn)]
    assert [r.external_id for r in records] == ["f1"]
    entry = sub.to_entry(records[0])
    assert entry.title == "A"
    assert entry.entry_type_key == "drive_file"
    assert entry.custom_fields["file_id"] == "f1"


@pytest.mark.asyncio
async def test_drive_sync_aborts_on_reauth():
    sub = drive_native.DriveNativeConnector()
    conn = FakeConnector({"access_token": "stale"})
    records = [r async for r in sub.sync_pull(connector=conn)]
    assert records == []
    assert conn.auth_state["reauth_required"] is True


# ---------------------------------------------------------------------------
# sheets_native tools
# ---------------------------------------------------------------------------


def test_sheets_tool_specs_write_flags():
    by_name = {t["name"]: t for t in sheets_native.TOOL_SPECS}
    assert len(by_name) == 8
    assert by_name["get_values"]["write"] is False
    assert by_name["get_spreadsheet"]["write"] is False
    assert by_name["list_spreadsheets"]["write"] is False
    assert by_name["update_values"]["write"] is True
    assert by_name["batch_update"]["write"] is True


@pytest.mark.asyncio
async def test_sheets_get_values_caps_rows():
    async def handler(request: httpx.Request) -> httpx.Response:
        assert "/values/" in str(request.url)
        return httpx.Response(
            200,
            json={
                "range": "S!A:A",
                "values": [
                    [str(i)] for i in range(sheets_native._VALUES_ROW_LIMIT + 5)
                ],
            },
        )

    out = await sheets_native.call_tool(
        "get_values",
        {"spreadsheet_id": "s1", "range": "S!A:A"},
        access_token="tok",
        http_client=_mock_client(handler),
    )
    assert out["truncated"] is True
    assert len(out["values"]) == sheets_native._VALUES_ROW_LIMIT


@pytest.mark.asyncio
async def test_sheets_update_values_put_shape():
    seen: Dict[str, Any] = {}

    async def handler(request: httpx.Request) -> httpx.Response:
        seen["method"] = request.method
        assert "valueInputOption=USER_ENTERED" in str(request.url)
        body = json.loads(request.content.decode())
        assert body["values"] == [["a", "b"]]
        return httpx.Response(200, json={"updatedCells": 2})

    out = await sheets_native.call_tool(
        "update_values",
        {"spreadsheet_id": "s1", "range": "S!A1:B1", "values": [["a", "b"]]},
        access_token="tok",
        http_client=_mock_client(handler),
    )
    assert seen["method"] == "PUT"
    assert out["updatedCells"] == 2


@pytest.mark.asyncio
async def test_sheets_batch_update_rejects_oversize():
    with pytest.raises(NativeToolError) as exc:
        await sheets_native.call_tool(
            "batch_update",
            {"spreadsheet_id": "s1", "requests": [{}] * 101},
            access_token="tok",
        )
    assert exc.value.error_code == "bad_args"


@pytest.mark.asyncio
async def test_sheets_create_spreadsheet():
    async def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "POST"
        body = json.loads(request.content.decode())
        assert body["properties"]["title"] == "Q3"
        return httpx.Response(
            200, json={"spreadsheetId": "new-s", "spreadsheetUrl": "https://x"}
        )

    out = await sheets_native.call_tool(
        "create_spreadsheet",
        {"title": "Q3", "sheet_titles": ["Data"]},
        access_token="tok",
        http_client=_mock_client(handler),
    )
    assert out["spreadsheetId"] == "new-s"


@pytest.mark.asyncio
async def test_sheets_sync_pull_yields_spreadsheets():
    async def handler(request: httpx.Request) -> httpx.Response:
        assert "mimeType" in str(request.url)
        return httpx.Response(
            200,
            json={
                "files": [
                    {
                        "id": "s1",
                        "name": "Budget",
                        "modifiedTime": "2026-09-02T00:00:00Z",
                    }
                ]
            },
        )

    sub = sheets_native.SheetsNativeConnector()
    sub.test_http_client = _mock_client(handler)
    conn = FakeConnector(_fresh_auth())
    records = [r async for r in sub.sync_pull(connector=conn)]
    assert [r.external_id for r in records] == ["s1"]
    entry = sub.to_entry(records[0])
    assert entry.entry_type_key == "spreadsheet"
    assert entry.custom_fields["spreadsheet_id"] == "s1"


# ---------------------------------------------------------------------------
# registry / dispatch / staging integration
# ---------------------------------------------------------------------------


def test_native_specs_carry_routing_metadata():
    specs = specs_for_slug("conn-abc-123", "drive_native")
    assert len(specs) == len(drive_native.TOOL_SPECS)
    keys = {s["key"] for s in specs}
    assert len(keys) == len(specs)
    assert all(k.startswith("native__") for k in keys)
    first = specs[0]
    assert first["handler_ref"] == "app.agentive.connectors.native_google_proxy:invoke"
    assert first["_native_connector_id"] == "conn-abc-123"
    assert first["_native_tool_name"] == drive_native.TOOL_SPECS[0]["name"]
    writes = {s["_native_tool_name"] for s in specs if s["_native_write"]}
    assert writes == {t["name"] for t in drive_native.TOOL_SPECS if t["write"]}
    with pytest.raises(ValueError):
        specs_for_slug("c", "not_a_connector")
    assert tool_key_for("...", "search_files").startswith("native__")


def test_register_unregister_roundtrip():
    from app.services.hooks.registry import get_workspace_tools

    ws = "ws-native-test"
    count = register_native_tools(ws, "conn-1", "sheets_native")
    assert count == len(sheets_native.TOOL_SPECS)
    tools = get_workspace_tools(ws)
    assert any(k.startswith("native__") for k in tools)
    assert native_bundle_slug("conn-1") == "native:conn-1"
    unregister_native_tools(ws, "conn-1", "sheets_native")
    assert not any(k.startswith("native__") for k in get_workspace_tools(ws))
    assert "sheets_native__get_values" not in get_workspace_tools(ws)


def test_native_tool_call_executor_registered():
    from app.agentive import staging_executors
    from app.agentive.staging import (
        _RESULT_BEARING_KINDS,
        _TARGET_SCOPED_AUTONOMY_KINDS,
        autonomy_key_for,
    )

    assert staging_executors.supports("native_tool_call") is True
    assert "native_tool_call" in _TARGET_SCOPED_AUTONOMY_KINDS
    assert "native_tool_call" in _RESULT_BEARING_KINDS
    key = autonomy_key_for(
        "native_tool_call",
        {"connector_id": "c1", "remote_name": "update_values"},
    )
    assert key == "native_tool_call:c1:update_values"


@pytest.mark.asyncio
async def test_native_proxy_rejects_missing_metadata():
    from app.agentive.connectors.native_google_proxy import (
        NativeProxyError,
        invoke,
    )
    from app.services.hooks.registry import ToolContext

    ctx = ToolContext(user_id="u", workspace_id="w", scope="w", actor_kind="human")
    with pytest.raises(NativeProxyError) as exc:
        await invoke({}, ctx)
    assert exc.value.error_code == "misconfigured"


# ---------------------------------------------------------------------------
# connected-sources announcement (workspace agent profile overlay)
# ---------------------------------------------------------------------------


class _StubConnector:
    def __init__(
        self,
        id: str,
        subclass_slug: str,
        auth_state: Optional[Dict[str, Any]] = None,
        health_status: str = "ok",
        last_error: Optional[str] = None,
        owner: str = "",
        connection_mode: str = "per_user",
        workspace_id: str = "ws",
        label: str = "",
    ):
        self.id = id
        self.subclass_slug = subclass_slug
        self.auth_state = dict(auth_state or {})
        self.health_status = health_status
        self.last_error = last_error
        self.updated_at = "2026-09-14T00:00:00+00:00"
        self.owner = owner
        self.connection_mode = connection_mode
        self.workspace_id = workspace_id
        self.label = label


@pytest.mark.asyncio
async def test_connected_sources_doc_announces_native_tools(monkeypatch):
    from app.agentive import workspace_agent_profile as wap
    from app.services.hooks.registry import get_workspace_tools

    ws = "ws-sources-test"
    register_native_tools(ws, "conn-drive-1", "drive_native")

    async def fake_find(query):
        assert query == {"workspace_id": ws}
        return [_StubConnector("conn-drive-1", "drive_native", owner="u1")]

    from app.agentive import nodes as agentive_nodes

    monkeypatch.setattr(agentive_nodes.Connector, "find", fake_find)
    doc = await wap._connected_sources_doc(ws, "u1")
    assert doc is not None
    assert doc.name == "connected_data_sources"
    assert "Google Drive" in doc.body
    # Canonical keys are announced; per-row legacy keys stay dispatchable
    # but are not advertised.
    assert "drive_native__search_files" in doc.body
    assert "native__conn_drive" not in doc.body
    assert "search_files" in doc.body
    assert "your connection" in doc.body
    unregister_native_tools(ws, "conn-drive-1", "drive_native")


@pytest.mark.asyncio
async def test_connected_sources_doc_flags_unhealthy_connector(monkeypatch):
    from app.agentive import workspace_agent_profile as wap

    ws = "ws-sources-sick"
    register_native_tools(ws, "conn-sick-1", "sheets_native")

    async def fake_find(query):
        return [
            _StubConnector(
                "conn-sick-1",
                "sheets_native",
                health_status="error",
                last_error="Google authorization expired — re-authorize",
                owner="admin",
                connection_mode="shared",
            )
        ]

    from app.agentive import nodes as agentive_nodes

    monkeypatch.setattr(agentive_nodes.Connector, "find", fake_find)
    doc = await wap._connected_sources_doc(ws, "u2")
    assert doc is not None
    assert "Google Sheets" in doc.body
    assert "shared connection" in doc.body
    assert "needs attention" in doc.body
    assert "re-authorize" in doc.body
    unregister_native_tools(ws, "conn-sick-1", "sheets_native")


@pytest.mark.asyncio
async def test_connected_sources_doc_teammate_row_not_usable(monkeypatch):
    from app.agentive import workspace_agent_profile as wap

    ws = "ws-sources-teammate"
    register_native_tools(ws, "conn-t-1", "drive_native")

    async def fake_find(query):
        return [_StubConnector("conn-t-1", "drive_native", owner="teammate")]

    from app.agentive import nodes as agentive_nodes

    monkeypatch.setattr(agentive_nodes.Connector, "find", fake_find)
    doc = await wap._connected_sources_doc(ws, "u1")
    assert doc is not None
    assert "connected by a teammate" in doc.body
    assert "Connect your own account" in doc.body
    unregister_native_tools(ws, "conn-t-1", "drive_native")


@pytest.mark.asyncio
async def test_connected_sources_doc_none_without_tools(monkeypatch):
    from app.agentive import workspace_agent_profile as wap

    async def fake_find(query):
        # Connected row exists but OAuth never completed → no tools advertised.
        return [_StubConnector("conn-bare-1", "drive_native")]

    from app.agentive import nodes as agentive_nodes

    monkeypatch.setattr(agentive_nodes.Connector, "find", fake_find)
    assert await wap._connected_sources_doc("ws-empty") is None


@pytest.mark.asyncio
async def test_connected_sources_doc_none_without_connectors(monkeypatch):
    from app.agentive import workspace_agent_profile as wap

    async def fake_find(query):
        return []

    from app.agentive import nodes as agentive_nodes

    monkeypatch.setattr(agentive_nodes.Connector, "find", fake_find)
    assert await wap._connected_sources_doc("ws-nothing") is None


def test_connector_display_name_prefers_catalog():
    from app.agentive.workspace_agent_profile import _connector_display_name

    assert (
        _connector_display_name(_StubConnector("c", "drive_native")) == "Google Drive"
    )
    assert (
        _connector_display_name(
            _StubConnector("c", "mcp", {"catalog_slug": "google_drive"})
        )
        == "Google Drive"
    )
    assert _connector_display_name(_StubConnector("c", "weird_thing")) == "weird thing"


def test_connectors_fingerprint_moves_with_tools():
    from app.agentive.workspace_agent_profile import (
        _connected_sources_fingerprint,
    )

    cons = [_StubConnector("c1", "drive_native")]
    a = _connected_sources_fingerprint(cons, ["native__c1__search_files"])
    b = _connected_sources_fingerprint(
        cons, ["native__c1__search_files", "native__c1__read_file_content"]
    )
    assert a != b
    c = _connected_sources_fingerprint(cons, ["native__c1__search_files"], "u1")
    assert c != a


@pytest.mark.asyncio
async def test_bundle_write_validates_before_staging():
    """A schema-invalid write must fail fast, never mint an approval card.

    Regression: the model probed create_file with no args "to see the
    error", which staged a real empty-args card; the user approved it and
    execution failed "name is required". Validation now runs ahead of the
    bless gate, so the probe returns tool_validation_failed and mints
    nothing.
    """
    from app.agentive import staging as staging_mod
    from app.agentive.tooling import dispatch as dispatch_mod

    spec = specs_for_slug("conn-probe-1", "drive_native")[6]  # create_file
    assert spec["_native_write"] is True
    before = len(staging_mod._tokens)
    res = await dispatch_mod._dispatch_bundle_tool(
        spec["key"],
        spec,
        {"_text": "native__conn_probe_1__create_file"},
        principal_id="u1",
        scope="ws-1",
        session_id="sess-1",
    )
    assert res.is_error is True
    assert res.error_code == "tool_validation_failed"
    assert "name" in res.message
    assert len(staging_mod._tokens) == before


# ---------------------------------------------------------------------------
# gmail live tools
# ---------------------------------------------------------------------------


def test_gmail_tool_specs_write_flags():
    from app.agentive.connectors import gmail as gmail_conn

    by_name = {t["name"]: t for t in gmail_conn.TOOL_SPECS}
    assert len(by_name) == 13
    for read_tool in (
        "search_threads",
        "get_thread",
        "get_message",
        "list_labels",
        "list_drafts",
        "get_draft",
    ):
        assert by_name[read_tool]["write"] is False
    for write_tool in (
        "create_draft",
        "send_message",
        "send_draft",
        "delete_draft",
        "trash_thread",
        "untrash_thread",
        "modify_thread_labels",
    ):
        assert by_name[write_tool]["write"] is True


@pytest.mark.asyncio
async def test_gmail_search_threads_shape():
    from app.agentive.connectors import gmail as gmail_conn

    seen: Dict[str, Any] = {}

    async def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        return httpx.Response(
            200,
            json={
                "threads": [{"id": "t1", "snippet": "hi"}],
                "resultSizeEstimate": 1,
            },
        )

    out = await gmail_conn.call_tool(
        "search_threads",
        {"query": "from:boss", "max_results": 5},
        access_token="tok",
        http_client=_mock_client(handler),
    )
    assert out["threads"][0]["id"] == "t1"
    assert out["result_size_estimate"] == 1
    assert "q=from" in seen["url"]


@pytest.mark.asyncio
async def test_gmail_get_thread_prunes_messages():
    from app.agentive.connectors import gmail as gmail_conn

    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "id": "t1",
                "snippet": "s",
                "historyId": "9",
                "messages": [
                    {
                        "id": f"m{i}",
                        "threadId": "t1",
                        "snippet": "x",
                        "labelIds": ["INBOX"],
                        "payload": {
                            "headers": [
                                {"name": "Subject", "value": f"Re {i}"},
                                {"name": "From", "value": "a@x.io"},
                            ]
                        },
                    }
                    for i in range(25)
                ],
            },
        )

    out = await gmail_conn.call_tool(
        "get_thread",
        {"thread_id": "t1"},
        access_token="tok",
        http_client=_mock_client(handler),
    )
    assert out["truncated"] is True
    assert out["message_count"] == gmail_conn._GMAIL_THREAD_MESSAGE_LIMIT
    assert out["messages"][0]["subject"] == "Re 0"
    assert "body" not in out["messages"][0]


@pytest.mark.asyncio
async def test_gmail_create_draft_mime_shape():
    import base64

    from app.agentive.connectors import gmail as gmail_conn

    seen: Dict[str, Any] = {}

    async def handler(request: httpx.Request) -> httpx.Response:
        seen["body"] = json.loads(request.content.decode())
        assert request.method == "POST"
        return httpx.Response(200, json={"id": "d1"})

    out = await gmail_conn.call_tool(
        "create_draft",
        {"to": "b@y.io", "subject": "Hi", "body": "hello"},
        access_token="tok",
        http_client=_mock_client(handler),
    )
    assert out["id"] == "d1"
    raw = base64.urlsafe_b64decode(seen["body"]["message"]["raw"]).decode()
    assert "To: b@y.io" in raw
    assert "Subject: Hi" in raw
    assert "hello" in raw


@pytest.mark.asyncio
async def test_gmail_send_message_posts_send_endpoint():
    from app.agentive.connectors import gmail as gmail_conn

    seen: Dict[str, Any] = {}

    async def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        return httpx.Response(200, json={"id": "m9", "threadId": "t9"})

    out = await gmail_conn.call_tool(
        "send_message",
        {"to": "b@y.io", "subject": "Hi", "body": "hello"},
        access_token="tok",
        http_client=_mock_client(handler),
    )
    assert "/messages/send" in seen["url"]
    assert out["id"] == "m9"


@pytest.mark.asyncio
async def test_gmail_insufficient_scope_maps_to_auth():
    from app.agentive.connectors import gmail as gmail_conn
    from app.agentive.connectors.drive_native import NativeToolError

    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            403,
            json={
                "error": {
                    "message": "Insufficient Permission",
                    "errors": [{"reason": "insufficientPermissions"}],
                }
            },
        )

    with pytest.raises(NativeToolError) as exc:
        await gmail_conn.call_tool(
            "send_message",
            {"to": "b@y.io", "subject": "Hi", "body": "hello"},
            access_token="tok",
            http_client=_mock_client(handler),
        )
    assert exc.value.error_code == "auth"
    assert "re-authorize" in exc.value.message


@pytest.mark.asyncio
async def test_gmail_modify_labels_requires_a_change():
    from app.agentive.connectors import gmail as gmail_conn
    from app.agentive.connectors.drive_native import NativeToolError

    with pytest.raises(NativeToolError) as exc:
        await gmail_conn.call_tool(
            "modify_thread_labels", {"thread_id": "t1"}, access_token="tok"
        )
    assert exc.value.error_code == "bad_args"


@pytest.mark.asyncio
async def test_gmail_recipient_validation():
    from app.agentive.connectors import gmail as gmail_conn
    from app.agentive.connectors.drive_native import NativeToolError

    with pytest.raises(NativeToolError) as exc:
        await gmail_conn.call_tool(
            "send_message",
            {"to": "not-an-email", "subject": "Hi", "body": "x"},
            access_token="tok",
        )
    assert exc.value.error_code == "bad_args"


@pytest.mark.asyncio
async def test_native_health_probe_gmail_branch(monkeypatch):
    from app.agentive.connectors import native_tool_registry as reg

    requests: List[str] = []

    class FakeResp:
        status_code = 200

    class FakeHttp:
        def __init__(self, *a, **k):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def get(self, url, **kwargs):
            requests.append(url)
            assert kwargs["headers"] == {"Authorization": "Bearer ya29.fresh"}
            return FakeResp()

    monkeypatch.setattr(httpx, "AsyncClient", FakeHttp)
    conn = FakeConnector(_fresh_auth())
    conn.subclass_slug = "gmail"
    conn.id = "conn-gmail-1"
    out = await reg.health_check_native_connector(conn)
    assert out["status"] == "ok"
    assert out["tool_count"] == 13
    assert any("/profile" in u for u in requests)


@pytest.mark.asyncio
async def test_native_health_probe_pending_without_tokens():
    from app.agentive.connectors import native_tool_registry as reg

    conn = FakeConnector({})
    conn.subclass_slug = "gmail"
    out = await reg.health_check_native_connector(conn)
    assert out["status"] == "unknown"
    assert "sign-in" in (out["last_error"] or "")


def test_google_profile_yaml_valid():
    from pathlib import Path

    import yaml

    path = (
        Path(__file__).resolve().parent.parent
        / "app"
        / "profiles"
        / "google-workspace"
        / "profile.yaml"
    )
    if not path.is_file():
        pytest.skip("google-workspace profile not installed in core")
    doc = yaml.safe_load(path.read_text(encoding="utf-8"))
    assert doc["package"]["slug"] == "google-workspace"
    track = doc["track"]
    keys = {et["key"] for et in track["entry_types"]}
    assert keys == {"drive_file", "spreadsheet"}
