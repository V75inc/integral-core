"""A completed dispatch must not hide a workspace tool's explicit refusal."""

from __future__ import annotations

import pytest

from app.agentive import staging
from app.agentive.services import staging_apply
from app.agentive.staging import create_staged_change, get_token
from app.agentive.staging_executors import _x_call_workspace_tool
from app.services.agent_scope import current_scope_workspace_id


@pytest.fixture(autouse=True)
def _clean_staging():
    staging._reset_for_tests()
    yield
    staging._reset_for_tests()


def _tool_output(monkeypatch, output):
    async def invoke(**kwargs):
        assert kwargs["agent"] is True
        assert kwargs["workspace_id"] == "n.Workspace.tool-outcomes"
        return output

    monkeypatch.setattr("app.services.workspace_tools.invoke_workspace_tool", invoke)


@pytest.mark.smoke
@pytest.mark.asyncio
@pytest.mark.parametrize(
    "output",
    [
        {
            "ok": False,
            "error": "Saved source changed",
            "file_id": "n.Attachment.retained",
        },
        {"ok": False, "message": "Reconcile uncertain execution"},
        {"ok": False, "reason": "Current record changed since review"},
        {"ok": False},
    ],
)
async def test_explicit_tool_refusal_preserves_output_and_is_not_success(
    monkeypatch, output
):
    _tool_output(monkeypatch, output)
    scope = current_scope_workspace_id.set("n.Workspace.tool-outcomes")
    try:
        result = await _x_call_workspace_tool(
            "u1", {"tool_key": "prepare_export", "input": {}}
        )
    finally:
        current_scope_workspace_id.reset(scope)
    assert result["ok"] is False
    assert result["error"] is True
    assert result["error_code"] == "workspace_tool_refused"
    assert result["message"] == output.get(
        "error",
        output.get("message", output.get("reason", "Workspace tool did not complete")),
    )
    assert result["output"] == output


@pytest.mark.smoke
@pytest.mark.asyncio
@pytest.mark.parametrize(
    "output", [{"ok": True}, {"rows": []}, {"ok": 0}, [], None, False]
)
async def test_legacy_data_and_success_outputs_keep_their_contract(monkeypatch, output):
    _tool_output(monkeypatch, output)
    scope = current_scope_workspace_id.set("n.Workspace.tool-outcomes")
    try:
        result = await _x_call_workspace_tool(
            "u1", {"tool_key": "prepare_export", "input": {}}
        )
    finally:
        current_scope_workspace_id.reset(scope)
    assert result == {"ok": True, "tool_key": "prepare_export", "output": output}


@pytest.mark.smoke
@pytest.mark.asyncio
async def test_approval_records_refusal_without_consuming_or_reexecuting(monkeypatch):
    output = {
        "ok": False,
        "error": "Saved source changed",
        "file_id": "n.Attachment.retained",
    }
    _tool_output(monkeypatch, output)
    scope = current_scope_workspace_id.set("n.Workspace.tool-outcomes")
    try:
        sc = await create_staged_change(
            user_id="u1",
            session_id=None,
            kind="call_workspace_tool",
            summary="Prepare export",
            diff_human="Prepare saved source",
            diff_machine={"tool_key": "prepare_export"},
            payload={"tool_key": "prepare_export", "input": {}},
        )
    finally:
        current_scope_workspace_id.reset(scope)
    calls = []

    async def dispatch(**kwargs):
        calls.append(kwargs)
        tok = current_scope_workspace_id.set(kwargs["preferred_workspace_id"])
        try:
            return await _x_call_workspace_tool(kwargs["user_id"], kwargs["payload"])
        finally:
            current_scope_workspace_id.reset(tok)

    monkeypatch.setattr(staging_apply, "_dispatch_kind", dispatch)
    response = await staging_apply.bless_and_execute(user_id="u1", token=sc.token)
    assert response["consumed"] is False
    assert response["execute_result"]["output"] == output
    current = await get_token(sc.token)
    assert current.state == "blessed"
    assert current.last_error["message"] == "Saved source changed"
    assert current.last_error["error_code"] == "workspace_tool_refused"
    assert len(calls) == 1
