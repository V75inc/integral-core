"""Verify a native deferred call against Core's staged capability evidence.

This read-side check is not itself a park, decision, or authorization to
resume. The eventual park and resume transitions must recheck the same facts
under their own transactional authority boundaries.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from jvspatial.core.context import get_default_context

from app.agentive.harness.broker_tools import _idempotency_key
from app.agentive.harness.contracts import HarnessExecutionScope
from app.agentive.harness.pydantic_ai_compat import (
    DeferredToolRequests,
    ModelRequest,
    ModelResponse,
    RetryPromptPart,
    ToolCallPart,
    ToolReturnPart,
)
from app.agentive.harness.scoped_store import ScopedStepStore
from app.agentive.harness.tool_argument_adapter import normalize_tool_arguments
from app.agentive.services import capability_broker, work_execution
from app.agentive.services.execution_runs import RunStep
from app.schemas.agentive.work import WorkError, WorkExecutionContext


@dataclass(frozen=True)
class DeferredStagedBinding:
    """Identity references only; possession is not execution authority."""

    snapshot_id: str
    tool_call_id: str
    tool_name: str
    run_step_id: str
    staging_token: str


async def verify_deferred_staged_call(
    *,
    scope: HarnessExecutionScope,
    context: WorkExecutionContext,
    store: ScopedStepStore,
    requests: DeferredToolRequests,
    tool_call_id: str,
    input_schema: dict[str, Any],
) -> DeferredStagedBinding:
    """Bind a native deferred call to its scoped, pending Core proposal.

    ``input_schema`` must be the host-selected schema used for broker
    invocation. Framework metadata and model arguments grant no authority.
    """

    def refuse() -> None:
        raise WorkError(
            "work.non_replayable_effect", "Deferred call needs reconciliation"
        )

    if (
        scope.principal_id != context.principal_id
        or scope.workspace_id != context.workspace_id
        or scope.thread_id != context.thread_id
        or scope.run_id != context.run_id
        or not tool_call_id
    ):
        refuse()
    await work_execution.assert_effect_boundary_allowed(context)
    snapshot = await store.latest_snapshot(
        run_id=scope.run_id, include_interrupted=True
    )
    if (
        snapshot is None
        or snapshot.run_id != scope.run_id
        or snapshot.conversation_id != scope.framework_conversation_id
        or snapshot.state != "interrupted"
        or not snapshot.idempotency_key
    ):
        refuse()
    requested = [call for call in requests.calls if call.tool_call_id == tool_call_id]
    recorded = [
        part
        for message in snapshot.messages
        if isinstance(message, ModelResponse)
        for part in message.parts
        if isinstance(part, ToolCallPart) and part.tool_call_id == tool_call_id
    ]
    already_returned = any(
        isinstance(part, (ToolReturnPart, RetryPromptPart))
        and part.tool_call_id == tool_call_id
        for message in snapshot.messages
        if isinstance(message, ModelRequest)
        for part in message.parts
    )
    if (
        len(requested) != 1
        or len(recorded) != 1
        or any(call.tool_call_id == tool_call_id for call in requests.approvals)
        or already_returned
    ):
        refuse()
    call, saved = requested[0], recorded[0]
    try:
        arguments = normalize_tool_arguments(
            call.args_as_dict(raise_if_invalid=True), input_schema
        )
        saved_arguments = normalize_tool_arguments(
            saved.args_as_dict(raise_if_invalid=True), input_schema
        )
    except (ValueError, AssertionError, TypeError):
        refuse()
    if call.tool_name != saved.tool_name or arguments != saved_arguments:
        refuse()
    effects = await store.list_unresolved_tool_effects(run_id=scope.run_id)
    matching = [effect for effect in effects if effect.tool_call_id == tool_call_id]
    if len(matching) != 1 or matching[0].tool_name != call.tool_name:
        refuse()
    key = _idempotency_key(scope.run_id, tool_call_id, call.tool_name)
    steps = await get_default_context().database.find(
        "object",
        {
            "entity": "RunStep",
            "context.run_id": scope.run_id,
            "context.idempotency_key": key,
        },
        limit=2,
    )
    if len(steps) != 1:
        refuse()
    step = RunStep(id=steps[0]["id"], **(steps[0].get("context") or {}))
    if (
        step.work_item_id != context.work_item_id
        or step.principal_id != scope.principal_id
        or step.workspace_id != scope.workspace_id
        or step.capability_key != call.tool_name
        or step.capability_source != "core"
        or step.capability_op_class != "propose"
        or step.status != "waiting_for_human"
        or step.input_fingerprint != capability_broker._fingerprint(arguments)
        or not step.approval_ref
    ):
        refuse()
    from app.agentive.staging import get_token

    staged = await get_token(step.approval_ref)
    if (
        staged is None
        or staged.token != step.approval_ref
        or staged.user_id != scope.principal_id
        or staged.workspace_id != scope.workspace_id
        or staged.session_id != scope.thread_id
        or staged.state != "pending"
        or staged.is_expired()
    ):
        refuse()
    return DeferredStagedBinding(
        snapshot_id=snapshot.idempotency_key,
        tool_call_id=tool_call_id,
        tool_name=call.tool_name,
        run_step_id=step.step_key,
        staging_token=step.approval_ref,
    )
