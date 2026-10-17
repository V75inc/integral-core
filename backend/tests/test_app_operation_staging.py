"""Agent App commands require review across wrapper and typed capability paths."""

from __future__ import annotations

import copy
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.agentive.services.app_operation_staging import (
    _input_review_labels,
    prepare_agent_app_operation,
)
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


def test_review_labels_preserve_app_titles_without_execution_schema_or_authority():
    schema = {
        "type": "object",
        "properties": {
            "sources": {
                "title": "Chosen material",
                "type": "array",
                "items": {
                    "properties": {"body": {"title": "Exact passage", "type": "string"}}
                },
            }
        },
        "required": ["sources"],
        "description": "This is not an execution instruction.",
    }
    original = copy.deepcopy(schema)
    assert _input_review_labels(schema) == {
        "properties": {
            "sources": {
                "title": "Chosen material",
                "items": {"properties": {"body": {"title": "Exact passage"}}},
            }
        }
    }
    assert schema == original
    wide = {"properties": {str(i): {"title": "Field"} for i in range(1000)}}
    assert len(_input_review_labels(wide)["properties"]) <= 256


def test_review_order_array_survives_unordered_object_roundtrip():
    schema = {
        "x-review-order": ["z_main", "a_source", "z_main", None, "missing"],
        "properties": {
            "z_main": {"title": "Main choice"},
            "a_source": {"title": "Source detail"},
        },
    }
    # Reordered object members reproduce the relevant JSONB transport behavior.
    reordered = json.loads(json.dumps(schema, sort_keys=True))
    projected = _input_review_labels(reordered)
    assert list(projected["properties"]) == ["a_source", "z_main"]
    assert projected["property_order"] == ["z_main", "a_source"]
    persisted = json.loads(json.dumps(projected, sort_keys=True))
    assert persisted["property_order"] == ["z_main", "a_source"]
    assert reordered == json.loads(json.dumps(schema, sort_keys=True))


@pytest.mark.asyncio
async def test_review_presentation_does_not_enter_execution_payload(operation_contract):
    spec, handler, _ = operation_contract
    spec["input_schema"]["properties"]["title"]["title"] = "Finding title"
    spec["input_schema"]["x-review-order"] = ["title"]
    register_app_operations(WS, APP, "reference-review-app", [spec])
    staged = await prepare_agent_app_operation(
        _args(), principal_id="u1", workspace_id=WS
    )
    assert staged["diff_machine"]["review"]["input_labels"] == {
        "properties": {"title": {"title": "Finding title"}},
        "property_order": ["title"],
    }
    assert "review" not in staged["payload"]
    assert staged["diff_machine"]["input"] == {"title": "Checked vendor page"}
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
async def test_read_executes_immediately_without_staging(
    test_user, request, monkeypatch
):
    from tests.contract.operation_fixtures import owned_operation_app

    workspace, app = await owned_operation_app(test_user, request)
    spec = {
        "key": "retain_findings",
        "kind": "read",
        "policy_action": "app.read",
        "handler_ref": "test.findings",
    }
    handler = AsyncMock(return_value={"ok": True})
    monkeypatch.setattr(operations, "resolve_handler", lambda _ref: handler)
    register_app_operations(workspace.id, app.id, "reference-review-app", [spec])
    result = await prepare_agent_app_operation(
        _args(app_id=app.id), principal_id=test_user.id, workspace_id=workspace.id
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


@pytest.mark.asyncio
async def test_review_execution_rejects_changed_resolved_targets(
    operation_contract, monkeypatch
):
    from app.api.errors import BadRequestError

    _, handler, _ = operation_contract
    monkeypatch.setattr(
        operations,
        "resolve_action_payload",
        AsyncMock(
            side_effect=[
                {"title": "Checked vendor page", "entry_id": "first-target"},
                {"title": "Checked vendor page", "entry_id": "replacement-target"},
            ]
        ),
    )
    staged = await prepare_agent_app_operation(
        _args(), principal_id="u1", workspace_id=WS
    )
    assert staged["payload"]["input"] == {"title": "Checked vendor page"}
    assert staged["payload"]["resolved_input"]["entry_id"] == "first-target"
    with pytest.raises(BadRequestError, match="targets changed"):
        await operations.invoke_app_operation(
            user_id="u1",
            workspace_id=WS,
            app_id=APP,
            operation_key="retain_findings",
            payload=staged["payload"]["input"],
            idempotency_key=staged["payload"]["operation_idempotency_key"],
            expected_declaration_fingerprint=staged["payload"][
                "declaration_fingerprint"
            ],
            expected_resolved_input=staged["payload"]["resolved_input"],
        )
    handler.assert_not_awaited()


@pytest.mark.asyncio
async def test_proposal_identity_binds_resolved_targets(
    operation_contract, monkeypatch
):
    monkeypatch.setattr(
        operations,
        "resolve_action_payload",
        AsyncMock(
            side_effect=[
                {"title": "Checked vendor page", "entry_id": "first-target"},
                {"title": "Checked vendor page", "entry_id": "replacement-target"},
            ]
        ),
    )
    first = await prepare_agent_app_operation(
        _args(idempotency_key="same-call"), principal_id="u1", workspace_id=WS
    )
    second = await prepare_agent_app_operation(
        _args(idempotency_key="same-call"), principal_id="u1", workspace_id=WS
    )
    assert first["payload"]["idempotency_key"] != second["payload"]["idempotency_key"]


@pytest.mark.asyncio
async def test_changed_definition_at_effect_admission_never_calls_old_handler(
    test_user, request, monkeypatch
):
    from contextlib import asynccontextmanager

    from app.api.errors import BadRequestError
    from app.services.app_operations import execution_receipts
    from tests.contract.operation_fixtures import owned_operation_app

    workspace, app = await owned_operation_app(test_user, request)
    spec = {
        "key": "retain_findings",
        "kind": "execute",
        "handler_ref": "test.findings",
        "input_schema": {"type": "object"},
        "output_schema": {"type": "object"},
    }
    handler = AsyncMock(return_value={"ok": True})
    monkeypatch.setattr(operations, "resolve_handler", lambda _ref: handler)
    monkeypatch.setattr(
        operations,
        "get_app_operation",
        lambda *args: {**spec, "_definition_fingerprint": "reviewed-definition"},
    )

    @asynccontextmanager
    async def newly_admitted_definition(**kwargs):
        yield SimpleNamespace(fingerprint="replacement-definition")

    async def execute_once(**kwargs):
        return SimpleNamespace(result=await kwargs["execute"](), replayed=False)

    monkeypatch.setattr(
        "app.services.app_operation_authority.operation_permit",
        newly_admitted_definition,
    )
    monkeypatch.setattr(execution_receipts, "execute_operation_once", execute_once)
    staged = await prepare_agent_app_operation(
        _args(app_id=app.id), principal_id=test_user.id, workspace_id=workspace.id
    )
    with pytest.raises(BadRequestError, match="changed since review"):
        await operations.invoke_app_operation(
            user_id=test_user.id,
            workspace_id=workspace.id,
            app_id=app.id,
            operation_key="retain_findings",
            payload=staged["payload"]["input"],
            idempotency_key=staged["payload"]["operation_idempotency_key"],
            expected_declaration_fingerprint=staged["payload"][
                "declaration_fingerprint"
            ],
        )
    handler.assert_not_awaited()


@pytest.mark.postgres
@pytest.mark.asyncio
async def test_direct_opt_in_uses_authorized_dispatch_and_deduplicates(
    test_user, request, monkeypatch
):
    from tests.contract.operation_fixtures import owned_operation_app

    workspace, app = await owned_operation_app(test_user, request)
    spec = {
        "key": "retain_findings",
        "kind": "execute",
        "policy_action": "entry.create",
        "handler_ref": "test.findings",
        "agent_invocation": "direct",
    }
    from app.models.edges import HAS_APPLICATION_DEFINITION
    from app.models.nodes import ApplicationDefinition
    from app.services.application_definitions import definition_fingerprint

    manifest = {"app": {"operations": [spec]}}
    definition = await ApplicationDefinition.create(
        app_id=app.id,
        workspace_id=workspace.id,
        revision=1,
        status="active",
        canonical_manifest=manifest,
        manifest_fingerprint=definition_fingerprint(manifest),
    )
    await app.connect(definition, edge=HAS_APPLICATION_DEFINITION)
    app.active_definition_id, app.active_definition_revision = definition.id, 1
    await app.save()
    handler = AsyncMock(return_value={"ok": True})
    monkeypatch.setattr(operations, "resolve_handler", lambda _ref: handler)
    register_app_operations(workspace.id, app.id, "reference-review-app", [spec])
    args = _args(app_id=app.id, idempotency_key="direct-attempt")
    try:
        for _ in range(2):
            result = await prepare_agent_app_operation(
                args, principal_id=test_user.id, workspace_id=workspace.id
            )
            assert result["_no_stage"] is True
            assert result["data"]["output"] == {"ok": True}
        handler.assert_awaited_once()
        monkeypatch.setattr(operations, "resolve_role", AsyncMock(return_value=None))
        with pytest.raises(Exception):
            await prepare_agent_app_operation(
                _args(app_id=app.id, idempotency_key="after-revocation"),
                principal_id=test_user.id,
                workspace_id=workspace.id,
            )
        handler.assert_awaited_once()
    finally:
        clear_workspace_operations(workspace.id)


@pytest.mark.asyncio
async def test_direct_opt_in_rechecks_declaration_before_dispatch(
    operation_contract, monkeypatch
):
    from app.agentive.services import app_operation_staging

    spec, handler, _ = operation_contract
    spec = {**spec, "agent_invocation": "direct"}
    register_app_operations(WS, APP, "reference-review-app", [spec])

    async def changed_policy(**kwargs):
        register_app_operations(
            WS, APP, "reference-review-app", [{**spec, "agent_invocation": "staged"}]
        )
        return await operations.invoke_app_operation(**kwargs)

    monkeypatch.setattr(app_operation_staging, "invoke_app_operation", changed_policy)
    with pytest.raises(Exception, match="changed since review"):
        await prepare_agent_app_operation(_args(), principal_id="u1", workspace_id=WS)
    handler.assert_not_awaited()


@pytest.mark.parametrize("invocation", [None, "staged", "direct"])
def test_compile_agent_invocation_policy(invocation):
    from app.services.operational_model_compile import _parse_manifest_operations

    raw = {"key": "write", "kind": "execute", "handler_ref": "test.write"}
    if invocation is not None:
        raw["agent_invocation"] = invocation
    spec = _parse_manifest_operations([raw])[0]
    assert spec["agent_invocation"] == (invocation or "staged")


def test_compile_rejects_unknown_agent_invocation():
    from app.services.operational_model_compile import (
        OperationalModelValidationError,
        _parse_manifest_operations,
    )

    with pytest.raises(OperationalModelValidationError):
        _parse_manifest_operations(
            [{"key": "write", "kind": "execute", "agent_invocation": "unrestricted"}]
        )


@pytest.mark.asyncio
async def test_direct_invocation_does_not_override_policy_approval(
    operation_contract, monkeypatch
):
    from app.schemas.policy import Decision

    spec, handler, _ = operation_contract
    register_app_operations(
        WS, APP, "reference-review-app", [{**spec, "agent_invocation": "direct"}]
    )
    monkeypatch.setattr(
        operations,
        "policy_evaluate",
        AsyncMock(
            return_value=Decision(allowed=False, reason="requires_human_approval")
        ),
    )
    with pytest.raises(Exception):
        await prepare_agent_app_operation(_args(), principal_id="u1", workspace_id=WS)
    handler.assert_not_awaited()


def test_operation_discovery_exposes_review_policy():
    assert operations._public_operation({})["agent_invocation"] == "staged"
    assert (
        operations._public_operation({"agent_invocation": "direct"})["agent_invocation"]
        == "direct"
    )


def test_compile_retains_schedule_instruction_description():
    from app.services.operational_model_compile import _parse_manifest_agents

    agents = _parse_manifest_agents(
        [
            {
                "key": "review",
                "persona_ref": "personas/review.md",
                "skills": ["review"],
                "default_schedules": [
                    {
                        "name": "Daily review",
                        "cron": "0 8 * * *",
                        "skill": "review",
                        "description": "Review pending records",
                    }
                ],
            }
        ],
        {"review"},
    )
    assert agents[0]["default_schedules"][0]["description"] == "Review pending records"
