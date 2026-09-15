"""Workspace-tool agent surface: list, invoke, stage, agent_callable gate."""

from __future__ import annotations

import pytest

from app.services.hooks.registry import (
    clear_workspace_registrations,
    register_workspace_tools,
)

WS = "n.Workspace.test-ws-tools"


async def fake_write_tool(payload, ctx):
    return {"ok": True, "echo": dict(payload or {}), "ws": ctx.workspace_id}


async def fake_read_tool(payload, ctx):
    return {"ok": True, "periods": ["2026-07"]}


@pytest.fixture
def workspace_tools(monkeypatch):
    async def _admin(user_id, workspace_id):
        return "admin"

    monkeypatch.setattr("app.services.workspace_tools.can_access_workspace", _admin)
    register_workspace_tools(
        WS,
        "test-bundle",
        [
            {
                "key": "populate_lines",
                "name": "Populate lines",
                "description": "Write tool",
                "handler_ref": "tests.test_workspace_tool_agent:fake_write_tool",
                "parameters_schema": {
                    "type": "object",
                    "properties": {"entry_id": {"type": "string"}},
                },
                "privileged": False,
                "side_effects": "write",
                "agent_callable": True,
            },
            {
                "key": "compute_periods",
                "name": "Compute periods",
                "description": "Read tool",
                "handler_ref": "tests.test_workspace_tool_agent:fake_read_tool",
                "parameters_schema": {"type": "object", "properties": {}},
                "privileged": False,
                "side_effects": "read",
                "agent_callable": True,
            },
            {
                "key": "human_only_finalize",
                "name": "Finalize",
                "description": "Action-bar only",
                "handler_ref": "tests.test_workspace_tool_agent:fake_write_tool",
                "parameters_schema": {"type": "object", "properties": {}},
                "privileged": False,
                "side_effects": "write",
                "agent_callable": False,
            },
        ],
    )
    yield
    clear_workspace_registrations(WS)


@pytest.mark.asyncio
async def test_list_omits_human_only_tools(workspace_tools):
    from app.services.workspace_tools import list_workspace_tools

    result = await list_workspace_tools(user_id="u1", workspace_id=WS)
    keys = {t["key"] for t in result["tools"]}
    assert keys == {"populate_lines", "compute_periods"}
    assert "human_only_finalize" not in keys
    writes = {t["key"]: t["write"] for t in result["tools"]}
    assert writes["populate_lines"] is True
    assert writes["compute_periods"] is False


@pytest.mark.asyncio
async def test_invoke_agent_refuses_human_only(workspace_tools):
    from app.api.errors import InsufficientPermissionsError
    from app.services.workspace_tools import invoke_workspace_tool

    with pytest.raises(InsufficientPermissionsError):
        await invoke_workspace_tool(
            user_id="u1",
            workspace_id=WS,
            tool_key="human_only_finalize",
            payload={},
            agent=True,
        )


@pytest.mark.asyncio
async def test_invoke_human_path_allows_action_bar_tool(workspace_tools):
    from app.services.workspace_tools import invoke_workspace_tool

    out = await invoke_workspace_tool(
        user_id="u1",
        workspace_id=WS,
        tool_key="human_only_finalize",
        payload={"entry_id": "n.Entry.x"},
        agent=False,
    )
    assert out["ok"] is True
    assert out["echo"]["entry_id"] == "n.Entry.x"


@pytest.mark.asyncio
async def test_stager_write_stages_and_read_runs_immediately(workspace_tools):
    from app.agentive.tooling.bindings import (
        _propose_principal,
        _stage_call_workspace_tool,
    )
    from app.services.agent_scope import current_scope_workspace_id

    scope_tok = current_scope_workspace_id.set(WS)
    prin_tok = _propose_principal.set("u1")
    try:
        staged = await _stage_call_workspace_tool(
            {"tool_key": "populate_lines", "input": {"entry_id": "{{step_1.id}}"}}
        )
        assert staged["kind"] == "call_workspace_tool"
        assert staged["payload"]["input"]["entry_id"] == "{{step_1.id}}"
        assert staged.get("_no_stage") is None

        immediate = await _stage_call_workspace_tool(
            {"tool_key": "compute_periods", "input": {}}
        )
        assert immediate["_no_stage"] is True
        assert immediate["data"]["output"]["periods"] == ["2026-07"]

        with pytest.raises(ValueError, match="not callable from chat"):
            await _stage_call_workspace_tool({"tool_key": "human_only_finalize"})
    finally:
        _propose_principal.reset(prin_tok)
        current_scope_workspace_id.reset(scope_tok)


@pytest.mark.asyncio
async def test_executor_runs_write_tool(workspace_tools):
    from app.agentive.staging_executors import _x_call_workspace_tool
    from app.services.agent_scope import current_scope_workspace_id

    tok = current_scope_workspace_id.set(WS)
    try:
        result = await _x_call_workspace_tool(
            "u1",
            {"tool_key": "populate_lines", "input": {"entry_id": "n.Entry.abc"}},
        )
    finally:
        current_scope_workspace_id.reset(tok)
    assert result["ok"] is True
    assert result["output"]["echo"]["entry_id"] == "n.Entry.abc"


def test_compile_passes_agent_callable():
    from app.services.content_profile_compile import _parse_manifest_tools

    tools = _parse_manifest_tools(
        [
            {
                "key": "visible",
                "handler_ref": "mod:fn",
                "side_effects": "write",
            },
            {
                "key": "hidden",
                "handler_ref": "mod:fn",
                "side_effects": "write",
                "agent_callable": False,
            },
        ],
        bundle_slug="test",
        trust_tier="trusted",
        where="app.tools",
    )
    by_key = {t["key"]: t for t in tools}
    assert by_key["visible"]["agent_callable"] is True
    assert by_key["hidden"]["agent_callable"] is False


def test_new_tools_in_catalogue_and_executors():
    from app.agentive.staging_executors import supports
    from app.agentive.tooling.catalogue import build_tool_catalogue

    names = {t["name"] for t in build_tool_catalogue()}
    assert "integral_list_workspace_tools" in names
    assert "integral_call_workspace_tool" in names
    assert supports("call_workspace_tool")
