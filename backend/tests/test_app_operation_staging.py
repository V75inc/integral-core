"""Agent App commands require review across wrapper and typed capability paths."""

from __future__ import annotations

import copy
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.agentive.services.app_operation_staging import prepare_agent_app_operation
from app.services.app_operations import dispatch as operations
from app.services.app_operations.registry import (
    clear_workspace_operations,
    register_app_operations,
)

WS, APP = "ws-review-command", "n.WorkspaceApp.review-command"
pytestmark = pytest.mark.smoke


@pytest.fixture
def operation_contract(monkeypatch):
    spec = {
        "key": "retain_findings",
        "name": "Retain findings",
        "kind": "execute",
        "handler_ref": "test.findings",
        "policy_action": "entry.create",
        "input_schema": {
            "type": "object",
            "properties": {"title": {"type": "string"}},
            "required": ["title"],
            "additionalProperties": False,
        },
        "output_schema": {"type": "object"},
    }
    app = SimpleNamespace(id=APP, workspace_id=WS, lifecycle_state="active")
    monkeypatch.setattr(operations.App, "get", AsyncMock(return_value=app))
    monkeypatch.setattr(operations, "resolve_role", AsyncMock(return_value="owner"))
    monkeypatch.setattr(
        operations, "can_access_workspace", AsyncMock(return_value="owner")
    )
    from app.schemas.policy import Decision

    monkeypatch.setattr(
        operations,
        "policy_evaluate",
        AsyncMock(return_value=Decision(allowed=True, reason="review-test")),
    )
    handler = AsyncMock(return_value={"ok": True})
    monkeypatch.setattr(operations, "resolve_handler", lambda _ref: handler)
    register_app_operations(WS, APP, "reference-review-app", [spec])
    yield spec, handler, app
    clear_workspace_operations(WS)


def _args(**updates):
    return {
        "app_id": APP,
        "operation_key": "retain_findings",
        "input": {"title": "Checked vendor page"},
        **updates,
    }


@pytest.mark.asyncio
async def test_prepare_mutation_validates_without_handler_and_freezes_input(
    operation_contract,
):
    _, handler, _ = operation_contract
    args = _args(idempotency_key="same-call")
    staged = await prepare_agent_app_operation(args, principal_id="u1", workspace_id=WS)
    assert staged["kind"] == "invoke_app_operation"
    assert "Checked vendor page" in staged["diff_human"]
    assert "```json\n" in staged["diff_human"]
    assert staged["diff_human"].endswith("\n```")
    assert staged["payload"]["declaration_fingerprint"]
    args["input"]["title"] = "Changed after preparation"
    assert staged["payload"]["input"]["title"] == "Checked vendor page"
    handler.assert_not_awaited()


@pytest.mark.asyncio
async def test_proposal_identity_does_not_alias_other_input(operation_contract):
    first = await prepare_agent_app_operation(
        _args(idempotency_key="same-call"), principal_id="u1", workspace_id=WS
    )
    again = await prepare_agent_app_operation(
        _args(idempotency_key="same-call"), principal_id="u1", workspace_id=WS
    )
    changed = await prepare_agent_app_operation(
        _args(idempotency_key="same-call", input={"title": "A different source"}),
        principal_id="u1",
        workspace_id=WS,
    )
    assert first["payload"]["idempotency_key"] == again["payload"]["idempotency_key"]
    assert first["payload"]["idempotency_key"] != changed["payload"]["idempotency_key"]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "failure", ["foreign_workspace", "invalid_input", "policy_denied", "paused"]
)
async def test_prepare_refuses_before_any_handler(
    operation_contract, monkeypatch, failure
):
    _, handler, app = operation_contract
    args = _args()
    if failure == "foreign_workspace":
        app.workspace_id = "another-workspace"
    elif failure == "invalid_input":
        args["input"] = {"invented_title": "wrong alias"}
    elif failure == "paused":
        app.lifecycle_state = "paused"
    else:
        from app.schemas.policy import Decision

        monkeypatch.setattr(
            operations,
            "policy_evaluate",
            AsyncMock(return_value=Decision(allowed=False, reason="revoked")),
        )
    with pytest.raises(Exception):
        await prepare_agent_app_operation(args, principal_id="u1", workspace_id=WS)
    handler.assert_not_awaited()


@pytest.mark.asyncio
async def test_read_executes_immediately_without_staging(operation_contract):
    spec, handler, _ = operation_contract
    spec = {**spec, "kind": "read", "policy_action": "app.read"}
    register_app_operations(WS, APP, "reference-review-app", [spec])
    result = await prepare_agent_app_operation(
        _args(), principal_id="u1", workspace_id=WS
    )
    assert result["_no_stage"] is True
    assert result["data"]["output"] == {"ok": True}
    handler.assert_awaited_once()


@pytest.mark.asyncio
async def test_review_execution_rejects_changed_declaration(operation_contract):
    spec, handler, _ = operation_contract
    prepared = await prepare_agent_app_operation(
        _args(), principal_id="u1", workspace_id=WS
    )
    register_app_operations(
        WS, APP, "reference-review-app", [{**spec, "handler_ref": "other.handler"}]
    )
    with pytest.raises(Exception, match="changed since review"):
        await operations.invoke_app_operation(
            user_id="u1",
            workspace_id=WS,
            app_id=APP,
            operation_key=spec["key"],
            payload=prepared["payload"]["input"],
            idempotency_key=prepared["payload"]["operation_idempotency_key"],
            expected_declaration_fingerprint=prepared["payload"][
                "declaration_fingerprint"
            ],
        )
    handler.assert_not_awaited()


@pytest.mark.asyncio
async def test_review_execution_rechecks_current_policy(
    operation_contract, monkeypatch
):
    spec, handler, _ = operation_contract
    prepared = await prepare_agent_app_operation(
        _args(), principal_id="u1", workspace_id=WS
    )
    from app.schemas.policy import Decision

    monkeypatch.setattr(
        operations,
        "policy_evaluate",
        AsyncMock(
            return_value=Decision(allowed=False, reason="revoked after preparation")
        ),
    )
    with pytest.raises(Exception, match="Access denied"):
        await operations.invoke_app_operation(
            user_id="u1",
            workspace_id=WS,
            app_id=APP,
            operation_key=spec["key"],
            payload=prepared["payload"]["input"],
            idempotency_key=prepared["payload"]["operation_idempotency_key"],
            expected_declaration_fingerprint=prepared["payload"][
                "declaration_fingerprint"
            ],
        )
    handler.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("source", ["core", "app"])
@pytest.mark.parametrize("origin", ["chat", "mcp"])
async def test_both_agent_capability_paths_create_pending_review(
    operation_contract, monkeypatch, source, origin
):
    from app.agentive import staging
    from app.agentive.services.capability_adapters import dispatch_capability
    from app.agentive.tooling import dispatch
    from app.schemas.capability_broker import CapabilityInvocation

    _, handler, _ = operation_contract
    monkeypatch.setattr(dispatch, "enforce_tool_policy", AsyncMock(return_value=None))
    monkeypatch.setattr(
        "app.services.chat_threads.design_proposed_pending",
        AsyncMock(return_value=False),
    )
    captured = []

    async def create(**kwargs):
        captured.append(copy.deepcopy(kwargs))
        return SimpleNamespace(
            to_dict=lambda: {"_kind": "staged_change", "token": "review-token"}
        )

    monkeypatch.setattr(staging, "create_staged_change", create)
    inv = CapabilityInvocation(
        run_id="run-review",
        principal_id="u1",
        workspace_id=WS,
        origin=origin,
        source=source,
        op_class="execute",
        app_id=APP,
        capability_key=(
            "integral_invoke_app_operation" if source == "core" else "retain_findings"
        ),
        arguments=_args() if source == "core" else _args()["input"],
    )
    output = await dispatch_capability(inv, {"kind": "execute"})
    assert output["_kind"] == "staged_change"
    assert len(captured) == 1
    assert captured[0]["payload"]["app_id"] == APP
    assert captured[0]["user_id"] == "u1"
    assert captured[0]["workspace_id"] == WS
    handler.assert_not_awaited()


@pytest.mark.asyncio
async def test_executor_uses_bound_identity_and_reviewed_idempotency(
    operation_contract, monkeypatch
):
    from app.agentive.staging_executors import _x_invoke_app_operation
    from app.services.agent_scope import current_scope_workspace_id

    staged = await prepare_agent_app_operation(
        _args(idempotency_key="retained-save"), principal_id="u1", workspace_id=WS
    )
    call = AsyncMock(return_value={"operation_receipt": {"status": "succeeded"}})
    monkeypatch.setattr(operations, "invoke_app_operation", call)
    token = current_scope_workspace_id.set(WS)
    try:
        output = await _x_invoke_app_operation("u1", staged["payload"])
    finally:
        current_scope_workspace_id.reset(token)
    assert output["operation_receipt"]["status"] == "succeeded"
    assert call.await_args.kwargs["user_id"] == "u1"
    assert call.await_args.kwargs["workspace_id"] == WS
    assert call.await_args.kwargs["idempotency_key"] == "retained-save"
    assert (
        call.await_args.kwargs["expected_declaration_fingerprint"]
        == staged["payload"]["declaration_fingerprint"]
    )


@pytest.mark.asyncio
async def test_rejected_review_cannot_be_consumed_after_restart(operation_contract):
    from app.agentive import staging
    from app.services.agent_scope import current_scope_workspace_id

    _, handler, _ = operation_contract
    staging._reset_for_tests()
    token = current_scope_workspace_id.set(WS)
    try:
        prepared = await prepare_agent_app_operation(
            _args(), principal_id="u1", workspace_id=WS
        )
        pending = await staging.create_staged_change(
            user_id="u1", session_id="review-session", **prepared
        )
        assert pending.state == "pending"
        await staging.revoke_token(user_id="u1", token=pending.token)
        staging._tokens.clear()
        with pytest.raises(staging.StagingError):
            await staging.consume_token(
                user_id="u1",
                token=pending.token,
                expected_kind="invoke_app_operation",
            )
        handler.assert_not_awaited()
    finally:
        current_scope_workspace_id.reset(token)
        staging._reset_for_tests()


@pytest.mark.asyncio
async def test_read_grant_refuses_a_current_mutation(operation_contract):
    from app.agentive.services.capability_adapters import (
        AdapterError,
        dispatch_capability,
    )
    from app.schemas.capability_broker import CapabilityInvocation

    _, handler, _ = operation_contract
    invocation = CapabilityInvocation(
        run_id="former-read",
        principal_id="u1",
        workspace_id=WS,
        origin="chat",
        source="app",
        op_class="read",
        app_id=APP,
        capability_key="retain_findings",
        arguments=_args()["input"],
    )
    with pytest.raises(AdapterError, match="execution class changed"):
        await dispatch_capability(invocation, {"kind": "read"})
    handler.assert_not_awaited()


@pytest.mark.asyncio
async def test_review_execution_rejects_replaced_package(operation_contract):
    spec, handler, app = operation_contract
    prepared = await prepare_agent_app_operation(
        _args(), principal_id="u1", workspace_id=WS
    )
    app.installed_artifact_fingerprint = "a-new-package-with-the-same-version"
    with pytest.raises(Exception, match="changed since review"):
        await operations.invoke_app_operation(
            user_id="u1",
            workspace_id=WS,
            app_id=APP,
            operation_key=spec["key"],
            payload=prepared["payload"]["input"],
            idempotency_key=prepared["payload"]["operation_idempotency_key"],
            expected_declaration_fingerprint=prepared["payload"][
                "declaration_fingerprint"
            ],
        )
    handler.assert_not_awaited()
