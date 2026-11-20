"""A trusted host adapter only runs inside its validated write tool."""

import asyncio
from copy import deepcopy
from unittest.mock import AsyncMock

import pytest

from app.api.auth import _merge_user_preferences, _scrub_sensitive_preferences
from app.api.errors import InsufficientPermissionsError
from app.services import host_hooks
from app.services.hooks import registry, tool_dispatch


def test_host_managed_preference_slots_are_not_client_editable(monkeypatch):
    monkeypatch.setattr(host_hooks, "_server_managed_preferences", frozenset())
    host_hooks.register_server_managed_preference_keys("host_managed_slot")
    existing = {"host_managed_slot": {"version": 1}, "theme": "dark"}
    merged = _merge_user_preferences(
        existing, {"host_managed_slot": {"version": 2}, "theme": "light"}
    )
    assert merged == {"host_managed_slot": {"version": 1}, "theme": "light"}
    assert _scrub_sensitive_preferences(merged) == {"theme": "light"}


@pytest.fixture
def bound_tool(monkeypatch):
    monkeypatch.setattr(host_hooks, "_host_tools", {})
    monkeypatch.setattr(registry, "_TOOLS", {})
    monkeypatch.setattr(registry.ToolContext, "emit_audit", AsyncMock())
    handler = AsyncMock(return_value={"ok": True})
    host_hooks.register_host_tool("extension-demo", "apply-change", handler)
    registry.register_workspace_tools(
        "workspace",
        "extension-demo",
        [
            {
                "key": "apply-change",
                "handler_ref": "demo:tool",
                "side_effects": "write",
                "parameters_schema": {
                    "type": "object",
                    "required": ["label"],
                    "properties": {"label": {"type": "string"}},
                },
            }
        ],
    )
    return handler, registry.get_workspace_tools("workspace")["apply-change"]


@pytest.mark.asyncio
async def test_actual_tool_runs_only_inside_current_registered_dispatch(
    monkeypatch, bound_tool
):
    handler, spec = bound_tool
    ctx = registry.ToolContext("principal", "workspace", "entry:record")
    payload = {"label": "accepted"}

    async def tool(body, context):
        result = await context.run_host_tool(body)
        with pytest.raises(InsufficientPermissionsError):
            await context.run_host_tool(body)
        return result

    monkeypatch.setattr(tool_dispatch, "resolve_handler", lambda _: tool)
    with pytest.raises(InsufficientPermissionsError):
        await ctx.run_host_tool(payload)

    assert await tool_dispatch._run_tool(spec, payload, ctx) == {"ok": True}
    handler.assert_awaited_once_with(payload, ctx)
    with pytest.raises(InsufficientPermissionsError):
        await ctx.run_host_tool(payload)
    with pytest.raises(InsufficientPermissionsError):
        await tool_dispatch._run_tool(deepcopy(spec), payload, ctx)
    handler.assert_awaited_once()


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["agent", "read", "task", "different_input"])
async def test_host_tool_respects_dispatch_bounds(monkeypatch, bound_tool, mode):
    handler, spec = bound_tool
    ctx = registry.ToolContext(
        "principal",
        "workspace",
        "entry:record",
        actor_kind="agent" if mode == "agent" else "human",
        read_only=mode == "read",
    )

    async def tool(body, context):
        if mode == "task":
            return await asyncio.create_task(context.run_host_tool(body))
        if mode == "different_input":
            return await context.run_host_tool({"label": "different"})
        return await context.run_host_tool(body)

    monkeypatch.setattr(tool_dispatch, "resolve_handler", lambda _: tool)
    with pytest.raises(InsufficientPermissionsError):
        await tool_dispatch._run_tool(spec, {"label": "accepted"}, ctx)
    handler.assert_not_awaited()
