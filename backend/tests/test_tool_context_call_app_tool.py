"""ToolContext.call_app_tool: one app's tool calling another app's exported tool."""

from __future__ import annotations

import sys
import types

import pytest

from app.services.hooks import registry
from app.services.hooks.registry import ToolContext


def _install_tool(
    monkeypatch, *, key="generate_document", exported=True, bundle="documents"
):
    calls = []

    async def handler(payload, ctx):
        calls.append(
            {
                "payload": payload,
                "bundle": ctx.bundle_slug,
                "depth": ctx._app_call_depth,
            }
        )
        return {"ok": True, "echo": payload.get("title")}

    mod = types.ModuleType("fake_documents_tools")
    mod.generate_document = handler
    monkeypatch.setitem(sys.modules, "fake_documents_tools", mod)
    spec = {
        "key": key,
        "handler_ref": "fake_documents_tools:generate_document",
        "parameters_schema": {
            "type": "object",
            "required": ["title"],
            "properties": {"title": {"type": "string"}},
        },
        "output_schema": {},
        "exported": exported,
        "_bundle_slug": bundle,
    }
    monkeypatch.setattr(
        registry, "get_workspace_tools", lambda ws: {key: spec} if ws == "w1" else {}
    )
    return calls


@pytest.fixture
def audit(monkeypatch):
    seen = []

    async def emit_audit(self, action, details):
        seen.append((action, details))

    monkeypatch.setattr(ToolContext, "emit_audit", emit_audit)
    return seen


def _ctx(bundle="invoicing", **kw):
    ctx = ToolContext(user_id="u1", workspace_id="w1", scope="entry:e1", **kw)
    ctx.bundle_slug = bundle
    return ctx


@pytest.mark.asyncio
async def test_calls_an_exported_tool_as_the_callee_bundle(monkeypatch, audit):
    calls = _install_tool(monkeypatch)
    ctx = _ctx()
    out = await ctx.call_app_tool("generate_document", {"title": "Invoice 1"})
    assert out == {"ok": True, "echo": "Invoice 1"}
    # the callee runs as itself, one level deeper; the caller's own context is untouched
    assert calls == [
        {"payload": {"title": "Invoice 1"}, "bundle": "documents", "depth": 1}
    ]
    assert ctx.bundle_slug == "invoicing"
    names = [a for a, _ in audit]
    assert "tool.cross_app_call" in names
    details = dict(audit)["tool.cross_app_call"]
    assert (
        details["caller_bundle"] == "invoicing"
        and details["callee_bundle"] == "documents"
    )


@pytest.mark.asyncio
async def test_a_tool_that_is_not_exported_is_unreachable(monkeypatch, audit):
    calls = _install_tool(monkeypatch, exported=False)
    with pytest.raises(PermissionError):
        await _ctx().call_app_tool("generate_document", {"title": "x"})
    assert calls == []


@pytest.mark.asyncio
async def test_unknown_and_unexported_look_the_same(monkeypatch, audit):
    _install_tool(monkeypatch, exported=False)
    messages = []
    for key in ("generate_document", "no_such_tool"):
        with pytest.raises(PermissionError) as exc:
            await _ctx().call_app_tool(key, {})
        messages.append(str(exc.value).replace(key, "<k>"))
    assert messages[0] == messages[1]


@pytest.mark.asyncio
async def test_only_a_running_app_tool_may_call(monkeypatch, audit):
    calls = _install_tool(monkeypatch)
    with pytest.raises(PermissionError):
        await _ctx(bundle="").call_app_tool("generate_document", {"title": "x"})
    assert calls == []


@pytest.mark.asyncio
async def test_other_workspaces_tools_are_not_visible(monkeypatch, audit):
    _install_tool(monkeypatch)
    ctx = ToolContext(user_id="u1", workspace_id="w2", scope="entry:e1")
    ctx.bundle_slug = "invoicing"
    with pytest.raises(PermissionError):
        await ctx.call_app_tool("generate_document", {"title": "x"})


@pytest.mark.asyncio
async def test_nesting_is_capped(monkeypatch, audit):
    calls = _install_tool(monkeypatch)
    ctx = _ctx()
    ctx._app_call_depth = ToolContext._MAX_APP_CALL_DEPTH
    with pytest.raises(PermissionError):
        await ctx.call_app_tool("generate_document", {"title": "x"})
    assert calls == []


@pytest.mark.asyncio
async def test_the_callees_input_schema_still_applies(monkeypatch, audit):
    from app.services.hooks.errors import ToolValidationFailedError

    calls = _install_tool(monkeypatch)
    with pytest.raises(ToolValidationFailedError):
        await _ctx().call_app_tool("generate_document", {})
    assert calls == []


@pytest.mark.asyncio
async def test_workspace_create_needs_a_running_bundle_and_a_workspace():
    ctx = ToolContext(user_id="u1", workspace_id="w1", scope="entry:e1")
    assert (
        await ctx.create_entry_in_workspace_bundle_track("documents", title="T") is None
    )
    ctx.bundle_slug = "documents"
    assert await ctx.create_entry_in_workspace_bundle_track("", title="T") is None


def test_the_manifest_compiler_carries_exported_and_defaults_it_off():
    from app.services.operational_model_compile import _parse_manifest_tools

    def tool(key, **extra):
        return {"key": key, "handler_ref": "tools.x:fn", **extra}

    specs = _parse_manifest_tools(
        [tool("a", exported=True), tool("b")],
        bundle_slug="x",
        trust_tier="trusted",
        where="tools",
    )
    by_key = {s["key"]: s for s in specs}
    assert by_key["a"]["exported"] is True
    assert by_key["b"]["exported"] is False


# --- create_entry_in_workspace_bundle_track, against the real graph -----------------


async def _documents_app(workspace_id: str, user_id: str, *, slug="documents"):
    from app.models.edges import CONTAINS
    from app.models.nodes import App, EntryType, Track

    app_node = await App.create(
        name="Documents",
        workspace_id=workspace_id,
        owner_id=user_id,
        lifecycle_state="active",
        context={"source_operational_model_slug": slug},
        source_operational_model_slug=slug,
    )
    track = await Track.create(
        title="Documents",
        owner_id=user_id,
        workspace_id=workspace_id,
        template_id="documents",
    )
    await app_node.connect(track, edge=CONTAINS)
    await EntryType.create(
        name="Document",
        icon="file-text",
        form_schema={
            "_manifest_entry_type_key": "document",
            "fields": [{"key": "format", "name": "File type", "type": "text"}],
        },
        track_id=track.id,
        is_template=False,
    )
    return app_node, track


def _allow(monkeypatch, role="editor", allowed=True):
    async def resolve_role(_u, _kind, _id):
        return role

    async def evaluate(**_kw):
        return types.SimpleNamespace(allowed=allowed, reason="test")

    monkeypatch.setattr("app.services.permissions.resolve_role", resolve_role)
    monkeypatch.setattr("app.services.policy_engine.evaluate", evaluate)


@pytest.mark.asyncio
async def test_workspace_create_writes_in_this_workspace_and_marks_the_app_as_writer(
    monkeypatch,
):
    from app.models.nodes import Entry

    _allow(monkeypatch)
    _, track = await _documents_app("ws-gen-1", "user-gen-1")
    ctx = ToolContext(user_id="user-gen-1", workspace_id="ws-gen-1", scope="entry:e1")
    ctx.bundle_slug = "documents"

    entry_id = await ctx.create_entry_in_workspace_bundle_track(
        "documents",
        title="Invoice 7",
        body="# Items",
        fields={"format": "pdf"},
        entry_type_key="document",
    )
    assert entry_id
    entry = await Entry.get(entry_id)
    assert (
        entry.track_id == track.id
        and entry.title == "Invoice 7"
        and entry.body == "# Items"
    )
    assert (
        entry.provenance.source == "agent"
        and entry.provenance.source_id == "bundle:documents"
    )


@pytest.mark.asyncio
async def test_workspace_create_cannot_reach_another_bundles_or_workspaces_tracks(
    monkeypatch,
):
    _allow(monkeypatch)
    await _documents_app("ws-gen-2", "user-gen-2")
    other_bundle = ToolContext(
        user_id="user-gen-2", workspace_id="ws-gen-2", scope="entry:e1"
    )
    other_bundle.bundle_slug = (
        "invoicing"  # a different app cannot write into Documents' track
    )
    assert (
        await other_bundle.create_entry_in_workspace_bundle_track(
            "documents", title="x"
        )
        is None
    )

    elsewhere = ToolContext(
        user_id="user-gen-2", workspace_id="ws-gen-2-other", scope="entry:e1"
    )
    elsewhere.bundle_slug = "documents"  # Documents is not installed in that workspace
    assert (
        await elsewhere.create_entry_in_workspace_bundle_track("documents", title="x")
        is None
    )


@pytest.mark.asyncio
async def test_workspace_create_respects_roles_and_policy(monkeypatch):
    await _documents_app("ws-gen-3", "user-gen-3")
    ctx = ToolContext(user_id="user-gen-3", workspace_id="ws-gen-3", scope="entry:e1")
    ctx.bundle_slug = "documents"

    _allow(monkeypatch, role="viewer")
    assert (
        await ctx.create_entry_in_workspace_bundle_track("documents", title="x") is None
    )
    _allow(monkeypatch, role="editor", allowed=False)
    assert (
        await ctx.create_entry_in_workspace_bundle_track("documents", title="x") is None
    )


# --- call_connector_tool ---------------------------------------------------------------


def _catalog_read_only(monkeypatch, *names, slug="google_drive_direct"):
    """The vetted catalog entry's read_only_tools (the one source Core trusts)."""

    def fake(catalog_slug):
        return frozenset(names) if catalog_slug == slug else frozenset()

    monkeypatch.setattr(
        "app.agentive.connectors.mcp_tool_class._catalog_read_only_tools", fake
    )


def _mount_connector_tool(
    monkeypatch,
    *,
    vetted_read_only=True,
    slug="google_drive_direct",
    tool="list_folder",
    annotations=None,
):
    calls = []

    async def fake_run_tool(spec, payload, ctx):
        calls.append(
            {"spec": spec["key"], "payload": payload, "bundle": ctx.bundle_slug}
        )
        return {"files": [{"id": "f1", "version": "v1"}]}

    spec = {
        "key": f"mcp__{slug}__{tool}",
        "_mcp_connector_slug": slug,
        "_mcp_remote_name": tool,
        # what the remote SERVER claims; it must make no difference
        "_mcp_annotations": annotations if annotations is not None else {},
        "input_schema": {"type": "object"},
        "_bundle_slug": f"mcp:canonical:{slug}",
    }
    monkeypatch.setattr(
        registry,
        "get_workspace_tools",
        lambda ws: {spec["key"]: spec} if ws == "w1" else {},
    )
    monkeypatch.setattr("app.services.hooks.tool_dispatch.run_tool", fake_run_tool)
    _catalog_read_only(monkeypatch, *([tool] if vetted_read_only else []), slug=slug)
    return calls


@pytest.mark.asyncio
async def test_a_tool_the_catalog_lists_read_only_can_be_called_even_with_no_annotations(
    monkeypatch, audit
):
    # After an API restart Core rebuilds mounted tools with NO annotations; the catalog still vouches.
    calls = _mount_connector_tool(monkeypatch, annotations={})
    out = await _ctx("documents").call_connector_tool(
        "google_drive_direct", "list_folder", {"folderId": "abc"}
    )
    assert out == {"files": [{"id": "f1", "version": "v1"}]}
    assert calls == [
        {
            "spec": "mcp__google_drive_direct__list_folder",
            "payload": {"folderId": "abc"},
            "bundle": "documents",
        }
    ]
    assert dict(audit)["tool.connector_call"]["caller_bundle"] == "documents"


@pytest.mark.asyncio
async def test_a_server_that_claims_read_only_cannot_unlock_a_tool_the_catalog_does_not_vouch_for(
    monkeypatch, audit
):
    calls = _mount_connector_tool(
        monkeypatch,
        vetted_read_only=False,
        tool="delete_everything",
        annotations={"readOnlyHint": True, "destructiveHint": False},
    )
    with pytest.raises(PermissionError, match="not listed read-only"):
        await _ctx("documents").call_connector_tool(
            "google_drive_direct", "delete_everything", {}
        )
    assert calls == []


@pytest.mark.asyncio
async def test_an_unmounted_connector_or_other_workspace_or_non_bundle_caller_is_refused(
    monkeypatch, audit
):
    calls = _mount_connector_tool(monkeypatch)
    with pytest.raises(PermissionError):
        await _ctx("documents").call_connector_tool("microsoft", "list_folder", {})
    other = ToolContext(user_id="u1", workspace_id="w2", scope="entry:e1")
    other.bundle_slug = "documents"
    with pytest.raises(PermissionError):
        await other.call_connector_tool("google_drive_direct", "list_folder", {})
    with pytest.raises(PermissionError):
        await _ctx("").call_connector_tool("google_drive_direct", "list_folder", {})
    assert calls == []


# --- notify -----------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_notify_sends_an_in_app_system_notification_to_the_acting_person(
    monkeypatch,
):
    sent = {}

    async def fake_dispatch(**kw):
        sent.update(kw)
        return {"notification_id": "n1", "results": []}

    monkeypatch.setattr("app.services.notification_router.dispatch", fake_dispatch)
    nid = await _ctx("documents").notify(
        "3 documents changed", "Policy, Audit plan", entry_id="e9", track_id="t9"
    )
    assert nid == "n1"
    assert (
        sent["user_id"] == "u1"
        and sent["kind"] == "system"
        and sent["actor_kind"] == "system"
    )
    assert sent["payload"]["content"] == "3 documents changed: Policy, Audit plan"
    assert sent["payload"]["entry_id"] == "e9" and sent["payload"]["track_id"] == "t9"


@pytest.mark.asyncio
async def test_notify_never_raises_and_needs_a_title(monkeypatch):
    async def boom(**kw):
        raise RuntimeError("channel down")

    monkeypatch.setattr("app.services.notification_router.dispatch", boom)
    assert await _ctx("documents").notify("Hello") is None
    assert await _ctx("documents").notify("   ") is None


# --- connector writes ---------------------------------------------------------------------


def _write_ctx(
    *, allowed=("google_drive_direct.upload_as_google_file",), is_write=True
):
    ctx = _ctx("documents")
    ctx.connector_writes = tuple(allowed)
    ctx.tool_is_write = is_write
    return ctx


def _mount_write_tool(monkeypatch, *, annotations=None, vetted_read_only=False):
    return _mount_connector_tool(
        monkeypatch,
        vetted_read_only=vetted_read_only,
        tool="upload_as_google_file",
        annotations=annotations,
    )


@pytest.mark.asyncio
async def test_a_declared_write_tool_may_write_through_a_tool_the_catalog_does_not_list_read_only(
    monkeypatch, audit
):
    calls = _mount_write_tool(
        monkeypatch, annotations={}
    )  # no annotations after a restart: irrelevant
    out = await _write_ctx().call_connector_tool(
        "google_drive_direct", "upload_as_google_file", {}, write=True
    )
    assert out == {"files": [{"id": "f1", "version": "v1"}]}
    assert [c["spec"] for c in calls] == [
        "mcp__google_drive_direct__upload_as_google_file"
    ]
    assert "tool.connector_write" in [a for a, _ in audit]


@pytest.mark.asyncio
async def test_a_write_needs_the_exact_declaration_and_a_write_tool(monkeypatch, audit):
    calls = _mount_write_tool(monkeypatch)
    with pytest.raises(PermissionError, match="did not declare"):
        await _write_ctx(allowed=()).call_connector_tool(
            "google_drive_direct", "upload_as_google_file", {}, write=True
        )
    with pytest.raises(PermissionError, match="did not declare"):
        await _write_ctx(
            allowed=("google_drive_direct.create_folder",)
        ).call_connector_tool(
            "google_drive_direct", "upload_as_google_file", {}, write=True
        )
    with pytest.raises(PermissionError, match="write tool"):
        await _write_ctx(is_write=False).call_connector_tool(
            "google_drive_direct", "upload_as_google_file", {}, write=True
        )
    assert calls == []


@pytest.mark.asyncio
async def test_a_write_cannot_target_a_tool_the_catalog_lists_read_only(
    monkeypatch, audit
):
    calls = _mount_write_tool(monkeypatch, vetted_read_only=True)
    with pytest.raises(PermissionError, match="listed read-only"):
        await _write_ctx().call_connector_tool(
            "google_drive_direct", "upload_as_google_file", {}, write=True
        )
    assert calls == []


@pytest.mark.asyncio
async def test_a_write_tool_cannot_be_called_without_write_true(monkeypatch, audit):
    calls = _mount_write_tool(monkeypatch)
    with pytest.raises(PermissionError, match="write=True"):
        await _write_ctx().call_connector_tool(
            "google_drive_direct", "upload_as_google_file", {}
        )
    assert calls == []


@pytest.mark.asyncio
async def test_run_tool_stamps_the_declared_writes_from_the_spec_not_from_the_tool(
    monkeypatch,
):
    from app.services.hooks.tool_dispatch import run_tool

    seen = {}

    async def handler(payload, ctx):
        seen["writes"], seen["is_write"] = ctx.connector_writes, ctx.tool_is_write
        return {}

    mod = types.ModuleType("fake_stamp_tools")
    mod.go = handler
    monkeypatch.setitem(sys.modules, "fake_stamp_tools", mod)

    async def emit_audit(self, action, details):
        return None

    monkeypatch.setattr(ToolContext, "emit_audit", emit_audit)
    spec = {
        "key": "k",
        "handler_ref": "fake_stamp_tools:go",
        "parameters_schema": {},
        "output_schema": {},
        "side_effects": "write",
        "connector_writes": ["google_drive_direct.upload_as_google_file"],
        "_bundle_slug": "documents",
    }
    ctx = ToolContext(user_id="u1", workspace_id="w1", scope="tool:k")
    await run_tool(spec, {}, ctx)
    assert seen == {
        "writes": ("google_drive_direct.upload_as_google_file",),
        "is_write": True,
    }
    spec["side_effects"] = "read_only"
    spec["connector_writes"] = []
    await run_tool(spec, {}, ctx)
    assert seen == {"writes": (), "is_write": False}


def test_the_manifest_compiler_carries_connector_writes():
    from app.services.operational_model_compile import _parse_manifest_tools

    specs = _parse_manifest_tools(
        [
            {
                "key": "a",
                "handler_ref": "tools.x:fn",
                "connector_writes": ["google_drive_direct.upload_as_google_file"],
            },
            {"key": "b", "handler_ref": "tools.x:fn"},
        ],
        bundle_slug="x",
        trust_tier="trusted",
        where="tools",
    )
    by_key = {s["key"]: s for s in specs}
    assert by_key["a"]["connector_writes"] == [
        "google_drive_direct.upload_as_google_file"
    ]
    assert by_key["b"]["connector_writes"] == []
