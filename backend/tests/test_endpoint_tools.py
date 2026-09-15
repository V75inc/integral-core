"""POST /api/tools/{key} — direct tool invocation (operator + MCP)."""

import pytest


@pytest.mark.asyncio
async def test_direct_tool_call(authenticated_client, test_user):
    from app.models.edges import IS_MEMBER_OF
    from app.models.nodes import Workspace
    from app.services.hooks.registry import (
        clear_workspace_registrations,
        register_workspace_tools,
    )
    from app.utils.time import utc_now_iso

    now = utc_now_iso()
    ws = await Workspace.create(
        kind="organization",
        workspace_type="company",
        name="WS Tool",
        name_fold="ws tool",
        created_at=now,
        updated_at=now,
    )
    await test_user.connect(ws, edge=IS_MEMBER_OF, role="owner", added_at=now)
    clear_workspace_registrations(ws.id)
    register_workspace_tools(
        ws.id,
        "test-bundle",
        [
            {
                "key": "echo",
                "handler_ref": "tests.fixtures.tool_echo:echo",
                "parameters_schema": {
                    "type": "object",
                    "required": ["msg"],
                    "properties": {"msg": {"type": "string"}},
                },
                "output_schema": {"type": "object"},
            }
        ],
    )
    resp = await authenticated_client.post(
        "/api/tools/echo",
        json={"input": {"msg": "hi"}},
        headers={"X-Integral-Scope": f"ws:{ws.id}"},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["output"]["echoed"] == "hi"
    assert body["tool_key"] == "echo"


@pytest.mark.asyncio
async def test_unknown_tool_returns_404(authenticated_client, test_user):
    from app.models.edges import IS_MEMBER_OF
    from app.models.nodes import Workspace
    from app.utils.time import utc_now_iso

    now = utc_now_iso()
    ws = await Workspace.create(
        kind="organization",
        workspace_type="company",
        name="WS Tool2",
        name_fold="ws tool2",
        created_at=now,
        updated_at=now,
    )
    await test_user.connect(ws, edge=IS_MEMBER_OF, role="owner", added_at=now)
    resp = await authenticated_client.post(
        "/api/tools/nonexistent",
        json={"input": {}},
        headers={"X-Integral-Scope": f"ws:{ws.id}"},
    )
    assert resp.status_code == 404, resp.text
