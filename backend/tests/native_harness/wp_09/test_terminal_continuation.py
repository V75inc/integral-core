"""Continue settled failed turns without replaying tools or granting authority."""

from datetime import datetime, timezone
from types import SimpleNamespace

import pytest
from pydantic_ai.messages import (
    ModelRequest,
    ModelResponse,
    TextPart,
    ToolCallPart,
    ToolReturnPart,
    UserPromptPart,
)
from pydantic_ai_harness.step_persistence import ToolEffectRecord

from app.agentive.harness.broker_tools import _idempotency_key
from app.agentive.harness.contracts import HarnessExecutionScope
from app.agentive.services.execution_runs import RunStep
from app.services.chat_providers.pydantic_ai_provider import (
    _resume_history,
    _settled_terminal_history,
)


def _scope(run_id):
    return HarnessExecutionScope(
        tenant_id="workspace-a",
        principal_id="user-a",
        workspace_id="workspace-a",
        thread_id="thread-a",
        session_id="session-a",
        run_id=run_id,
        permission_revision="permissions-v1",
        capability_version="capabilities-v1",
    )


def _run(**changes):
    return SimpleNamespace(
        **{
            "run_id": "failed-run",
            "status": "failed",
            "user_id": "user-a",
            "workspace_id": "workspace-a",
            "thread_id": "thread-a",
            "provider_id": "integral_native",
            "capability_snapshot": {
                "capabilities": [
                    {"name": "read_tool", "op_class": "read"},
                    {"name": "write_tool", "op_class": "write"},
                ]
            },
            **changes,
        }
    )


def _receipt(tool, status):
    return SimpleNamespace(
        principal_id="user-a",
        workspace_id="workspace-a",
        capability_key=tool,
        status=status,
        idempotency_key=_idempotency_key("failed-run", "call-1", tool),
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("status", ["failed", "cancelled"])
@pytest.mark.parametrize("reason", ["write", "approval", "model", "missing_message"])
async def test_new_user_turn_continues_without_replaying_uncertain_terminal_work(
    monkeypatch, status, reason
):
    """New user input uses public text without settling or replaying old work."""
    from app.agentive.harness import adaptive, model_observations
    from app.agentive.work_models import WorkApproval

    effect = ToolEffectRecord(
        run_id="failed-run",
        tool_name="write_tool",
        tool_call_id="call-1",
        status="started",
    )
    effects = [effect] if reason == "write" else []
    approvals = [SimpleNamespace(status="pending")] if reason == "approval" else []
    now = datetime.now(timezone.utc)

    async def observations(**kwargs):
        return (
            [
                SimpleNamespace(
                    request_id="unknown",
                    outcome="dispatch_intent",
                    observed_at=now,
                    dispatched_at=now,
                )
            ]
            if reason == "model"
            else []
        )

    async def pending(query):
        return approvals

    async def no_receipts(query):
        return []

    async def public_messages(thread):
        assert thread.id == "thread-a"
        return [
            SimpleNamespace(
                id="old",
                role="assistant",
                parts=[
                    {"type": "text", "text": "Earlier reply"},
                    {"type": "tool-call", "tool_name": "write_tool"},
                ],
            ),
            SimpleNamespace(
                id="current",
                role="user",
                parts=[{"type": "text", "text": "Continue here"}],
            ),
        ]

    class Store:
        async def list_unresolved_tool_effects(self, **kwargs):
            return effects

        async def list_tool_effects(self, **kwargs):
            return effects

        async def latest_snapshot(self, **kwargs):
            return None

    monkeypatch.setattr(
        model_observations, "list_model_request_observations", observations
    )
    monkeypatch.setattr(WorkApproval, "find", pending)
    monkeypatch.setattr(RunStep, "find", no_receipts)
    monkeypatch.setattr(adaptive, "recent_public_messages", public_messages)
    recovery_state = {}
    history = await _resume_history(
        _scope("next-run"),
        SimpleNamespace(last_run_id="failed-run"),
        Store(),
        previous_run=_run(status=status),
        thread=SimpleNamespace(id="thread-a"),
        current_message_id="current",
        allow_fresh_turn=True,
        recovery_state=recovery_state,
    )
    assert history[0].parts[0].content == "Earlier reply"
    assert len(history) == 2
    assert "Do not replay prior tool calls" in history[-1].parts[0].content
    assert not any(
        isinstance(part, (ToolCallPart, ToolReturnPart))
        for message in history
        for part in message.parts
    )
    assert recovery_state == {"public_dialogue_restored": True}
    assert effect.status == "started"
    if approvals:
        assert approvals[0].status == "pending"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "status,workspace,allow_fresh_turn",
    [
        ("running", "workspace-a", True),
        ("failed", "other-workspace", True),
        ("failed", "workspace-a", False),
    ],
)
async def test_fresh_turn_does_not_bypass_live_scope_or_host_recovery_guards(
    monkeypatch, status, workspace, allow_fresh_turn
):
    """Text fallback cannot authorize overlap, foreign state, or host retries."""
    from app.agentive.harness import model_observations
    from app.agentive.work_models import WorkApproval
    from app.api.errors import ResourceConflictError

    effect = ToolEffectRecord(
        run_id="failed-run",
        tool_name="write_tool",
        tool_call_id="call-1",
        status="started",
    )

    async def observations(**kwargs):
        return []

    async def no_rows(query):
        return []

    class Store:
        async def list_unresolved_tool_effects(self, **kwargs):
            return [effect]

        async def list_tool_effects(self, **kwargs):
            return [effect]

        async def latest_snapshot(self, **kwargs):
            return None

    monkeypatch.setattr(
        model_observations, "list_model_request_observations", observations
    )
    monkeypatch.setattr(WorkApproval, "find", no_rows)
    monkeypatch.setattr(RunStep, "find", no_rows)
    with pytest.raises(ResourceConflictError):
        await _resume_history(
            _scope("next-run"),
            SimpleNamespace(last_run_id="failed-run"),
            Store(),
            previous_run=_run(status=status, workspace_id=workspace),
            thread=SimpleNamespace(id="thread-a"),
            current_message_id="current",
            allow_fresh_turn=allow_fresh_turn,
        )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "code",
    [
        "invalid_scaffold_plan",
        "plan_differs_from_design",
        "scaffold_plan_validation_failed",
        "incomplete_scaffold",
    ],
)
async def test_pre_effect_rejection_recovers_without_replaying_build(monkeypatch, code):
    from app.agentive.harness import model_observations
    from app.agentive.work_models import WorkApproval

    tool = "integral_build_approved_design"
    receipt = _receipt(tool, "failed")
    receipt.capability_source = "core"
    receipt.adapter_error_code = code
    effect = ToolEffectRecord(
        run_id="failed-run", tool_name=tool, tool_call_id="call-1", status="started"
    )
    audit = []

    async def empty(**kwargs):
        return []

    async def rows(query):
        return [receipt]

    async def no_rows(query):
        return []

    class Store:
        async def list_unresolved_tool_effects(self, **kwargs):
            return [effect]

        async def list_tool_effects(self, **kwargs):
            return [effect]

        async def latest_snapshot(self, **kwargs):
            return None

        async def record_tool_effect(self, record):
            audit.append(record)

    monkeypatch.setattr(model_observations, "list_model_request_observations", empty)
    monkeypatch.setattr(WorkApproval, "find", no_rows)
    monkeypatch.setattr(RunStep, "find", rows)
    prompt = ModelRequest(parts=[UserPromptPart(content="yes")])
    history = await _resume_history(
        _scope("next-run"),
        SimpleNamespace(last_run_id="failed-run"),
        Store(),
        recovery_message=prompt,
        previous_run=_run(),
    )
    assert history[0] == prompt
    assert "not applied" in history[-1].parts[0].content
    assert len(audit) == 1
    assert audit[0].status == "failed"
    assert "no replay" in audit[0].effect_summary.lower()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "source,code",
    [
        ("connector", "invalid_scaffold_plan"),
        ("core", "scaffold_plan_stage_failed"),
        ("core", "partial_build_requires_repair"),
        ("core", "unknown"),
    ],
)
async def test_pre_effect_recovery_rejects_foreign_or_partial_failure(
    monkeypatch, source, code
):
    receipt = _receipt("integral_build_approved_design", "failed")
    receipt.capability_source = source
    receipt.adapter_error_code = code

    async def rows(query):
        return [receipt]

    monkeypatch.setattr(RunStep, "find", rows)
    assert (
        await _settled_terminal_history(
            _scope("next-run"),
            _run(),
            object(),
            None,
            [],
            recovery_message=ModelRequest(parts=[UserPromptPart(content="yes")]),
            checkpoint_run_id=None,
        )
        is None
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "tool,status,allowed",
    [
        ("read_tool", "succeeded", True),
        ("read_tool", "failed", True),
        ("read_tool", "running", True),
        ("write_tool", "succeeded", True),
        ("write_tool", "denied", True),
        ("write_tool", "failed", False),
        ("write_tool", "running", False),
        ("write_tool", "waiting_for_human", False),
    ],
)
@pytest.mark.parametrize("run_status", ["succeeded", "failed", "cancelled"])
async def test_terminal_continuation_uses_scoped_original_receipts(
    monkeypatch, tool, status, allowed, run_status
):
    history = [
        ModelResponse(parts=[ToolCallPart(tool, {}, "call-1")]),
        ModelRequest(parts=[ToolReturnPart(tool, {"status": status}, "call-1")]),
    ]

    async def steps(_query):
        return [
            _receipt(tool, status),
            SimpleNamespace(capability_key="", kind="model", status="running"),
        ]

    class Store:
        async def latest_snapshot(self, **_kwargs):
            return SimpleNamespace(messages=history)

    monkeypatch.setattr(RunStep, "find", steps)
    restored = await _settled_terminal_history(
        _scope("next-run"),
        _run(status=run_status),
        Store(),
        SimpleNamespace(state="complete"),
        [SimpleNamespace(tool_name=tool, tool_call_id="call-1", status="completed")],
        recovery_message=None,
        checkpoint_run_id=None,
    )

    assert (restored is not None) is allowed
    if allowed:
        assert restored[:2] == history
        assert "must not be repeated" in restored[-1].parts[0].content


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "changes",
    [
        {"status": "running"},
        {"user_id": "another-user"},
        {"workspace_id": "another-workspace"},
        {"thread_id": "another-thread"},
        {"provider_id": "integral"},
    ],
)
async def test_terminal_continuation_rejects_running_or_foreign_runs(changes):
    assert (
        await _settled_terminal_history(
            _scope("next-run"),
            _run(**changes),
            object(),
            None,
            [],
            recovery_message=None,
            checkpoint_run_id=None,
        )
        is None
    )


@pytest.mark.asyncio
async def test_complete_snapshot_must_contain_every_mutation_receipt(monkeypatch):
    async def steps(_query):
        return [_receipt("write_tool", "succeeded")]

    class Store:
        async def latest_snapshot(self, **_kwargs):
            return SimpleNamespace(messages=[])

    monkeypatch.setattr(RunStep, "find", steps)
    assert (
        await _settled_terminal_history(
            _scope("next-run"),
            _run(),
            Store(),
            SimpleNamespace(state="complete"),
            [
                SimpleNamespace(
                    tool_name="write_tool", tool_call_id="absent", status="completed"
                )
            ],
            recovery_message=None,
            checkpoint_run_id=None,
        )
        is None
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "outcome", ["responded", "failed", "outcome_unknown", "dispatch_intent"]
)
async def test_paid_model_only_failure_can_accept_a_new_user_request(
    monkeypatch, outcome
):
    """Settled paid attempts are history; they are not automatically retried."""
    from app.agentive.harness import model_observations
    from app.agentive.work_models import WorkApproval

    now = datetime.now(timezone.utc)

    async def observations(**_kwargs):
        return [
            SimpleNamespace(
                request_id="paid-attempt",
                outcome=outcome,
                observed_at=now,
                dispatched_at=now,
            )
        ]

    async def empty(_query):
        return []

    class Store:
        async def list_unresolved_tool_effects(self, **_kwargs):
            return []

        async def list_tool_effects(self, **_kwargs):
            return []

        async def latest_snapshot(self, **_kwargs):
            return None

    monkeypatch.setattr(
        model_observations, "list_model_request_observations", observations
    )
    monkeypatch.setattr(WorkApproval, "find", empty)
    monkeypatch.setattr(RunStep, "find", empty)
    previous_prompt = ModelRequest(parts=[UserPromptPart(content="original request")])
    history = await _resume_history(
        _scope("next-run"),
        SimpleNamespace(last_run_id="failed-run"),
        Store(),
        recovery_message=previous_prompt,
        previous_run=_run(),
    )
    assert history[0] == previous_prompt
    assert "stopped without a final answer" in history[1].parts[0].content


@pytest.mark.asyncio
async def test_abandoned_read_is_audited_without_reissuing_the_tool(monkeypatch):
    from app.agentive.harness import model_observations
    from app.agentive.work_models import WorkApproval

    effect = ToolEffectRecord(
        run_id="failed-run",
        tool_name="read_tool",
        tool_call_id="unreturned-read",
        status="started",
    )
    abandoned = []

    async def empty(**_kwargs):
        return []

    async def no_rows(_query):
        return []

    class Store:
        async def list_unresolved_tool_effects(self, **_kwargs):
            return [effect]

        async def list_tool_effects(self, **_kwargs):
            return [effect]

        async def latest_snapshot(self, **_kwargs):
            return SimpleNamespace(
                idempotency_key="library-snapshot",
                state="complete",
                step_index=2,
                messages=[ModelRequest(parts=[UserPromptPart(content="old request")])],
            )

        async def record_tool_effect(self, record):
            abandoned.append(record)

    async def no_manifest(**_kwargs):
        return None

    monkeypatch.setattr(model_observations, "list_model_request_observations", empty)
    monkeypatch.setattr(WorkApproval, "find", no_rows)
    monkeypatch.setattr(RunStep, "find", no_rows)
    monkeypatch.setattr(
        "app.services.chat_providers.pydantic_ai_provider.load_checkpoint_manifest",
        no_manifest,
    )
    history = await _resume_history(
        _scope("next-run"),
        SimpleNamespace(last_run_id="failed-run"),
        Store(),
        previous_run=_run(),
    )
    assert history[0].parts[0].content == "old request"
    assert len(abandoned) == 1
    assert abandoned[0].tool_call_id == effect.tool_call_id
    assert abandoned[0].status == "failed"
    assert "no replay" in abandoned[0].effect_summary


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "tool,status,allowed",
    [
        ("read_tool", "succeeded", True),
        ("write_tool", "succeeded", True),
        ("write_tool", "running", False),
        ("write_tool", "failed", False),
    ],
)
@pytest.mark.parametrize("terminal_status", ["failed", "cancelled"])
async def test_terminal_request_unknown_billing_does_not_replay_settled_effects(
    monkeypatch,
    tool,
    status,
    allowed,
    terminal_status,
):
    """Accounting uncertainty survives; missing mutation receipts still block."""
    from app.agentive.harness import model_observations
    from app.agentive.work_models import WorkApproval
    from app.api.errors import ResourceConflictError

    now = datetime.now(timezone.utc)
    transitions = [
        SimpleNamespace(
            request_id="unfinished-model-request",
            outcome="dispatch_intent",
            observed_at=now,
            dispatched_at=now,
        )
    ]
    effect = SimpleNamespace(tool_name=tool, tool_call_id="call-1", status="completed")
    old_history = [
        ModelResponse(parts=[ToolCallPart(tool, {}, "call-1")]),
        ModelRequest(parts=[ToolReturnPart(tool, {"status": status}, "call-1")]),
    ]

    async def observations(**_kwargs):
        return transitions

    async def steps(_query):
        return [_receipt(tool, status)]

    async def empty(_query):
        return []

    class Store:
        async def list_unresolved_tool_effects(self, **_kwargs):
            return []

        async def list_tool_effects(self, **_kwargs):
            return [effect]

        async def latest_snapshot(self, **_kwargs):
            return SimpleNamespace(
                idempotency_key="snapshot",
                state="complete",
                step_index=2,
                messages=old_history,
            )

    monkeypatch.setattr(
        model_observations, "list_model_request_observations", observations
    )
    monkeypatch.setattr(WorkApproval, "find", empty)
    monkeypatch.setattr(RunStep, "find", steps)
    call = _resume_history(
        _scope("next-run"),
        SimpleNamespace(last_run_id="failed-run"),
        Store(),
        previous_run=_run(status=terminal_status),
    )
    if allowed:
        history = await call
        assert history[:2] == old_history
        assert "must not be repeated" in history[-1].parts[0].content
    else:
        with pytest.raises(ResourceConflictError):
            await call
    assert model_observations.unsettled_model_request_ids(transitions) == [
        "unfinished-model-request"
    ]


@pytest.mark.asyncio
async def test_completed_retry_run_continues_with_unknown_billing_without_replay(
    monkeypatch,
):
    """A successful run may contain uncertain failed attempts before its reply."""
    from app.agentive.harness import model_observations
    from app.agentive.work_models import WorkApproval

    now = datetime.now(timezone.utc)
    history = [
        ModelResponse(parts=[ToolCallPart("read_tool", {}, "call-1")]),
        ModelRequest(parts=[ToolReturnPart("read_tool", {"count": 3}, "call-1")]),
        ModelResponse(parts=[TextPart("Read completed")]),
    ]

    async def observations(**_kwargs):
        return [
            SimpleNamespace(
                request_id="timed-out-attempt",
                outcome="outcome_unknown",
                observed_at=now,
                dispatched_at=now,
            )
        ]

    async def approvals(_query):
        return []

    async def steps(_query):
        return [_receipt("read_tool", "succeeded")]

    class Store:
        async def list_unresolved_tool_effects(self, **_kwargs):
            return []

        async def list_tool_effects(self, **_kwargs):
            return [
                SimpleNamespace(
                    tool_name="read_tool", tool_call_id="call-1", status="completed"
                )
            ]

        async def latest_snapshot(self, **_kwargs):
            return SimpleNamespace(messages=history)

    monkeypatch.setattr(
        model_observations, "list_model_request_observations", observations
    )
    monkeypatch.setattr(WorkApproval, "find", approvals)
    monkeypatch.setattr(RunStep, "find", steps)
    restored = await _resume_history(
        _scope("next-run"),
        SimpleNamespace(last_run_id="failed-run"),
        Store(),
        previous_run=_run(status="succeeded"),
    )
    assert restored[:3] == history
    assert "previous turn completed" in restored[-1].parts[0].content.lower()
    assert "must not be repeated" in restored[-1].parts[0].content


@pytest.mark.asyncio
async def test_rejected_build_repairs_missing_tool_return_from_original_receipt(
    monkeypatch,
):
    tool = "integral_build_approved_design"
    receipt = _receipt(tool, "failed")
    receipt.capability_source = "core"
    receipt.adapter_error_code = "invalid_scaffold_plan"

    async def rows(query):
        return [receipt]

    class Store:
        async def latest_snapshot(self, **kwargs):
            return SimpleNamespace(
                messages=[ModelResponse(parts=[ToolCallPart(tool, {}, "call-1")])]
            )

    monkeypatch.setattr(RunStep, "find", rows)
    history = await _settled_terminal_history(
        _scope("next-run"),
        _run(),
        Store(),
        SimpleNamespace(state="complete"),
        [SimpleNamespace(tool_name=tool, tool_call_id="call-1", status="failed")],
        recovery_message=None,
        checkpoint_run_id=None,
    )
    returned = history[-2].parts[0]
    assert isinstance(returned, ToolReturnPart)
    assert returned.tool_call_id == "call-1"
    assert returned.content["applied"] is False
    assert returned.content["error_code"] == "invalid_scaffold_plan"


@pytest.mark.asyncio
async def test_exhausted_plan_corrections_produce_truthful_final_answer():
    from app.services.chat_providers.pydantic_ai_provider import (
        _scaffold_completion_validator,
    )

    validator = _scaffold_completion_validator(
        {
            "scaffold_coverage_attempted": True,
            "build_validation_failure": "Date Spent must retain its approved field key.",
        }
    )
    answer = await validator(None, "Everything is ready!")
    assert "No build changes were applied" in answer
    assert "Date Spent" in answer
    assert "Everything is ready" not in answer


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "tool,code",
    [
        ("integral_build_approved_design", "build_already_attempted"),
        ("integral_propose_design", "approved_design_immutable"),
    ],
)
async def test_completed_core_guard_return_recovers_without_broker_receipt(
    monkeypatch, tool, code
):
    async def rows(query):
        return []

    history = [
        ModelResponse(
            parts=[ToolCallPart(tool_name=tool, args={}, tool_call_id="guard-call")]
        ),
        ModelRequest(
            parts=[
                ToolReturnPart(
                    tool_name=tool,
                    tool_call_id="guard-call",
                    content={"error": True, "error_code": code, "retryable": False},
                )
            ]
        ),
    ]

    class Store:
        async def continue_messages(self, **kwargs):
            return history

    async def checkpoint(store, run_id):
        return history

    monkeypatch.setattr(RunStep, "find", rows)
    monkeypatch.setattr(
        "app.services.chat_providers.pydantic_ai_provider._continue_from_checkpoint",
        checkpoint,
    )
    effect = ToolEffectRecord(
        run_id="failed-run",
        tool_name=tool,
        tool_call_id="guard-call",
        status="completed",
    )
    result = await _settled_terminal_history(
        _scope("next-run"),
        _run(
            capability_snapshot={
                "capabilities": [{"name": tool, "op_class": "propose"}]
            }
        ),
        Store(),
        object(),
        [effect],
        recovery_message=None,
        checkpoint_run_id=None,
    )
    assert result is not None
    assert result[:2] == history[:2]


@pytest.mark.parametrize(
    "mismatch", ["tool", "code", "unresolved", "missing_call", "retryable"]
)
def test_guard_recovery_requires_exact_original_completed_return(mismatch):
    from app.agentive.harness.effect_reconciliation import settled_core_guard_calls

    tool = "integral_build_approved_design"
    code = "build_already_attempted"
    status = "completed"
    if mismatch == "tool":
        tool = "connector_build"
    if mismatch == "code":
        code = "unknown"
    if mismatch == "unresolved":
        status = "started"
    history = [
        ModelResponse(
            parts=[ToolCallPart(tool_name=tool, args={}, tool_call_id="guard-call")]
        )
    ]
    if mismatch == "missing_call":
        history = []
    history.append(
        ModelRequest(
            parts=[
                ToolReturnPart(
                    tool_name=tool,
                    tool_call_id="guard-call",
                    content={
                        "error": True,
                        "error_code": code,
                        "retryable": mismatch == "retryable",
                    },
                )
            ]
        )
    )
    effect = ToolEffectRecord(
        run_id="failed-run", tool_name=tool, tool_call_id="guard-call", status=status
    )
    assert settled_core_guard_calls(history, [effect]) == set()


@pytest.mark.asyncio
@pytest.mark.parametrize("status", ["succeeded", "denied"])
async def test_original_core_receipt_repairs_started_write_with_missing_return(
    monkeypatch, status
):
    tool = "write_tool"
    receipt = _receipt(tool, status)
    receipt.capability_source = "core"

    async def rows(query):
        return [receipt]

    audit = []

    class Store:
        async def latest_snapshot(self, **kwargs):
            return SimpleNamespace(
                messages=[ModelResponse(parts=[ToolCallPart(tool, {}, "call-1")])]
            )

        async def record_tool_effect(self, record):
            audit.append(record)

    monkeypatch.setattr(RunStep, "find", rows)
    history = await _settled_terminal_history(
        _scope("next-run"),
        _run(),
        Store(),
        SimpleNamespace(state="complete"),
        [
            ToolEffectRecord(
                run_id="failed-run",
                tool_name=tool,
                tool_call_id="call-1",
                status="started",
            )
        ],
        recovery_message=None,
        checkpoint_run_id=None,
    )
    result = history[-2].parts[0]
    assert isinstance(result, ToolReturnPart)
    assert result.content["recorded_status"] == status
    assert result.content["observation_only"] is True
    assert "not current state" in result.content["message"]
    assert audit[0].status == "completed"
    assert "no replay" in audit[0].effect_summary
