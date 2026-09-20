"""Hook + tool registry: frozen hook-point catalog + per-workspace tool cache."""

import pytest

from app.services.hooks.registry import (
    HOOK_POINTS,
    ToolContext,
    clear_workspace_registrations,
    get_workspace_hooks,
    get_workspace_tools,
    register_workspace_hooks,
    register_workspace_tools,
)


def test_hook_point_catalog_frozen():
    assert "entry.transform" in HOOK_POINTS
    assert "entry.public_share" in HOOK_POINTS
    assert "entry.precompute" in HOOK_POINTS
    assert "entry.create" in HOOK_POINTS
    assert "entry.validate" in HOOK_POINTS
    assert "entry.update" in HOOK_POINTS
    assert "connector.dedup" in HOOK_POINTS
    assert "connector.auto_link" in HOOK_POINTS
    # Frozen: must be a frozenset to enforce immutability.
    assert isinstance(HOOK_POINTS, frozenset)


def test_register_and_fetch_tools():
    ws = "n.Workspace.test1"
    spec = {
        "key": "t1",
        "handler_ref": "mod:fn",
        "parameters_schema": {"type": "object"},
        "output_schema": {"type": "object"},
        "privileged": False,
        "side_effects": "read_only",
    }
    register_workspace_tools(ws, "bundle-slug", [spec])
    fetched = get_workspace_tools(ws)
    assert "t1" in fetched
    assert fetched["t1"]["handler_ref"] == "mod:fn"
    clear_workspace_registrations(ws)
    assert get_workspace_tools(ws) == {}


def test_register_and_fetch_hooks():
    ws = "n.Workspace.test2"
    binding = {
        "point": "entry.transform",
        "key": "h1",
        "match": {"source_entry_type": "x"},
        "mode": "declarative",
        "declarative": {"copy_fields": []},
    }
    register_workspace_hooks(ws, "bundle-slug", [binding])
    fetched = get_workspace_hooks(ws, "entry.transform")
    assert len(fetched) == 1
    assert fetched[0]["key"] == "h1"
    clear_workspace_registrations(ws)
    assert get_workspace_hooks(ws, "entry.transform") == []


def test_tool_context_facade_has_required_methods():
    ctx = ToolContext(user_id="u1", workspace_id="w1", scope="entry:e1")
    # Compile-time API surface check — methods exist on the class.
    assert callable(getattr(ctx, "get_entry"))
    assert callable(getattr(ctx, "find_entries"))
    assert callable(getattr(ctx, "get_employee_compensation"))
    assert callable(getattr(ctx, "emit_audit"))
    assert callable(getattr(ctx, "get_entry_system"))
    assert callable(getattr(ctx, "update_entry_fields"))


def test_unknown_hook_point_rejected():
    from app.services.hooks.errors import HookMisconfiguredError

    ws = "n.Workspace.test3"
    with pytest.raises(HookMisconfiguredError):
        register_workspace_hooks(
            ws,
            "bundle-slug",
            [
                {
                    "point": "entry.bogus",
                    "key": "h2",
                    "mode": "declarative",
                    "declarative": {},
                }
            ],
        )


def test_runtime_tool_registration_rejects_cross_bundle_replacement():
    """A dynamically loaded bundle cannot replace another bundle's capability."""
    from app.services.hooks.errors import HookMisconfiguredError

    workspace_id = "n.Workspace.runtime-collision"
    spec = {"key": "assets.inspect", "handler_ref": "first:run"}
    clear_workspace_registrations(workspace_id)
    try:
        register_workspace_tools(workspace_id, "first", [spec])
        with pytest.raises(HookMisconfiguredError, match="already registered"):
            register_workspace_tools(
                workspace_id,
                "second",
                [{"key": "assets.inspect", "handler_ref": "second:run"}],
            )
        assert (
            get_workspace_tools(workspace_id)["assets.inspect"]["handler_ref"]
            == "first:run"
        )
    finally:
        clear_workspace_registrations(workspace_id)


def test_runtime_tool_registration_allows_same_bundle_refresh():
    """A bundle may refresh its own registration during restart recovery."""
    workspace_id = "n.Workspace.runtime-refresh"
    clear_workspace_registrations(workspace_id)
    try:
        register_workspace_tools(
            workspace_id, "assets", [{"key": "assets.inspect", "handler_ref": "v1:run"}]
        )
        register_workspace_tools(
            workspace_id, "assets", [{"key": "assets.inspect", "handler_ref": "v2:run"}]
        )
        assert (
            get_workspace_tools(workspace_id)["assets.inspect"]["handler_ref"]
            == "v2:run"
        )
    finally:
        clear_workspace_registrations(workspace_id)
