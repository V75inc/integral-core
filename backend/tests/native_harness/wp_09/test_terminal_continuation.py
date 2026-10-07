"""Continue settled failed turns without replaying tools or granting authority."""

from datetime import datetime, timezone
from types import SimpleNamespace

import pytest
from pydantic_ai.messages import (
    ModelRequest,
    ModelResponse,
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
async def test_terminal_continuation_uses_scoped_original_receipts(
    monkeypatch, tool, status, allowed
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
        _run(),
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
async def test_paid_model_only_failure_can_accept_a_new_user_request(monkeypatch):
    """Settled paid attempts are history; they are not automatically retried."""
    from app.agentive.harness import model_observations
    from app.agentive.work_models import WorkApproval

    now = datetime.now(timezone.utc)

    async def observations(**_kwargs):
        return [
            SimpleNamespace(
                request_id="paid-attempt",
                outcome="responded",
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
async def test_cancelled_request_unknown_billing_does_not_replay_settled_effects(
    monkeypatch,
    tool,
    status,
    allowed,
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
        previous_run=_run(status="cancelled"),
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
