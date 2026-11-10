"""Verify a native deferred call against Core's staged capability evidence.

The verifier is a read-side check, not authorization to resume. Parking
rechecks it under the proposal and worker locks; a later resume must separately
authenticate the decision and applied receipt under its own lease.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, NoReturn

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


@dataclass(frozen=True)
class DeferredAppliedOutcome:
    """Authenticated data for a later, separately admitted native resume."""

    scope: HarnessExecutionScope
    snapshot: Any = field(repr=False)
    tool_call_id: str
    result: dict[str, Any] = field(repr=False)
    receipt_id: str
    thread_revision: str


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

    def refuse() -> NoReturn:
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


async def park_deferred_staged_work(
    *,
    scope: HarnessExecutionScope,
    context: WorkExecutionContext,
    store: ScopedStepStore,
    requests: DeferredToolRequests,
    tool_call_id: str,
    input_schema: dict[str, Any],
) -> tuple[Any, Any]:
    """Atomically bind one deferred frontier to a pending reviewed WorkItem.

    Multi-call frontiers require a coordinated multi-decision design and are
    refused here; never silently discard the other calls. The caller must
    durably publish the approval and arrange resume separately.
    """
    from app.agentive import staging_store
    from app.agentive.services import work_approvals, work_items
    from app.services.app_operations.transaction_scope import (
        postgres_graph_transaction,
    )

    if len(requests.calls) != 1 or requests.approvals:
        raise WorkError(
            "work.non_replayable_effect", "Deferred calls require reconciliation"
        )
    arguments = {
        "scope": scope,
        "context": context,
        "store": store,
        "requests": requests,
        "tool_call_id": tool_call_id,
        "input_schema": input_schema,
    }
    observed = await verify_deferred_staged_call(**arguments)
    async with postgres_graph_transaction(
        get_default_context().database
    ) as transaction:
        rows = await transaction.find(
            "object",
            {
                "entity": staging_store.StagedChangeRecord._entity_name(),
                "context.token": observed.staging_token,
            },
            limit=2,
        )
        if len(rows) != 1:
            raise WorkError(
                "work.non_replayable_effect", "Proposal needs reconciliation"
            )
        # Decision transitions lock this row before touching linked work.
        locked = await transaction.find_one_and_update(
            "object", {"id": rows[0]["id"]}, {"$set": {}}
        )
        facts = (locked or {}).get("context") or {}
        if (
            facts.get("token") != observed.staging_token
            or facts.get("user_id") != scope.principal_id
            or facts.get("workspace_id") != scope.workspace_id
            or facts.get("session_id") != scope.thread_id
            or facts.get("state") != "pending"
        ):
            raise WorkError(
                "work.non_replayable_effect", "Proposal needs reconciliation"
            )
        intent = staging_store.decision_intent_fingerprint(facts)
        async with work_items.authorized_work_item_effect(context):
            current = await verify_deferred_staged_call(**arguments)
            if current != observed:
                raise WorkError(
                    "work.non_replayable_effect", "Deferred call changed while parking"
                )
            item = await work_items._current_work_item(
                context.work_item_id, database=transaction
            )
            if item is None:
                raise WorkError("work.not_found", "Work item is unavailable")
            workspace_role = ""
            if item.kind == "chat_turn":
                from app.middleware.permissions_cache import isolated_permissions_cache
                from app.services.chat_turn_worker_input import (
                    load_claimed_chat_turn_input,
                )
                from app.services.workspace_permissions import can_access_workspace

                # Re-admit the original accepted input, live workspace access,
                # attachments and host control before retaining its authority.
                await load_claimed_chat_turn_input(item)
                with isolated_permissions_cache():
                    workspace_role = await can_access_workspace(
                        context.principal_id, context.workspace_id
                    )
                if workspace_role == "none":
                    raise WorkError("work.policy_denied", "Workspace access changed")
                thread = await transaction.get("node", scope.thread_id)
                thread_facts = (thread or {}).get("context") or {}
                if (
                    (thread or {}).get("entity") != "ChatThread"
                    or thread_facts.get("user_id") != scope.principal_id
                    or thread_facts.get("workspace_id") != scope.workspace_id
                    or thread_facts.get("provider_id") != "integral_native"
                    or thread_facts.get("archived")
                    or thread_facts.get("active_work_item_id") != item.work_item_id
                ):
                    raise WorkError(
                        "work.non_replayable_effect",
                        "Chat admission changed while parking",
                    )
            thread_revision = ""
            if item.kind == "chat_turn":
                from app.services.chat_turn_admission import (
                    release_chat_turn_admission_in_transaction,
                )

                await release_chat_turn_admission_in_transaction(
                    transaction=transaction,
                    thread=None,
                    thread_id=scope.thread_id,
                    work_item_id=item.work_item_id,
                )
                thread = await transaction.get("node", scope.thread_id)
                thread_facts = (thread or {}).get("context") or {}
                if (
                    (thread or {}).get("entity") != "ChatThread"
                    or thread_facts.get("user_id") != scope.principal_id
                    or thread_facts.get("workspace_id") != scope.workspace_id
                    or thread_facts.get("provider_id") != "integral_native"
                    or thread_facts.get("archived")
                    or thread_facts.get("active_work_item_id")
                ):
                    raise WorkError(
                        "work.non_replayable_effect",
                        "Chat admission changed while parking",
                    )
                thread_revision = str(thread_facts.get("updated_at") or "")
                if not thread_revision:
                    raise WorkError(
                        "work.non_replayable_effect", "Chat revision is unavailable"
                    )
            approval, parked = await work_approvals.propose_work_approval_unit(
                work_item_id=context.work_item_id,
                lease_token=context.lease_token,
                lease_fence=context.lease_fence,
                run_id=context.run_id,
                run_step_id=current.run_step_id,
                staging_token=current.staging_token,
                authority_digest=context.effect_key,
                expires_at=item.deadline_at or "",
                framework_snapshot_id=current.snapshot_id,
                framework_tool_call_id=current.tool_call_id,
                framework_tool_name=current.tool_name,
                deferred_intent_fingerprint=intent,
                deferred_thread_revision=thread_revision,
                framework_session_id=scope.session_id,
                framework_permission_revision=scope.permission_revision,
                framework_capability_version=scope.capability_version,
                deferred_workspace_role=workspace_role,
                transaction=transaction,
            )
            return approval, parked


async def read_deferred_applied_chat(
    *, context: WorkExecutionContext, input_schema: dict[str, Any]
) -> DeferredAppliedOutcome:
    """Reconcile a parked chat's applied decision without starting a model run.

    These facts can change after the function returns. A production resume
    must recheck them while atomically acquiring chat admission and must fence
    each subsequent model request and effect against current authority.
    """
    from app.agentive import staging_store
    from app.agentive.harness.jvspatial_store import JvSpatialStepStore
    from app.agentive.services import work_items
    from app.agentive.services.staging_execution import execution_record_id
    from app.agentive.services.work_approvals import (
        deterministic_work_approval_id,
        work_approval_object_id,
    )
    from app.middleware.permissions_cache import isolated_permissions_cache
    from app.models.edges import HAS_HARNESS_SESSION
    from app.services.credential_crypto import decrypt_secret_from_storage
    from app.services.workspace_permissions import can_access_workspace

    def refuse() -> NoReturn:
        raise WorkError(
            "work.non_replayable_effect", "Deferred outcome needs reconciliation"
        )

    await work_execution.assert_effect_boundary_allowed(context)
    async with work_items.authorized_work_item_effect(context):
        database = get_default_context().database
        item = await work_items._current_work_item(
            context.work_item_id, database=database
        )
        if item is None or item.kind != "chat_turn" or not item.run_id:
            refuse()
        links = await database.find(
            "object",
            {
                "entity": "WorkApproval",
                "context.work_item_id": item.work_item_id,
                "context.run_id": item.run_id,
                "context.status": "approved",
            },
            limit=2,
        )
        if len(links) != 1:
            refuse()
        link = links[0].get("context") or {}
        token = str(link.get("staging_token") or "")
        intent = str(link.get("deferred_intent_fingerprint") or "")
        receipt_id = str(link.get("staging_execution_id") or "")
        request_hash = str(link.get("staging_request_hash") or "")
        call_id = str(link.get("framework_tool_call_id") or "")
        tool_name = str(link.get("framework_tool_name") or "")
        snapshot_id = str(link.get("framework_snapshot_id") or "")
        revision = str(link.get("deferred_thread_revision") or "")
        scope_values = (
            link.get("framework_session_id"),
            link.get("framework_permission_revision"),
            link.get("framework_capability_version"),
        )
        with isolated_permissions_cache():
            workspace_role = await can_access_workspace(
                context.principal_id, context.workspace_id
            )
        if (
            not all(
                (
                    token,
                    intent,
                    receipt_id,
                    request_hash,
                    call_id,
                    tool_name,
                    snapshot_id,
                    revision,
                    *scope_values,
                )
            )
            or links[0]["id"]
            != work_approval_object_id(
                deterministic_work_approval_id(
                    work_item_id=item.work_item_id, staging_token=token
                )
            )
            or link.get("authority_digest") != context.effect_key
            or link.get("staging_intent_fingerprint") != intent
            or receipt_id != execution_record_id(token)
        ):
            refuse()
        thread = await database.get("node", context.thread_id)
        thread_data = (thread or {}).get("context") or {}
        if (
            (thread or {}).get("entity") != "ChatThread"
            or thread_data.get("user_id") != context.principal_id
            or thread_data.get("workspace_id") != context.workspace_id
            or thread_data.get("provider_id") != "integral_native"
            or thread_data.get("archived")
            or thread_data.get("updated_at") != revision
            or thread_data.get("active_work_item_id")
            or workspace_role == "none"
            or workspace_role != link.get("deferred_workspace_role")
        ):
            refuse()
        session_id = str(thread_data.get("active_harness_session_id") or "")
        session = await database.get("node", session_id) if session_id else None
        session_data = (session or {}).get("context") or {}
        active_sessions = await database.find(
            "node",
            {
                "entity": "HarnessSession",
                "context.thread_id": context.thread_id,
                "context.status": "active",
            },
            limit=2,
        )
        graph = get_default_context()
        if (
            (session or {}).get("entity") != "HarnessSession"
            or len(active_sessions) != 1
            or active_sessions[0].get("id") != session_id
            or session_data.get("session_id") != scope_values[0]
            or session_data.get("permission_revision") != scope_values[1]
            or session_data.get("capability_version") != scope_values[2]
            or session_data.get("principal_id") != context.principal_id
            or session_data.get("workspace_id") != context.workspace_id
            or session_data.get("thread_id") != context.thread_id
            or session_data.get("status") != "active"
            or session_data.get("binding_id") != "pydantic_ai_v1"
            or not await graph.find_edges_between(
                context.thread_id, session_id, edge_class=HAS_HARNESS_SESSION
            )
        ):
            refuse()
        marker = await database.get("object", staging_store._decision_record_id(token))
        marker_data = (marker or {}).get("context") or {}
        receipt = await database.get("object", receipt_id)
        facts = (receipt or {}).get("context") or {}
        steps = await database.find(
            "object",
            {
                "entity": "RunStep",
                "context.run_id": item.run_id,
                "context.step_key": link.get("run_step_id"),
            },
            limit=2,
        )
        runs = await database.find(
            "object", {"entity": "AgentRun", "context.run_id": item.run_id}, limit=2
        )
        run = (runs[0].get("context") or {}) if len(runs) == 1 else {}
        step = (steps[0].get("context") or {}) if len(steps) == 1 else {}
        if (
            run.get("work_item_id") != item.work_item_id
            or run.get("user_id") != context.principal_id
            or run.get("workspace_id") != context.workspace_id
            or run.get("thread_id") != context.thread_id
            or run.get("provider_id") != "integral_native"
            or run.get("status") != "running"
            or (marker or {}).get("entity") != "StagingDecisionRecord"
            or marker_data.get("state") != "consumed"
            or marker_data.get("user_id") != context.principal_id
            or marker_data.get("intent_fingerprint") != intent
            or (receipt or {}).get("entity") != "StagingExecutionRecord"
            or facts.get("principal_id") != context.principal_id
            or facts.get("workspace_id") != context.workspace_id
            or facts.get("kind") != marker_data.get("kind")
            or facts.get("request_hash") != request_hash
            or facts.get("state") != "applied"
            or facts.get("local_effect_state") != "applied"
            or facts.get("external_outcome") not in {None, "not_dispatched", "returned"}
            or step.get("work_item_id") != item.work_item_id
            or step.get("principal_id") != context.principal_id
            or step.get("workspace_id") != context.workspace_id
            or step.get("capability_key") != tool_name
            or step.get("capability_source") != "core"
            or step.get("capability_op_class") != "propose"
            or step.get("status") != "waiting_for_human"
            or step.get("approval_ref") != token
            or step.get("idempotency_key")
            != _idempotency_key(item.run_id, call_id, tool_name)
        ):
            refuse()
        scope = HarnessExecutionScope(
            tenant_id=context.workspace_id,
            workspace_id=context.workspace_id,
            principal_id=context.principal_id,
            thread_id=context.thread_id,
            session_id=str(scope_values[0]),
            run_id=item.run_id,
            permission_revision=str(scope_values[1]),
            capability_version=str(scope_values[2]),
        )
        store = ScopedStepStore(store=JvSpatialStepStore(scope=scope), scope=scope)
        snapshot = await store.latest_snapshot(
            run_id=scope.run_id, include_interrupted=True
        )
        if (
            snapshot is None
            or snapshot.state != "interrupted"
            or snapshot.idempotency_key != snapshot_id
        ):
            refuse()
        calls = [
            part
            for msg in snapshot.messages
            if isinstance(msg, ModelResponse)
            for part in msg.parts
            if isinstance(part, ToolCallPart) and part.tool_call_id == call_id
        ]
        returns = [
            part
            for msg in snapshot.messages
            if isinstance(msg, ModelRequest)
            for part in msg.parts
            if isinstance(part, (ToolReturnPart, RetryPromptPart))
            and part.tool_call_id == call_id
        ]
        effects = await store.list_unresolved_tool_effects(run_id=scope.run_id)
        if (
            len(calls) != 1
            or returns
            or len(effects) != 1
            or effects[0].tool_call_id != call_id
            or effects[0].tool_name != tool_name
        ):
            refuse()
        try:
            args = normalize_tool_arguments(
                calls[0].args_as_dict(raise_if_invalid=True), input_schema
            )
            result = json.loads(
                decrypt_secret_from_storage(
                    facts.get("result_ciphertext"), aad=receipt_id
                )
            )
        except Exception as exc:
            raise WorkError(
                "work.non_replayable_effect", "Deferred receipt is unavailable"
            ) from exc
        if (
            calls[0].tool_name != tool_name
            or capability_broker._fingerprint(args) != step.get("input_fingerprint")
            or not isinstance(result, dict)
        ):
            refuse()
        return DeferredAppliedOutcome(
            scope, snapshot, call_id, result, receipt_id, revision
        )


async def admit_deferred_chat_resume(
    *, context: WorkExecutionContext, input_schema: dict[str, Any]
) -> DeferredAppliedOutcome:
    """Reconcile and reacquire chat admission under the same work fence.

    The returned outcome does not authorize an unfenced model request. A
    caller must still rebuild the current runtime and recheck its capabilities,
    sources, cancellation and effect boundaries. Recovery after a crash during
    admission or dispatch requires a separate durable continuation protocol.
    """
    from app.agentive.services import work_items
    from app.models.nodes import ChatThread
    from app.services.chat_turn_admission import reserve_chat_turn_admission
    from app.services.chat_turn_worker_input import load_claimed_chat_turn_input

    async with work_items.authorized_work_item_effect(context):
        outcome = await read_deferred_applied_chat(
            context=context, input_schema=input_schema
        )
        graph = get_default_context()
        transaction = graph.database
        item = await work_items._current_work_item(
            context.work_item_id, database=transaction
        )
        row = await transaction.get("node", context.thread_id)
        if (
            item is None
            or row is None
            or row.get("entity") != "ChatThread"
            or (row.get("context") or {}).get("updated_at") != outcome.thread_revision
            or (row.get("context") or {}).get("active_work_item_id")
        ):
            raise WorkError(
                "work.non_replayable_effect", "Chat admission is unavailable"
            )
        thread = await graph._deserialize_entity(ChatThread, row)
        if thread is None:
            raise WorkError(
                "work.non_replayable_effect", "Chat admission is unavailable"
            )
        # reserve_chat_turn_admission compares the thread's exact revision.
        # A competing message or admission between reconciliation and this
        # CAS forces a rollback of the permit and the whole transaction.
        await reserve_chat_turn_admission(
            transaction=transaction,
            thread=thread,
            principal_id=context.principal_id,
            workspace_id=context.workspace_id,
            work_item=item,
        )
        await graph._evict_from_cache(context.thread_id)
        try:
            await load_claimed_chat_turn_input(item)
        finally:
            # A rolled-back reservation must not leave a cached thread
            # projection that claims an admission which never committed.
            await graph._evict_from_cache(context.thread_id)
        return outcome
