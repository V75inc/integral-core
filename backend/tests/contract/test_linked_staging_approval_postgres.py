"""Linked work decisions must follow the actual staging authority check."""

import asyncio
import json
import os
import sys
from typing import Any

import pytest
from jvspatial.core.context import GraphContext, scoped_default_context_async

from app.agentive.services import staging_apply, work_approvals, work_items
from app.agentive.staging import StagingError
from app.agentive.work_models import WorkApproval, WorkItem
from app.models.nodes import Entry
from app.schemas.agentive.work import WorkError
from tests.contract.test_staging_command_apply_postgres import proposal

pytestmark = [pytest.mark.contract, pytest.mark.postgres, pytest.mark.asyncio]


async def _park(auth, workspace_id, sc):
    item = await work_items.enqueue_work_item(
        kind="capability",
        origin="http",
        principal_id=auth.id,
        workspace_id=workspace_id,
        idempotency_key=f"linked-approval-{sc.token}",
        input_payload={
            "capability_key": "integral_create_entry",
            "op_class": "propose",
        },
    )
    claimed = await work_items.claim_due_candidate(
        worker_id="approval-test", work_item_id=item.work_item_id, lease_seconds=60
    )
    approval, parked = await work_approvals.propose_work_approval_unit(
        work_item_id=claimed.work_item_id,
        lease_token=claimed.lease_token,
        lease_fence=claimed.lease_fence,
        run_id="approval-test-run",
        run_step_id="approval-test-step",
        staging_token=sc.token,
        authority_digest="approval-test-authority",
    )
    assert parked.status == "waiting_for_human"
    return item, approval


async def _linked_proposal():
    auth, workspace, track, sc = await proposal()
    item, approval = await _park(auth, workspace.id, sc)
    return auth, track, sc, item, approval


@pytest.mark.parametrize(
    "changed",
    [
        "run_id",
        "run_step_id",
        "authority_digest",
        "expires_at",
        "framework_snapshot_id",
        "framework_tool_call_id",
        "framework_tool_name",
        "deferred_intent_fingerprint",
        "deferred_schema_fingerprint",
        "deferred_declaration_fingerprint",
        "deferred_thread_revision",
        "framework_session_id",
        "framework_permission_revision",
        "framework_capability_version",
        "deferred_workspace_role",
    ],
)
async def test_existing_approval_cannot_alias_different_work_intent(
    postgres_raw_db, monkeypatch, changed
):
    """Idempotent parking only returns the same reviewed run and authority."""
    monkeypatch.setenv("INTEGRAL_CREDENTIAL_ENC_KEY", "22" * 32)
    async with scoped_default_context_async(GraphContext(database=postgres_raw_db)):
        _, track, sc, item, approval = await _linked_proposal()
        args = {
            "work_item_id": item.work_item_id,
            "lease_token": "unused-on-idempotent-retry",
            "lease_fence": 1,
            "run_id": "approval-test-run",
            "run_step_id": "approval-test-step",
            "staging_token": sc.token,
            "authority_digest": "approval-test-authority",
            "expires_at": "",
        }
        same, parked = await work_approvals.propose_work_approval_unit(**args)
        assert same.id == approval.id and parked.status == "waiting_for_human"
        args[changed] = "different"
        with pytest.raises(WorkError) as error:
            await work_approvals.propose_work_approval_unit(**args)
        assert error.value.code == "work.idempotency_conflict"
        assert (await WorkApproval.get(approval.id)).status == "pending"
        assert (await WorkItem.get(item.id)).status == "waiting_for_human"
        assert await Entry.find({"track_id": track.id}) == []


async def test_racing_approval_insert_rechecks_existing_intent(
    postgres_raw_db, monkeypatch
):
    """The transactional insert-conflict path also checks the stored binding."""
    monkeypatch.setenv("INTEGRAL_CREDENTIAL_ENC_KEY", "22" * 32)
    async with scoped_default_context_async(GraphContext(database=postgres_raw_db)):
        _, _, _, item, approval = await _linked_proposal()
        doc = await postgres_raw_db.get("object", approval.id)
        doc["context"]["run_step_id"] = "different-step"
        with pytest.raises(WorkError) as error:
            await work_approvals._propose_postgres(
                db=postgres_raw_db,
                transaction=None,
                doc=doc,
                work_item_id=item.work_item_id,
                lease_token="unused",
                lease_fence=1,
                now="2026-01-01T00:00:00+00:00",
            )
        assert error.value.code == "work.idempotency_conflict"
        assert (await postgres_raw_db.get("object", approval.id))["context"][
            "run_step_id"
        ] == "approval-test-step"
        assert (await postgres_raw_db.get("object", item.id))["context"][
            "status"
        ] == "waiting_for_human"


@pytest.mark.parametrize(
    "work_kind,intervention",
    [
        ("capability", False),
        ("chat_turn", False),
        ("chat_turn", True),
        ("chat_turn", "race"),
        ("chat_turn", "cas"),
        ("chat_turn", "dispatch"),
        ("chat_turn", "correction"),
        ("chat_turn", "correction_changed"),
        ("chat_turn", "correction_scope"),
        ("chat_turn", "rotation"),
        ("chat_turn", "settlement_changed"),
        ("chat_turn", "receipt_changed"),
        ("chat_turn", "rejected"),
        ("chat_turn", "expired"),
        ("chat_turn", "worker_park"),
        ("chat_turn", "worker_reject"),
    ],
)
async def test_native_deferred_frontier_with_real_broker_approval(
    postgres_raw_db, monkeypatch, work_kind, intervention
):
    """Probe native deferral against the real broker, decision and graph effect."""
    from pydantic_ai import Agent, CallDeferred, RunContext
    from pydantic_ai.messages import (
        DeferredToolRequestsEvent,
        ModelRequest,
        ToolReturnPart,
    )
    from pydantic_ai.models.function import DeltaToolCall, FunctionModel
    from pydantic_ai.tools import DeferredToolRequests, DeferredToolResults
    from pydantic_ai_harness.step_persistence import StepPersistence

    from app.agentive import staging
    from app.agentive.harness.broker_tools import _idempotency_key, _tool_work_context
    from app.agentive.harness.contracts import HarnessExecutionScope
    from app.agentive.harness.deferred_staging import (
        admit_deferred_chat_resume,
        current_core_deferred_schema,
        park_deferred_staged_work,
        read_deferred_applied_chat,
        verify_deferred_staged_call,
    )
    from app.agentive.harness.jvspatial_store import JvSpatialStepStore
    from app.agentive.harness.scoped_store import ScopedStepStore
    from app.agentive.services import capability_broker, work_execution
    from app.agentive.services.execution_runs import RunStep, start_run

    monkeypatch.setenv("INTEGRAL_CREDENTIAL_ENC_KEY", "22" * 32)
    async with scoped_default_context_async(GraphContext(database=postgres_raw_db)):
        auth, workspace, track, unrelated = await proposal()
        await staging.revoke_token(user_id=auth.id, token=unrelated.token)
        if work_kind == "chat_turn":
            from app.services.chat_threads import create_thread

            thread_node = await create_thread(
                user_id=auth.id,
                workspace_id=workspace.id,
                provider_id="integral_native",
            )
            thread = thread_node.id
        else:
            thread_node = None
            thread = "deferred-probe-" + unrelated.token
        arguments = {"track_id": track.id, "title": "Deferred broker probe"}
        if thread_node is not None:
            from app.schemas.agentive.work import ChatTurnSubmissionRequest
            from app.services.chat_turn_submissions import submit_chat_turn

            accepted = await submit_chat_turn(
                ChatTurnSubmissionRequest(
                    principal_id=auth.id,
                    workspace_id=workspace.id,
                    thread_id=thread,
                    client_request_id="deferred-" + unrelated.token,
                    parts=[{"type": "text", "text": "Create the entry."}],
                )
            )
            item = await WorkItem.get(f"o.WorkItem.{accepted.work_item_id}")
        else:
            item = await work_items.enqueue_work_item(
                kind=work_kind,
                origin="http",
                principal_id=auth.id,
                workspace_id=workspace.id,
                thread_id=thread,
                idempotency_key="deferred-" + unrelated.token,
                input_payload={
                    "capability_key": "integral_create_entry",
                    "source": "core",
                    "op_class": "propose",
                    "arguments": arguments,
                },
            )
        claimed = await work_items.claim_due_candidate(
            worker_id="deferred-probe", work_item_id=item.work_item_id, lease_seconds=60
        )
        context = work_execution.build_work_execution_context(
            work_item=claimed,
            logical_step_key=work_execution.logical_step_key_for(kind=work_kind),
        )
        await start_run(
            thread_id=thread,
            user_id=auth.id,
            workspace_id=workspace.id,
            provider_id=(
                "integral_native" if work_kind == "chat_turn" else "deferred-probe"
            ),
            origin="chat" if work_kind == "chat_turn" else "http",
            run_id=context.run_id,
            work_item_id=item.work_item_id,
        )
        scope = HarnessExecutionScope(
            tenant_id=workspace.id,
            principal_id=auth.id,
            workspace_id=workspace.id,
            thread_id=thread,
            session_id=thread,
            run_id=context.run_id,
            permission_revision="probe",
            capability_version="probe",
        )
        if thread_node is not None:
            from app.services.harness_sessions import ensure_harness_session

            await ensure_harness_session(
                scope=scope, binding_id="pydantic_ai_v1", work_execution_context=context
            )
        store = ScopedStepStore(
            store=JvSpatialStepStore(scope=scope),
            scope=scope,
            work_execution_context=context,
        )
        broker_results = []

        async def integral_create_entry(ctx: RunContext, track_id: str, title: str):
            if intervention in {"worker_park", "worker_reject"}:
                from app.agentive.harness.broker_tools import _make_handler

                handler = _make_handler(
                    scope=scope,
                    capability_name="integral_create_entry",
                    capability_source="core",
                    capability_op_class="propose",
                    input_schema=current_core_deferred_schema("integral_create_entry"),
                    skill_allowlist=(),
                    invoke_declared=capability_broker.invoke_declared_capability,
                    work_execution_context=context,
                    call_state={"native_deferred_approvals": True, "attempted": 0},
                    no_workspace_writes=False,
                    design_only=False,
                    conversation_id=thread,
                )
                return await handler(ctx, track_id=track_id, title=title)
            tool_context = _tool_work_context(context, ctx.tool_call_id, ctx.tool_name)
            async with work_items.authorized_work_item_effect(context):
                result = await capability_broker.invoke_declared_capability(
                    principal_id=auth.id,
                    workspace_id=workspace.id,
                    capability_key="integral_create_entry",
                    origin="chat" if work_kind == "chat_turn" else "http",
                    source="core",
                    op_class="propose",
                    arguments={"track_id": track_id, "title": title},
                    run_id=context.run_id,
                    session_id=thread,
                    work_execution_context=tool_context,
                    idempotency_key=_idempotency_key(
                        context.run_id, ctx.tool_call_id, ctx.tool_name
                    ),
                )
            assert result.ok, result
            assert result.receipt.status == "waiting_for_human"
            broker_results.append(result)
            raise CallDeferred(metadata={"proposal_ref": result.data["token"]})

        async def model(_messages, _info):
            yield {
                0: DeltaToolCall(
                    name="integral_create_entry",
                    json_args=json.dumps(arguments),
                    tool_call_id="native-probe-call",
                )
            }

        agent = Agent(
            FunctionModel(stream_function=model),
            tools=[integral_create_entry],
            output_type=[str, DeferredToolRequests],
            capabilities=[
                StepPersistence(store=store, run_id=scope.run_id, capture_frontier=True)
            ],
        )
        if intervention in {"worker_park", "worker_reject"}:
            import tempfile

            from pydantic_ai import Tool

            from app.agentive.harness.runtime import build_native_runtime
            from app.agentive.services.work_worker import execute_claimed_work
            from app.services.chat_providers.pydantic_ai_provider import (
                PydanticAIProvider,
            )

            provider = PydanticAIProvider()
            agent, store = build_native_runtime(
                model=FunctionModel(stream_function=model),
                instructions="",
                tools=[Tool(integral_create_entry)],
                scope=scope,
                agent_name="integral_core",
                work_execution_context=context,
                accepts_deferred_approvals=True,
            )

            async def prepare(_turn):
                return (
                    scope,
                    None,
                    agent,
                    store,
                    [],
                    tempfile.TemporaryDirectory(),
                    context,
                    {"native_deferred_approvals": True},
                )

            class Registry:
                def get(self, provider_id):
                    assert provider_id == "integral_native"
                    return provider

            monkeypatch.setattr(provider, "_prepare", prepare)
            monkeypatch.setattr(provider, "is_available", lambda: True)
            monkeypatch.setattr("app.services.chat_providers.get_registry", Registry)
            original_stream = provider.stream_turn
            early_checks = []
            published = []

            async def observe_created(change):
                published.append(change.token)

            monkeypatch.setattr(staging, "_push_created_event", observe_created)

            async def verify_early_decision(turn):
                from contextlib import aclosing

                async with aclosing(original_stream(turn)) as stream:
                    async for event in stream:
                        if event["type"] == "_deferred-approval":
                            proposals = await postgres_raw_db.find(
                                "object",
                                {
                                    "entity": "StagedChangeRecord",
                                    "context.origin_proof.work_item_id": item.work_item_id,
                                },
                            )
                            assert len(proposals) == 1
                            fields = proposals[0]["context"]
                            assert (
                                fields["origin_proof"]["requires_deferred_link"] is True
                            )
                            assert fields["token"] not in {
                                change.token
                                for change in await staging.get_pending_for_user(
                                    auth.id
                                )
                            }
                            assert published == []
                            thread_facts = (await postgres_raw_db.get("node", thread))[
                                "context"
                            ]
                            assert fields["token"] not in {
                                queued.get("token")
                                for queued in (
                                    thread_facts.get("prompt_queue") or {}
                                ).get("items", [])
                            }
                            with pytest.raises(StagingError) as refused:
                                await staging_apply.bless_and_execute(
                                    user_id=auth.id, token=fields["token"]
                                )
                            assert refused.value.code == "approval_not_ready"
                            assert not await Entry.find({"track_id": track.id})
                            early_checks.append(fields["token"])
                        yield event

            monkeypatch.setattr(provider, "stream_turn", verify_early_decision)
            parked = await execute_claimed_work(
                claimed, worker_id="deferred-probe", lease_seconds=60
            )
            assert parked.status == "waiting_for_human", parked.failure
            approval = await work_approvals.get_pending_by_work_item(item.work_item_id)
            assert (
                approval is not None
                and approval.framework_tool_call_id == "native-probe-call"
            )
            assert early_checks == [approval.staging_token]
            assert published == [approval.staging_token]
            assert approval.staging_token in {
                change.token for change in await staging.get_pending_for_user(auth.id)
            }
            assert not await Entry.find({"track_id": track.id})
            assert (await postgres_raw_db.get("node", thread))["context"][
                "active_work_item_id"
            ] == ""
            if intervention == "worker_reject":
                from app.agentive.services.approval_decisions import decide_staged_write

                original_decide = work_approvals.decide_work_approval_unit

                async def fail_after_closure(**kwargs):
                    await original_decide(**kwargs)
                    raise WorkError("work.injected_failure")

                with monkeypatch.context() as patch:
                    patch.setattr(
                        work_approvals, "decide_work_approval_unit", fail_after_closure
                    )
                    with pytest.raises(
                        StagingError, match="Linked work requires reconciliation"
                    ):
                        await decide_staged_write(
                            principal_id=auth.id,
                            proposal_id=approval.staging_token,
                            decision="reject",
                            source="card",
                        )
                assert (
                    await staging.get_token(approval.staging_token)
                ).state == "pending"
                assert (await postgres_raw_db.get("object", item.id))["context"][
                    "status"
                ] == "waiting_for_human"
                rejected = await decide_staged_write(
                    principal_id=auth.id,
                    proposal_id=approval.staging_token,
                    decision="reject",
                    source="card",
                )
                assert rejected["state"] == "revoked"
                assert (await postgres_raw_db.get("object", item.id))["context"][
                    "status"
                ] == "failed"
                assert (await postgres_raw_db.get("object", approval.id))["context"][
                    "status"
                ] == "rejected"
                runs = await postgres_raw_db.find(
                    "object", {"entity": "AgentRun", "context.run_id": context.run_id}
                )
                assert runs[0]["context"]["error"]["code"] == "work.approval_rejected"
                assert not await Entry.find({"track_id": track.id})
                return

            applied = await staging_apply.bless_and_execute(
                user_id=auth.id, token=approval.staging_token
            )
            assert applied["consumed"], applied
            assert len(await Entry.find({"track_id": track.id})) == 1
            monkeypatch.setattr(provider, "stream_turn", original_stream)

            async def prepare_second(turn):
                from app.agentive.harness.broker_tools import _make_handler
                from app.schemas.agentive.work import WorkExecutionContext

                admitted = turn.deferred_resume
                assert admitted is not None
                current = WorkExecutionContext.model_validate(
                    turn.extra_data["work_execution_context"]
                )
                new_scope = scope.model_copy(update={"run_id": current.run_id})
                handler = _make_handler(
                    scope=new_scope,
                    capability_name="integral_create_entry",
                    capability_source="core",
                    capability_op_class="propose",
                    input_schema=current_core_deferred_schema("integral_create_entry"),
                    skill_allowlist=(),
                    invoke_declared=capability_broker.invoke_declared_capability,
                    work_execution_context=current,
                    call_state={"native_deferred_approvals": True, "attempted": 0},
                    no_workspace_writes=False,
                    design_only=False,
                    conversation_id=thread,
                )

                async def integral_create_entry(
                    ctx: RunContext, track_id: str, title: str
                ):
                    return await handler(ctx, track_id=track_id, title=title)

                async def second_model(messages, _info):
                    assert any(
                        part.tool_call_id == admitted.tool_call_id
                        and part.content == admitted.result
                        for message in messages
                        if isinstance(message, ModelRequest)
                        for part in message.parts
                        if isinstance(part, ToolReturnPart)
                    )
                    if admitted.tool_call_id == "second-native-call":
                        yield "Both approved Entries were created."
                        return
                    yield {
                        0: DeltaToolCall(
                            name="integral_create_entry",
                            json_args=json.dumps(
                                {"track_id": track.id, "title": "Second approved Entry"}
                            ),
                            tool_call_id="second-native-call",
                        )
                    }

                agent, second_store = build_native_runtime(
                    model=FunctionModel(stream_function=second_model),
                    instructions="",
                    tools=[Tool(integral_create_entry)],
                    scope=new_scope,
                    agent_name="integral_core",
                    work_execution_context=current,
                    accepts_deferred_approvals=True,
                )
                history, results = admitted.native_resume_input(accepted_text=turn.text)
                return (
                    new_scope,
                    None,
                    agent,
                    second_store,
                    history,
                    tempfile.TemporaryDirectory(),
                    current,
                    {
                        "native_deferred_approvals": True,
                        "deferred_tool_results": results,
                        "user_prompt": None,
                    },
                )

            monkeypatch.setattr(provider, "_prepare", prepare_second)
            second = await work_items.claim_due_candidate(
                worker_id="deferred-probe",
                work_item_id=item.work_item_id,
                lease_seconds=60,
            )
            assert second is not None
            parked_again = await execute_claimed_work(
                second, worker_id="deferred-probe", lease_seconds=60
            )
            assert parked_again.status == "waiting_for_human", parked_again.failure
            approval2 = await work_approvals.get_pending_by_work_item(item.work_item_id)
            assert (
                approval2 is not None
                and approval2.framework_tool_call_id == "second-native-call"
            )
            original_runs = await postgres_raw_db.find(
                "object", {"entity": "AgentRun", "context.run_id": context.run_id}
            )
            assert original_runs[0]["context"]["status"] == "succeeded"
            assert len(await Entry.find({"track_id": track.id})) == 1
            applied2 = await staging_apply.bless_and_execute(
                user_id=auth.id, token=approval2.staging_token
            )
            assert applied2["consumed"], applied2
            assert len(await Entry.find({"track_id": track.id})) == 2
            third = await work_items.claim_due_candidate(
                worker_id="deferred-probe",
                work_item_id=item.work_item_id,
                lease_seconds=60,
            )
            assert third is not None
            finished = await execute_claimed_work(
                third, worker_id="deferred-probe", lease_seconds=60
            )
            assert finished.status == "succeeded", finished.failure
            runs = await postgres_raw_db.find(
                "object",
                {"entity": "AgentRun", "context.work_item_id": item.work_item_id},
            )
            assert len(runs) == 3 and all(
                row["context"]["status"] == "succeeded" for row in runs
            )
            assert len(await Entry.find({"track_id": track.id})) == 2
            return

        events = []
        async with agent.run_stream_events(
            "Create the entry.", conversation_id=scope.session_id, run_id=scope.run_id
        ) as stream:
            async for event in stream:
                events.append(event)
        deferred = next(e for e in events if isinstance(e, DeferredToolRequestsEvent))
        result = broker_results[0]
        token = result.data["token"]
        assert deferred.requests.metadata["native-probe-call"]["proposal_ref"] == token
        frontier = await store.latest_snapshot(
            run_id=scope.run_id, include_interrupted=True
        )
        assert frontier is not None and frontier.state == "interrupted"
        assert len(await store.list_unresolved_tool_effects(run_id=scope.run_id)) == 1
        assert await Entry.find({"track_id": track.id}) == []
        step = await RunStep.get(result.receipt.step_id)
        assert step.approval_ref == token and step.work_item_id == item.work_item_id
        assert step.idempotency_key == _idempotency_key(
            context.run_id, "native-probe-call", "integral_create_entry"
        )
        assert (
            await RunStep.find_one(
                {"run_id": scope.run_id, "idempotency_key": step.idempotency_key}
            )
            is not None
        )
        verify_args = {
            "scope": scope,
            "context": context,
            "store": store,
            "requests": deferred.requests,
            "tool_call_id": "native-probe-call",
            "input_schema": current_core_deferred_schema("integral_create_entry"),
        }
        binding = await verify_deferred_staged_call(**verify_args)
        assert binding.staging_token == token
        assert binding.run_step_id == step.step_key
        assert binding.snapshot_id == frontier.idempotency_key

        from copy import deepcopy

        changed = deepcopy(deferred.requests)
        changed.calls[0].args = {**arguments, "title": "different"}
        with pytest.raises(WorkError, match="Deferred call needs reconciliation"):
            await verify_deferred_staged_call(**{**verify_args, "requests": changed})
        changed = deepcopy(deferred.requests)
        changed.calls.append(deepcopy(changed.calls[0]))
        with pytest.raises(WorkError, match="Deferred call needs reconciliation"):
            await verify_deferred_staged_call(**{**verify_args, "requests": changed})
        with pytest.raises(WorkError, match="Deferred calls require reconciliation"):
            await park_deferred_staged_work(**{**verify_args, "requests": changed})
        changed = deepcopy(deferred.requests)
        changed.approvals.append(deepcopy(changed.calls[0]))
        with pytest.raises(WorkError, match="Deferred call needs reconciliation"):
            await verify_deferred_staged_call(**{**verify_args, "requests": changed})
        with pytest.raises(WorkError, match="Deferred call needs reconciliation"):
            await verify_deferred_staged_call(
                **{
                    **verify_args,
                    "scope": scope.model_copy(update={"principal_id": "someone-else"}),
                }
            )
        with pytest.raises(WorkError) as stale_lease:
            await verify_deferred_staged_call(
                **{
                    **verify_args,
                    "context": context.model_copy(update={"lease_token": "stale"}),
                }
            )
        assert stale_lease.value.code == "work.lease_lost"
        if thread_node is not None:
            await postgres_raw_db.find_one_and_update(
                "node", {"id": thread}, {"$set": {"context.archived": True}}
            )
            with pytest.raises(WorkError):
                await park_deferred_staged_work(**verify_args)
            assert (await postgres_raw_db.get("object", item.id))["context"][
                "status"
            ] == "running"
            await postgres_raw_db.find_one_and_update(
                "node", {"id": thread}, {"$set": {"context.archived": False}}
            )

        from dataclasses import replace

        async def already_settled(**_kwargs):
            return replace(
                frontier,
                messages=[
                    *frontier.messages,
                    ModelRequest(
                        parts=[
                            ToolReturnPart(
                                "integral_create_entry",
                                {"created": True},
                                "native-probe-call",
                            )
                        ]
                    ),
                ],
            )

        with monkeypatch.context() as patch:
            patch.setattr(store, "latest_snapshot", already_settled)
            with pytest.raises(WorkError, match="Deferred call needs reconciliation"):
                await verify_deferred_staged_call(**verify_args)

        from types import SimpleNamespace

        async def wrong_broker_step(*args: Any, **kwargs: Any):
            rows = deepcopy(await postgres_raw_db.find(*args, **kwargs))
            for row in rows:
                row["context"]["approval_ref"] = "another-token"
            return rows

        with monkeypatch.context() as patch:
            patch.setattr(
                "app.agentive.harness.deferred_staging.get_default_context",
                lambda: SimpleNamespace(
                    database=SimpleNamespace(find=wrong_broker_step)
                ),
            )
            with pytest.raises(WorkError, match="Deferred call needs reconciliation"):
                await verify_deferred_staged_call(**verify_args)

        original_propose = work_approvals.propose_work_approval_unit

        async def fail_after_park(**kwargs):
            await original_propose(**kwargs)
            raise RuntimeError("injected failure before commit")

        with monkeypatch.context() as patch:
            patch.setattr(work_approvals, "propose_work_approval_unit", fail_after_park)
            with pytest.raises(RuntimeError, match="injected failure"):
                await park_deferred_staged_work(**verify_args)
        assert (await postgres_raw_db.get("object", item.id))["context"][
            "status"
        ] == "running"
        if thread_node is not None:
            assert (await postgres_raw_db.get("node", thread))["context"][
                "active_work_item_id"
            ] == item.work_item_id
        assert (
            await postgres_raw_db.find(
                "object",
                {"entity": "WorkApproval", "context.work_item_id": item.work_item_id},
            )
            == []
        )
        if intervention in {"rejected", "expired"}:
            approval, _ = await park_deferred_staged_work(**verify_args)
            with pytest.raises(WorkError, match="requires an applied staging receipt"):
                await work_approvals.approve_work_approval(
                    work_approval_id=approval.work_approval_id, decider_id=auth.id
                )
            assert (await postgres_raw_db.get("object", item.id))["context"][
                "status"
            ] == "waiting_for_human"
            decision = {
                "work_approval_id": approval.work_approval_id,
                "decision": intervention,
                "decider_id": auth.id,
            }
            for _ in range(2):
                decided, finished = await work_approvals.decide_work_approval_unit(
                    **decision
                )
                assert decided.status == intervention
                assert finished.status == (
                    "expired" if intervention == "expired" else "failed"
                )
            runs = await postgres_raw_db.find(
                "object", {"entity": "AgentRun", "context.run_id": context.run_id}
            )
            assert len(runs) == 1
            assert runs[0]["context"]["status"] == "failed"
            assert (
                runs[0]["context"]["error"]["code"] == f"work.approval_{intervention}"
            )
            assert not await Entry.find({"track_id": track.id})
            return
        entered, release = asyncio.Event(), asyncio.Event()

        async def hold_before_commit(**kwargs):
            result = await original_propose(**kwargs)
            entered.set()
            await release.wait()
            return result

        with monkeypatch.context() as patch:
            patch.setattr(
                work_approvals, "propose_work_approval_unit", hold_before_commit
            )
            parking = asyncio.create_task(park_deferred_staged_work(**verify_args))
            await asyncio.wait_for(entered.wait(), timeout=10)
            applying = asyncio.create_task(
                staging_apply.bless_and_execute(user_id=auth.id, token=token)
            )
            try:
                done, _ = await asyncio.wait({applying}, timeout=0.2)
                assert not done, "A decision passed the uncommitted park"
            finally:
                release.set()
            approval, parked = await parking
            outcome = await asyncio.wait_for(applying, timeout=20)
        assert parked.status == "waiting_for_human"
        assert approval.framework_snapshot_id == frontier.idempotency_key
        assert approval.framework_tool_call_id == "native-probe-call"
        assert approval.framework_tool_name == "integral_create_entry"
        assert approval.deferred_intent_fingerprint
        if thread_node is not None:
            assert (await postgres_raw_db.get("node", thread))["context"][
                "active_work_item_id"
            ] == ""
            slots = await postgres_raw_db.find(
                "object",
                {
                    "entity": "HarnessTurnAdmissionSlot",
                    "context.work_item_id": item.work_item_id,
                },
            )
            assert slots == []
        assert outcome["consumed"] is True, outcome
        assert len(await Entry.find({"track_id": track.id})) == 1
        if thread_node is not None:
            resumed_item = await work_items.claim_due_candidate(
                worker_id="deferred-resume-probe",
                work_item_id=item.work_item_id,
                lease_seconds=60,
            )
            assert resumed_item is not None
            resumed_context = work_execution.build_work_execution_context(
                work_item=resumed_item,
                logical_step_key=work_execution.logical_step_key_for(kind="chat_turn"),
            )
            recovered = await read_deferred_applied_chat(
                context=resumed_context,
                input_schema=current_core_deferred_schema("integral_create_entry"),
            )
            assert recovered.scope == scope
            assert recovered.tool_call_id == "native-probe-call"
            with pytest.raises(
                WorkError, match="Deferred outcome needs reconciliation"
            ):
                await read_deferred_applied_chat(
                    context=resumed_context, input_schema={"type": "object"}
                )
            from app.agentive.tooling.catalogue import build_tool_catalogue

            catalogue = build_tool_catalogue()
            original_schema = current_core_deferred_schema("integral_create_entry")
            changed_catalogue = deepcopy(catalogue)
            changed_item = next(
                row
                for row in changed_catalogue
                if row["name"] == "integral_create_entry"
            )
            changed_item["input_schema"]["properties"]["new_field"] = {"type": "string"}
            with monkeypatch.context() as patch:
                patch.setattr(
                    "app.agentive.tooling.catalogue.build_tool_catalogue",
                    lambda: changed_catalogue,
                )
                with pytest.raises(
                    WorkError, match="Deferred outcome needs reconciliation"
                ):
                    await read_deferred_applied_chat(
                        context=resumed_context, input_schema=original_schema
                    )
            changed_catalogue = deepcopy(catalogue)
            changed_item = next(
                row
                for row in changed_catalogue
                if row["name"] == "integral_create_entry"
            )
            changed_item["policy_action"] = "entry.different_action"
            with monkeypatch.context() as patch:
                patch.setattr(
                    "app.agentive.tooling.catalogue.build_tool_catalogue",
                    lambda: changed_catalogue,
                )
                with pytest.raises(
                    WorkError, match="Deferred outcome needs reconciliation"
                ):
                    await read_deferred_applied_chat(
                        context=resumed_context, input_schema=original_schema
                    )
            assert (
                recovered.result["entry_id"] == outcome["execute_result"]["entry"]["id"]
            )
            from app.services.permissions import resolve_role

            async def deny_created_entry(user_id, kind, resource_id, **kwargs):
                if kind == "entry" and resource_id == recovered.result["entry_id"]:
                    return None
                return await resolve_role(user_id, kind, resource_id, **kwargs)

            with monkeypatch.context() as patch:
                patch.setattr(
                    "app.services.permissions.resolve_role", deny_created_entry
                )
                with pytest.raises(
                    WorkError, match="Deferred outcome needs reconciliation"
                ):
                    await read_deferred_applied_chat(
                        context=resumed_context,
                        input_schema=current_core_deferred_schema(
                            "integral_create_entry"
                        ),
                    )
            thread_doc = await postgres_raw_db.get("node", thread)
            original_revision = thread_doc["context"]["updated_at"]
            await postgres_raw_db.find_one_and_update(
                "node",
                {"id": thread},
                {"$set": {"context.updated_at": "changed-by-user"}},
            )
            with pytest.raises(
                WorkError, match="Deferred outcome needs reconciliation"
            ):
                await read_deferred_applied_chat(
                    context=resumed_context,
                    input_schema=current_core_deferred_schema("integral_create_entry"),
                )
            await postgres_raw_db.find_one_and_update(
                "node",
                {"id": thread},
                {"$set": {"context.updated_at": original_revision}},
            )
            from app.agentive.staging_store import _decision_record_id

            session_node_id = thread_doc["context"]["active_harness_session_id"]
            for collection, record_id, field in (
                ("node", session_node_id, "status"),
                ("object", _decision_record_id(token), "intent_fingerprint"),
                ("object", recovered.receipt_id, "request_hash"),
                ("object", recovered.receipt_id, "result_ciphertext"),
                ("object", step.id, "input_fingerprint"),
            ):
                original = (await postgres_raw_db.get(collection, record_id))[
                    "context"
                ][field]
                await postgres_raw_db.find_one_and_update(
                    collection,
                    {"id": record_id},
                    {"$set": {f"context.{field}": "changed"}},
                )
                with pytest.raises(WorkError):
                    await read_deferred_applied_chat(
                        context=resumed_context,
                        input_schema=current_core_deferred_schema(
                            "integral_create_entry"
                        ),
                    )
                await postgres_raw_db.find_one_and_update(
                    collection,
                    {"id": record_id},
                    {"$set": {f"context.{field}": original}},
                )
            from app.services.credential_crypto import encrypt_secret_for_storage

            original_ciphertext = (
                await postgres_raw_db.get("object", recovered.receipt_id)
            )["context"]["result_ciphertext"]
            await postgres_raw_db.find_one_and_update(
                "object",
                {"id": recovered.receipt_id},
                {
                    "$set": {
                        "context.result_ciphertext": encrypt_secret_for_storage(
                            json.dumps({"entry_id": "substituted-before-admission"}),
                            aad=recovered.receipt_id,
                        )
                    }
                },
            )
            with pytest.raises(
                WorkError, match="Deferred outcome needs reconciliation"
            ):
                await read_deferred_applied_chat(
                    context=resumed_context,
                    input_schema=current_core_deferred_schema("integral_create_entry"),
                )
            await postgres_raw_db.find_one_and_update(
                "object",
                {"id": recovered.receipt_id},
                {"$set": {"context.result_ciphertext": original_ciphertext}},
            )
            with pytest.raises(WorkError) as stale_resume:
                await read_deferred_applied_chat(
                    context=resumed_context.model_copy(update={"lease_token": "stale"}),
                    input_schema=current_core_deferred_schema("integral_create_entry"),
                )
            assert stale_resume.value.code == "work.lease_lost"

            async def changed_workspace_role(*_args: Any, **_kwargs: Any):
                return (
                    "viewer"
                    if approval.deferred_workspace_role != "viewer"
                    else "member"
                )

            with monkeypatch.context() as patch:
                patch.setattr(
                    "app.services.workspace_permissions.can_access_workspace",
                    changed_workspace_role,
                )
                with pytest.raises(
                    WorkError, match="Deferred outcome needs reconciliation"
                ):
                    await read_deferred_applied_chat(
                        context=resumed_context,
                        input_schema=current_core_deferred_schema(
                            "integral_create_entry"
                        ),
                    )

        async def resumed_model(_messages, _info):
            yield "Continued after the approved effect."

        resumed = Agent(
            FunctionModel(stream_function=resumed_model), tools=[integral_create_entry]
        )
        async with resumed.run_stream_events(
            message_history=frontier.messages,
            deferred_tool_results=DeferredToolResults(
                calls={"native-probe-call": outcome}
            ),
        ) as stream:
            resumed_events = [event async for event in stream]
        assert any(
            type(event).__name__ == "AgentRunResultEvent" for event in resumed_events
        )
        assert len(broker_results) == 1
        assert len(await Entry.find({"track_id": track.id})) == 1

        # Reconstruct the model result from committed Core records in another
        # interpreter; do not pass the in-memory outcome or model history.
        worker = """
import asyncio, json, os, sys
from jvspatial.core.context import GraphContext, set_default_context
from jvspatial.db.factory import create_database
from pydantic_ai import Agent
from pydantic_ai.messages import ModelRequest, ToolReturnPart
from pydantic_ai.models.function import FunctionModel
from pydantic_ai.tools import DeferredToolResults
from app.agentive.harness.contracts import HarnessExecutionScope
from app.agentive.harness.jvspatial_store import JvSpatialStepStore
from app.agentive.harness.scoped_store import ScopedStepStore
from app.agentive.services.staging_execution import execution_record_id
from app.agentive.staging_store import _decision_record_id
from app.agentive.services.work_approvals import deterministic_work_approval_id, work_approval_object_id
from app.services.credential_crypto import decrypt_secret_from_storage

async def integral_create_entry(track_id: str, title: str):
    raise AssertionError('an approved call was dispatched again')

async def main():
    scope = HarnessExecutionScope.model_validate_json(sys.argv[1])
    work_id, token, step_id = sys.argv[2:5]
    db = create_database(db_type='postgres')
    set_default_context(GraphContext(database=db))
    try:
        approval_id = deterministic_work_approval_id(work_item_id=work_id, staging_token=token)
        link = (await db.get('object', work_approval_object_id(approval_id)))['context']
        decision = (await db.get('object', _decision_record_id(token)))['context']
        receipt_id = execution_record_id(token)
        receipt = (await db.get('object', receipt_id))['context']
        step = (await db.get('object', step_id))['context']
        assert link['status'] == 'approved' and link['run_id'] == scope.run_id
        assert link['staging_execution_id'] == receipt_id
        assert link['staging_intent_fingerprint'] == decision['intent_fingerprint']
        assert decision['state'] == 'consumed' and decision['user_id'] == scope.principal_id
        assert receipt['state'] == 'applied' and receipt['principal_id'] == scope.principal_id
        assert receipt['workspace_id'] == scope.workspace_id
        assert receipt['request_hash'] == link['staging_request_hash']
        assert step['work_item_id'] == work_id and step['approval_ref'] == token
        applied = json.loads(decrypt_secret_from_storage(receipt['result_ciphertext'], aad=receipt_id))
        async def model(messages, info):
            returned = [part for msg in messages if isinstance(msg, ModelRequest)
                        for part in msg.parts if isinstance(part, ToolReturnPart)]
            assert any(part.tool_call_id == 'native-probe-call' and part.content == applied
                       for part in returned)
            yield 'Continued after the durable receipt.'
        store = ScopedStepStore(store=JvSpatialStepStore(scope=scope), scope=scope)
        frontier = await store.latest_snapshot(run_id=scope.run_id, include_interrupted=True)
        assert frontier is not None and frontier.state == 'interrupted'
        agent = Agent(FunctionModel(stream_function=model), tools=[integral_create_entry])
        async with agent.run_stream_events(message_history=frontier.messages,
                deferred_tool_results=DeferredToolResults(calls={'native-probe-call': applied})) as stream:
            events = [type(event).__name__ async for event in stream]
        assert 'AgentRunResultEvent' in events
        print('__DEFERRED_PROBE__' + json.dumps({'pid': os.getpid(), 'receipt': receipt_id}))
    finally:
        await db.close()

asyncio.run(main())
"""
        child = await asyncio.create_subprocess_exec(
            sys.executable,
            "-c",
            worker,
            scope.model_dump_json(),
            item.work_item_id,
            token,
            step.id,
            env={**os.environ, "EMBEDDING_STORE_DRIVER": "null"},
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            stdout, stderr = await asyncio.wait_for(child.communicate(), timeout=60)
        except BaseException:
            child.kill()
            await child.wait()
            raise
        assert child.returncode == 0, stderr.decode()
        result_lines = [
            line
            for line in stdout.decode().splitlines()
            if line.startswith("__DEFERRED_PROBE__")
        ]
        assert len(result_lines) == 1
        assert (
            json.loads(result_lines[0].removeprefix("__DEFERRED_PROBE__"))["pid"]
            != os.getpid()
        )
        assert len(await Entry.find({"track_id": track.id})) == 1
        if thread_node is not None and intervention in {"race", "cas"}:
            from app.agentive.harness import deferred_staging
            from app.api.errors import ResourceConflictError
            from app.services import chat_turn_admission

            reconciled, release_admission = asyncio.Event(), asyncio.Event()
            original_read = deferred_staging.read_deferred_applied_chat

            async def wait_after_reconciliation(**kwargs):
                result = await original_read(**kwargs)
                reconciled.set()
                await release_admission.wait()
                return result

            original_reserve = chat_turn_admission.reserve_chat_turn_admission

            async def wait_before_reservation(**kwargs):
                if kwargs["work_item"].work_item_id == item.work_item_id:
                    reconciled.set()
                    await release_admission.wait()
                return await original_reserve(**kwargs)

            with monkeypatch.context() as patch:
                if intervention == "race":
                    patch.setattr(
                        deferred_staging,
                        "read_deferred_applied_chat",
                        wait_after_reconciliation,
                    )
                else:
                    patch.setattr(
                        chat_turn_admission,
                        "reserve_chat_turn_admission",
                        wait_before_reservation,
                    )
                admitting = asyncio.create_task(
                    admit_deferred_chat_resume(
                        context=resumed_context,
                        input_schema=current_core_deferred_schema(
                            "integral_create_entry"
                        ),
                    )
                )
                await asyncio.wait_for(reconciled.wait(), timeout=10)
                try:
                    newer = await asyncio.wait_for(
                        submit_chat_turn(
                            ChatTurnSubmissionRequest(
                                principal_id=auth.id,
                                workspace_id=workspace.id,
                                thread_id=thread,
                                client_request_id="racing-" + unrelated.token,
                                parts=[{"type": "text", "text": "Change the plan."}],
                            )
                        ),
                        timeout=10,
                    )
                finally:
                    release_admission.set()
                assert newer.work_item_id != item.work_item_id
                with pytest.raises((WorkError, ResourceConflictError)):
                    await asyncio.wait_for(admitting, timeout=10)
            row = await postgres_raw_db.get("node", thread)
            assert row["context"]["active_work_item_id"] == newer.work_item_id
            original_slots = await postgres_raw_db.find(
                "object",
                {
                    "entity": "HarnessTurnAdmissionSlot",
                    "context.work_item_id": item.work_item_id,
                },
            )
            assert original_slots == []
        elif thread_node is not None and intervention is True:
            newer = await submit_chat_turn(
                ChatTurnSubmissionRequest(
                    principal_id=auth.id,
                    workspace_id=workspace.id,
                    thread_id=thread,
                    client_request_id="intervening-" + unrelated.token,
                    parts=[{"type": "text", "text": "Change the plan."}],
                )
            )
            assert newer.work_item_id != item.work_item_id
            with pytest.raises(
                WorkError, match="Deferred outcome needs reconciliation"
            ):
                await read_deferred_applied_chat(
                    context=resumed_context,
                    input_schema=current_core_deferred_schema("integral_create_entry"),
                )
            with pytest.raises(
                WorkError, match="Deferred outcome needs reconciliation"
            ):
                await admit_deferred_chat_resume(
                    context=resumed_context,
                    input_schema=current_core_deferred_schema("integral_create_entry"),
                )
            assert len(await Entry.find({"track_id": track.id})) == 1
        elif thread_node is not None:
            from app.api.errors import ResourceConflictError

            async def fail_after_admission(*_args: Any, **_kwargs: Any):
                raise WorkError("work.policy_denied", "injected recheck failure")

            with monkeypatch.context() as patch:
                patch.setattr(
                    "app.services.chat_turn_worker_input.load_claimed_chat_turn_input",
                    fail_after_admission,
                )
                with pytest.raises(WorkError, match="injected recheck failure"):
                    await admit_deferred_chat_resume(
                        context=resumed_context,
                        input_schema=current_core_deferred_schema(
                            "integral_create_entry"
                        ),
                    )
            assert (await postgres_raw_db.get("node", thread))["context"][
                "active_work_item_id"
            ] == ""
            assert not await postgres_raw_db.find(
                "object",
                {
                    "entity": "HarnessTurnAdmissionSlot",
                    "context.work_item_id": item.work_item_id,
                },
            )
            original_record = ScopedStepStore.record_tool_effect

            async def fail_after_effect_write(store_instance, record):
                await original_record(store_instance, record)
                if record.status == "completed":
                    raise WorkError("work.policy_denied", "injected settlement failure")

            with monkeypatch.context() as patch:
                patch.setattr(
                    ScopedStepStore, "record_tool_effect", fail_after_effect_write
                )
                with pytest.raises(WorkError, match="injected settlement failure"):
                    await admit_deferred_chat_resume(
                        context=resumed_context,
                        input_schema=current_core_deferred_schema(
                            "integral_create_entry"
                        ),
                    )
            assert (
                await store.get_tool_effect(
                    run_id=scope.run_id, tool_call_id="native-probe-call"
                )
            ).status == "started"
            assert (await postgres_raw_db.get("node", thread))["context"][
                "active_work_item_id"
            ] == ""
            assert not await postgres_raw_db.find(
                "object",
                {
                    "entity": "HarnessTurnAdmissionSlot",
                    "context.work_item_id": item.work_item_id,
                },
            )
            crash_worker = """
import asyncio, os, sys
from jvspatial.core.context import GraphContext, set_default_context
from jvspatial.db.factory import create_database
from app.agentive.harness.deferred_staging import admit_deferred_chat_resume, current_core_deferred_schema
from app.schemas.agentive.work import WorkExecutionContext
async def main():
    db = create_database(db_type='postgres')
    set_default_context(GraphContext(database=db))
    await admit_deferred_chat_resume(context=WorkExecutionContext.model_validate_json(sys.argv[1]), input_schema=current_core_deferred_schema("integral_create_entry"))
    os._exit(77)
asyncio.run(main())
"""
            crashed = await asyncio.create_subprocess_exec(
                sys.executable,
                "-c",
                crash_worker,
                resumed_context.model_dump_json(),
                env={**os.environ, "EMBEDDING_STORE_DRIVER": "null"},
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            _, crash_stderr = await asyncio.wait_for(crashed.communicate(), timeout=60)
            assert crashed.returncode == 77, crash_stderr.decode()
            settled_effect = await store.get_tool_effect(
                run_id=scope.run_id, tool_call_id="native-probe-call"
            )
            assert settled_effect.status == "completed"
            assert settled_effect.effect_summary == recovered.settlement_summary
            assert await store.list_unresolved_tool_effects(run_id=scope.run_id) == []
            if intervention == "settlement_changed":
                unguarded_store = ScopedStepStore(
                    store=JvSpatialStepStore(scope=scope), scope=scope
                )
                await unguarded_store.record_tool_effect(
                    replace(settled_effect, effect_summary="foreign receipt")
                )
            if intervention == "receipt_changed":
                from app.services.credential_crypto import encrypt_secret_for_storage

                await postgres_raw_db.find_one_and_update(
                    "object",
                    {"id": recovered.receipt_id},
                    {
                        "$set": {
                            "context.result_ciphertext": encrypt_secret_for_storage(
                                json.dumps({"entry_id": "substituted"}),
                                aad=recovered.receipt_id,
                            )
                        }
                    },
                )
            assert (await postgres_raw_db.get("node", thread))["context"][
                "active_work_item_id"
            ] == item.work_item_id
            slots = await postgres_raw_db.find(
                "object",
                {
                    "entity": "HarnessTurnAdmissionSlot",
                    "context.work_item_id": item.work_item_id,
                },
            )
            assert len(slots) == 1
            if intervention == "dispatch":
                await postgres_raw_db.insert_if_absent(
                    "object",
                    {
                        "id": "o.HarnessModelRequestRecord.test-" + unrelated.token,
                        "entity": "HarnessModelRequestRecord",
                        "context": {
                            "run_key": scope.run_id,
                            "outcome": "dispatch_intent",
                        },
                    },
                )
            if intervention in {
                "correction",
                "correction_changed",
                "correction_scope",
                "rotation",
            }:
                from app.schemas.agentive.steering import SteeringCorrectionRequest
                from app.services.chat_steering import (
                    _inbox_id,
                    accept_steering_correction,
                )

                await accept_steering_correction(
                    item.work_item_id,
                    auth.id,
                    SteeringCorrectionRequest(
                        client_request_id="recovery-correction",
                        text="Use the new destination.",
                    ),
                )
                if intervention == "correction":
                    await accept_steering_correction(
                        item.work_item_id,
                        auth.id,
                        SteeringCorrectionRequest(
                            client_request_id="recovery-correction-2",
                            text="Keep the answer concise.",
                        ),
                    )
                if intervention == "correction_changed":
                    from app.services.credential_crypto import (
                        encrypt_secret_for_storage,
                    )

                    await admit_deferred_chat_resume(
                        context=resumed_context,
                        input_schema=current_core_deferred_schema(
                            "integral_create_entry"
                        ),
                    )
                    ciphertext = encrypt_secret_for_storage(
                        json.dumps({"recovery-correction": "Substituted correction."}),
                        aad=_inbox_id(item.work_item_id),
                    )
                    await postgres_raw_db.find_one_and_update(
                        "object",
                        {"id": _inbox_id(item.work_item_id)},
                        {"$set": {"context.payload_ciphertext": ciphertext}},
                    )
                elif intervention == "correction_scope":
                    await postgres_raw_db.find_one_and_update(
                        "object",
                        {"id": _inbox_id(item.work_item_id)},
                        {"$set": {"context.scope_key": "foreign"}},
                    )
                elif intervention == "rotation":
                    from app.agentive.harness.key_rotation import (
                        rewrap_harness_session_records,
                    )
                    from app.agentive.harness.turn_input import thread_scope_key
                    from app.services.credential_crypto import (
                        decrypt_secret_from_storage,
                    )

                    await admit_deferred_chat_resume(
                        context=resumed_context,
                        input_schema=current_core_deferred_schema(
                            "integral_create_entry"
                        ),
                    )
                    link = await postgres_raw_db.get("object", approval.id)
                    baseline_id = link["context"]["deferred_steering_baseline_id"]
                    monkeypatch.setenv("INTEGRAL_CREDENTIAL_ENC_KEY", "33" * 32)
                    monkeypatch.setenv(
                        "INTEGRAL_CREDENTIAL_ENC_KEY_PREVIOUS", "22" * 32
                    )
                    report = await rewrap_harness_session_records(
                        scope_key=thread_scope_key(
                            principal_id=auth.id,
                            workspace_id=workspace.id,
                            thread_id=thread,
                        )
                    )
                    assert report["HarnessSteeringResumeBaseline"] == 1
                    monkeypatch.delenv("INTEGRAL_CREDENTIAL_ENC_KEY_PREVIOUS")
                    baseline = await postgres_raw_db.get("object", baseline_id)
                    assert json.loads(
                        decrypt_secret_from_storage(
                            baseline["context"]["payload_ciphertext"], aad=baseline_id
                        )
                    ) == {"recovery-correction": "Use the new destination."}
                    monkeypatch.setenv(
                        "INTEGRAL_CREDENTIAL_ENC_KEY_PREVIOUS", "22" * 32
                    )
            await work_items.force_expire_lease_for_tests(item.work_item_id)
            worker = """
import asyncio, json, sys
from jvspatial.core.context import GraphContext, set_default_context
from jvspatial.db.factory import create_database
from pydantic_ai import Agent
from pydantic_ai.messages import ModelRequest, ToolReturnPart, UserPromptPart
from pydantic_ai.models.function import FunctionModel
from app.services.chat_turn_worker_input import load_claimed_chat_turn_input
from app.agentive.services import work_execution, work_items
from app.agentive.harness.deferred_staging import admit_deferred_chat_resume, current_core_deferred_schema
from app.agentive.harness.steering import DurableSteering
from app.schemas.agentive.work import WorkError
async def main():
    db = create_database(db_type='postgres')
    set_default_context(GraphContext(database=db))
    try:
        item = await work_items.reclaim_expired_lease(sys.argv[1], worker_id='resume-recovery')
        context = work_execution.build_work_execution_context(work_item=item, logical_step_key=work_execution.logical_step_key_for(kind='chat_turn'))
        try:
            result = await admit_deferred_chat_resume(context=context, input_schema=current_core_deferred_schema("integral_create_entry"))
            response = {'state': 'admitted', 'receipt': result.receipt_id, 'fence': context.lease_fence}
            if sys.argv[2] == 'correction':
                received = []
                async def model(messages, info):
                    received.append([part.content for message in messages if isinstance(message, ModelRequest) for part in message.parts if isinstance(part, UserPromptPart)])
                    assert any(part.tool_call_id == result.tool_call_id and part.content == result.result for message in messages if isinstance(message, ModelRequest) for part in message.parts if isinstance(part, ToolReturnPart))
                    yield 'Continued with the correction.'
                async def integral_create_entry(track_id: str, title: str):
                    raise AssertionError('approved tool was dispatched again')
                agent = Agent(FunctionModel(stream_function=model), tools=[integral_create_entry], capabilities=[DurableSteering(context)])
                accepted = await load_claimed_chat_turn_input(item)
                history, deferred_results = result.native_resume_input(accepted_text=accepted.text)
                assert len(history) == 2 and history[-1].parts[0].args == {}
                async with agent.run_stream_events(message_history=history, deferred_tool_results=deferred_results) as stream:
                    events = [type(event).__name__ async for event in stream]
                assert 'AgentRunResultEvent' in events
                response['received'] = received
            print('__RESULT__' + json.dumps(response))
        except WorkError as exc:
            print('__RESULT__' + json.dumps({'state': 'refused', 'code': exc.code}))
    finally:
        await db.close()
asyncio.run(main())
"""
            child = await asyncio.create_subprocess_exec(
                sys.executable,
                "-c",
                worker,
                item.work_item_id,
                str(intervention),
                env={**os.environ, "EMBEDDING_STORE_DRIVER": "null"},
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, stderr = await asyncio.wait_for(child.communicate(), timeout=60)
            assert child.returncode == 0, stderr.decode()
            result_line = next(
                line
                for line in stdout.decode().splitlines()
                if line.startswith("__RESULT__")
            )
            child_result = json.loads(result_line.removeprefix("__RESULT__"))
            if intervention in {
                "dispatch",
                "correction_changed",
                "correction_scope",
                "settlement_changed",
                "receipt_changed",
            }:
                assert child_result == {
                    "state": "refused",
                    "code": "work.non_replayable_effect",
                }
            else:
                assert child_result["state"] == "admitted"
                assert child_result["receipt"] == recovered.receipt_id
                assert child_result["fence"] > resumed_context.lease_fence
                if intervention == "correction":
                    assert any(
                        [
                            part
                            for part in call
                            if part
                            in {"Use the new destination.", "Keep the answer concise."}
                        ]
                        == ["Use the new destination.", "Keep the answer concise."]
                        for call in child_result["received"]
                    )
                    from app.services.chat_turn_events import (
                        replay_work_item_chat_events,
                    )

                    replay = await replay_work_item_chat_events(
                        principal_id=auth.id,
                        workspace_id=workspace.id,
                        thread_id=thread,
                        work_item_id=item.work_item_id,
                    )
                    assert [
                        event["text"]
                        for event in replay.events
                        if event["type"] == "steering-delivered"
                    ] == ["Use the new destination.", "Keep the answer concise."]
                transitions = [
                    record
                    for record in await store.list_tool_effects(run_id=scope.run_id)
                    if record.tool_call_id == "native-probe-call"
                    and record.status == "completed"
                ]
                assert len(transitions) == 1
            with pytest.raises(WorkError):
                await admit_deferred_chat_resume(
                    context=resumed_context,
                    input_schema=current_core_deferred_schema("integral_create_entry"),
                )
            with pytest.raises(ResourceConflictError):
                await submit_chat_turn(
                    ChatTurnSubmissionRequest(
                        principal_id=auth.id,
                        workspace_id=workspace.id,
                        thread_id=thread,
                        client_request_id="after-admission-" + unrelated.token,
                        parts=[{"type": "text", "text": "Another message."}],
                    )
                )

            if intervention == "correction":
                from jvspatial.core.context import get_default_context

                from app.agentive.services.work_worker import execute_claimed_work

                class ResumingProvider:
                    def get(self, provider_id):
                        assert provider_id == "integral_native"
                        return self

                    def is_available(self):
                        return True

                    async def stream_turn(self, turn):
                        assert turn.deferred_resume is not None
                        assert turn.deferred_resume.receipt_id == recovered.receipt_id
                        assert turn.deferred_resume.result == recovered.result
                        yield {
                            "type": "text-delta",
                            "delta": "Continued from the approved result.",
                        }
                        yield {"type": "message-finish"}

                monkeypatch.setattr(
                    "app.services.chat_providers.get_registry", ResumingProvider
                )
                await get_default_context()._evict_from_cache(item.id)
                reclaimed = await WorkItem.get(item.id)
                finished = await execute_claimed_work(
                    reclaimed, worker_id="resume-recovery", lease_seconds=60
                )
                assert finished.status == "succeeded", finished.failure
                runs = await postgres_raw_db.find(
                    "object",
                    {"entity": "AgentRun", "context.work_item_id": item.work_item_id},
                )
                assert len(runs) == 2
                assert all(run["context"]["status"] == "succeeded" for run in runs)
                assert len(await Entry.find({"track_id": track.id})) == 1


async def test_foreign_caller_cannot_decide_linked_work(postgres_raw_db, monkeypatch):
    """A foreign decision leaves the real approval and queued effect untouched."""
    monkeypatch.setenv("INTEGRAL_CREDENTIAL_ENC_KEY", "22" * 32)
    async with scoped_default_context_async(GraphContext(database=postgres_raw_db)):
        auth, track, sc, item, approval = await _linked_proposal()

        with pytest.raises(StagingError) as error:
            await staging_apply.bless_and_execute(user_id="other", token=sc.token)
        assert error.value.code == "wrong_user"
        assert (await WorkApproval.get(approval.id)).status == "pending"
        assert (await WorkItem.get(item.id)).status == "waiting_for_human"
        assert await Entry.find({"track_id": track.id}) == []

        applied = await staging_apply.bless_and_execute(user_id=auth.id, token=sc.token)
        assert applied["consumed"] is True
        assert (await WorkApproval.get(approval.id)).status == "approved"
        assert (await WorkItem.get(item.id)).status == "queued"
        assert len(await Entry.find({"track_id": track.id})) == 1


async def test_deferred_intent_mismatch_cannot_apply_effect(
    postgres_raw_db, monkeypatch
):
    """A proposal changed after parking cannot acquire the parked authority."""
    monkeypatch.setenv("INTEGRAL_CREDENTIAL_ENC_KEY", "22" * 32)
    async with scoped_default_context_async(GraphContext(database=postgres_raw_db)):
        auth, track, sc, item, approval = await _linked_proposal()
        await postgres_raw_db.find_one_and_update(
            "object",
            {"id": approval.id},
            {"$set": {"context.deferred_intent_fingerprint": "different-intent"}},
        )
        result = await staging_apply.bless_and_execute(user_id=auth.id, token=sc.token)
        assert result["consumed"] is False
        assert await Entry.find({"track_id": track.id}) == []
        assert (await postgres_raw_db.get("object", item.id))["context"]["status"] == (
            "waiting_for_human"
        )
        assert (await postgres_raw_db.get("object", approval.id))["context"][
            "status"
        ] == ("pending")


async def test_work_waits_until_applied_decision_commits(postgres_raw_db, monkeypatch):
    """A worker cannot claim the linked work while application is finishing."""
    monkeypatch.setenv("INTEGRAL_CREDENTIAL_ENC_KEY", "22" * 32)
    entered, resume = asyncio.Event(), asyncio.Event()
    consume = staging_apply.consume_token

    async def pause_before_consumption(**kwargs):
        entered.set()
        await resume.wait()
        return await consume(**kwargs)

    monkeypatch.setattr(staging_apply, "consume_token", pause_before_consumption)
    async with scoped_default_context_async(GraphContext(database=postgres_raw_db)):
        auth, track, sc, item, approval = await _linked_proposal()
        applying = asyncio.create_task(
            staging_apply.bless_and_execute(user_id=auth.id, token=sc.token)
        )
        try:
            await asyncio.wait_for(entered.wait(), timeout=10)
            assert (await postgres_raw_db.get("object", item.id))["context"][
                "status"
            ] == ("waiting_for_human")
            assert (await postgres_raw_db.get("object", approval.id))["context"][
                "status"
            ] == "pending"
            assert (
                await work_items.claim_due_candidate(
                    worker_id="racing-worker",
                    work_item_id=item.work_item_id,
                    lease_seconds=60,
                )
                is None
            )
            assert len(await Entry.find({"track_id": track.id})) == 1
        finally:
            resume.set()
        result = await applying
        assert result["consumed"] is True
        assert (await postgres_raw_db.get("object", item.id))["context"][
            "status"
        ] == "queued"
        assert (await postgres_raw_db.get("object", approval.id))["context"][
            "status"
        ] == ("approved")


@pytest.mark.parametrize(
    "change", ["principal_id", "workspace_id", "thread_id", "cancel_requested_at"]
)
async def test_changed_work_scope_does_not_resume(postgres_raw_db, monkeypatch, change):
    """Changed work scope or cancellation prevents the canonical graph effect."""
    monkeypatch.setenv("INTEGRAL_CREDENTIAL_ENC_KEY", "22" * 32)
    async with scoped_default_context_async(GraphContext(database=postgres_raw_db)):
        auth, track, sc, item, approval = await _linked_proposal()
        await postgres_raw_db.find_one_and_update(
            "object", {"id": item.id}, {"$set": {f"context.{change}": "changed"}}
        )
        result = await staging_apply.bless_and_execute(user_id=auth.id, token=sc.token)
        assert result["execute_result"]["error_code"] == "approval_unavailable"
        assert (await postgres_raw_db.get("object", item.id))["context"]["status"] == (
            "waiting_for_human"
        )
        assert (await postgres_raw_db.get("object", approval.id))["context"][
            "status"
        ] == "pending"
        assert await Entry.find({"track_id": track.id}) == []


async def test_cancelled_work_is_not_externally_dispatched(
    postgres_raw_db, monkeypatch
):
    """A cancelled linked job cannot send its first extension dispatch."""
    from app.agentive.services.staging_execution import execution_record_id
    from app.services.hooks import registry

    monkeypatch.setenv("INTEGRAL_CREDENTIAL_ENC_KEY", "22" * 32)
    monkeypatch.setattr(registry, "get_workspace_hooks", lambda *_: [{"mode": "tool"}])
    async with scoped_default_context_async(GraphContext(database=postgres_raw_db)):
        auth, track, sc, item, approval = await _linked_proposal()
        await work_items.cancel_work_item(item.work_item_id)
        with pytest.raises(StagingError) as error:
            await staging_apply.bless_and_execute(user_id=auth.id, token=sc.token)
        assert error.value.code == "approval_unavailable"
        execution = await postgres_raw_db.get("object", execution_record_id(sc.token))
        assert execution["context"]["state"] == "ready"
        assert execution["context"]["external_outcome"] == "not_dispatched"
        assert (await postgres_raw_db.get("object", approval.id))["context"][
            "status"
        ] == "pending"
        assert await Entry.find({"track_id": track.id}) == []


async def test_cancelled_work_stops_batch_before_first_step(
    postgres_raw_db, monkeypatch
):
    """A cancelled parent also fences the batch's first child effect."""
    from tests.contract.test_staging_command_batch_postgres import batch

    monkeypatch.setenv("INTEGRAL_CREDENTIAL_ENC_KEY", "22" * 32)
    async with scoped_default_context_async(GraphContext(database=postgres_raw_db)):
        auth, track, sc = await batch()
        item, _ = await _park(auth, sc.workspace_id, sc)
        await work_items.cancel_work_item(item.work_item_id)
        result = await staging_apply.bless_and_execute(user_id=auth.id, token=sc.token)
        assert result["consumed"] is False
        assert await Entry.find({"track_id": track.id}) == []


async def test_cancelled_work_stops_app_operation(postgres_raw_db, monkeypatch):
    """The same linked-work fence applies to App-operation admission."""
    from app.services.app_operations.registry import unregister_app_operations
    from tests.contract.test_staged_app_operation_postgres import proposal as operation

    monkeypatch.setenv("INTEGRAL_CREDENTIAL_ENC_KEY", "22" * 32)
    async with scoped_default_context_async(GraphContext(database=postgres_raw_db)):
        auth, workspace, app, sc, calls = await operation(monkeypatch)
        try:
            item, _ = await _park(auth, workspace.id, sc)
            await work_items.cancel_work_item(item.work_item_id)
            result = await staging_apply.bless_and_execute(
                user_id=auth.id, token=sc.token
            )
            assert result["consumed"] is False
            assert calls == []
        finally:
            unregister_app_operations(workspace.id, app.id)


async def test_cancel_racing_admitted_effect_waits_for_commit(
    postgres_raw_db, monkeypatch
):
    """Cancellation cannot interleave with a graph effect after its work lock."""
    from app.agentive.services import entry_proposal_revision

    monkeypatch.setenv("INTEGRAL_CREDENTIAL_ENC_KEY", "22" * 32)
    entered, release, cancelled = asyncio.Event(), asyncio.Event(), asyncio.Event()
    validate = entry_proposal_revision.validate_entry_edit_fence
    consume = staging_apply.consume_token

    async def pause_after_work_lock(*args: Any, **kwargs: Any):
        await validate(*args, **kwargs)
        entered.set()
        await release.wait()

    async def wait_for_cancel(**kwargs):
        await cancelled.wait()
        return await consume(**kwargs)

    monkeypatch.setattr(
        entry_proposal_revision, "validate_entry_edit_fence", pause_after_work_lock
    )
    monkeypatch.setattr(staging_apply, "consume_token", wait_for_cancel)
    async with scoped_default_context_async(GraphContext(database=postgres_raw_db)):
        auth, track, sc, item, approval = await _linked_proposal()
        applying = asyncio.create_task(
            staging_apply.bless_and_execute(user_id=auth.id, token=sc.token)
        )
        await asyncio.wait_for(entered.wait(), timeout=10)

        async def cancel():
            try:
                return await work_items.cancel_work_item(item.work_item_id)
            finally:
                cancelled.set()

        cancellation = asyncio.create_task(cancel())
        try:
            await asyncio.sleep(0.05)
            assert not cancellation.done()
        finally:
            release.set()
        assert (await asyncio.wait_for(cancellation, timeout=10)).status == "cancelled"
        result = await asyncio.wait_for(applying, timeout=10)
        assert result["application_state"] == "reconciliation_required"
        assert len(await Entry.find({"track_id": track.id})) == 1
        assert (await postgres_raw_db.get("object", approval.id))["context"][
            "status"
        ] == "pending"


async def test_linked_completion_rollback_and_retry(postgres_raw_db, monkeypatch):
    """A failed completion commits neither work nor decision and never replays the effect."""
    monkeypatch.setenv("INTEGRAL_CREDENTIAL_ENC_KEY", "22" * 32)
    approve = work_approvals.approve_work_approval

    async def fail_after_transition(**kwargs):
        await approve(**kwargs)
        raise StagingError("injected_failure", "Fail after linked transition")

    async with scoped_default_context_async(GraphContext(database=postgres_raw_db)):
        auth, track, sc, item, approval = await _linked_proposal()
        monkeypatch.setattr(
            work_approvals, "approve_work_approval", fail_after_transition
        )
        result = await staging_apply.bless_and_execute(user_id=auth.id, token=sc.token)
        assert result["application_state"] == "reconciliation_required"
        assert (await postgres_raw_db.get("object", item.id))["context"]["status"] == (
            "waiting_for_human"
        )
        assert (await postgres_raw_db.get("object", approval.id))["context"][
            "status"
        ] == "pending"
        assert len(await Entry.find({"track_id": track.id})) == 1

        monkeypatch.setattr(work_approvals, "approve_work_approval", approve)
        retried = await staging_apply.bless_and_execute(user_id=auth.id, token=sc.token)
        assert retried["consumed"] is True
        assert len(await Entry.find({"track_id": track.id})) == 1
        assert (await postgres_raw_db.get("object", item.id))["context"][
            "status"
        ] == "queued"
        assert (await postgres_raw_db.get("object", approval.id))["context"][
            "status"
        ] == ("approved")


async def test_new_process_reconciles_linked_committed_effect(
    postgres_raw_db, monkeypatch
):
    """A fresh process completes the saved effect and queues work exactly once."""
    monkeypatch.setenv("INTEGRAL_CREDENTIAL_ENC_KEY", "22" * 32)
    approve = work_approvals.approve_work_approval

    async def fail_after_transition(**kwargs):
        await approve(**kwargs)
        raise StagingError("injected_failure", "Fail after linked transition")

    worker = """
import asyncio, json, os, sys
from jvspatial.core.context import GraphContext, set_default_context
from jvspatial.db.factory import create_database
from app.agentive.services.staging_apply import bless_and_execute

async def main():
    db = create_database(db_type='postgres')
    set_default_context(GraphContext(database=db))
    try:
        result = await bless_and_execute(user_id=sys.argv[1], token=sys.argv[2])
        print('__LINKED_RESULT__' + json.dumps({'pid': os.getpid(), 'result': result}))
    finally:
        await db.close()

asyncio.run(main())
"""
    async with scoped_default_context_async(GraphContext(database=postgres_raw_db)):
        auth, track, sc, item, approval = await _linked_proposal()
        monkeypatch.setattr(
            work_approvals, "approve_work_approval", fail_after_transition
        )
        first = await staging_apply.bless_and_execute(user_id=auth.id, token=sc.token)
        assert first["application_state"] == "reconciliation_required"
        assert len(await Entry.find({"track_id": track.id})) == 1
        assert (await postgres_raw_db.get("object", item.id))["context"]["status"] == (
            "waiting_for_human"
        )

        child = await asyncio.create_subprocess_exec(
            sys.executable,
            "-c",
            worker,
            auth.id,
            sc.token,
            env={**os.environ, "EMBEDDING_STORE_DRIVER": "null"},
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            stdout, stderr = await asyncio.wait_for(child.communicate(), timeout=60)
        except BaseException:
            child.kill()
            await child.wait()
            raise
        assert child.returncode == 0, stderr.decode()
        lines = [
            line
            for line in stdout.decode().splitlines()
            if line.startswith("__LINKED_RESULT__")
        ]
        assert len(lines) == 1
        observed = json.loads(lines[0].removeprefix("__LINKED_RESULT__"))
        assert observed["pid"] != os.getpid()
        assert observed["result"]["consumed"] is True
        assert len(await Entry.find({"track_id": track.id})) == 1
        assert (await postgres_raw_db.get("object", item.id))["context"][
            "status"
        ] == "queued"
        assert (await postgres_raw_db.get("object", approval.id))["context"][
            "status"
        ] == "approved"


@pytest.mark.parametrize("tamper", ["none", "marker", "receipt", "step", "proof"])
async def test_worker_recovers_applied_capability_without_redispatch(
    postgres_raw_db, monkeypatch, tamper
):
    """Only the exact applied approval can complete a later work attempt."""
    from app.agentive.services import capability_broker, work_worker
    from app.agentive.services.execution_runs import RunStep
    from app.agentive.services.staging_execution import execution_record_id
    from app.agentive.services.work_execution import effect_key
    from app.agentive.staging_store import _decision_record_id

    monkeypatch.setenv("INTEGRAL_CREDENTIAL_ENC_KEY", "22" * 32)
    async with scoped_default_context_async(GraphContext(database=postgres_raw_db)):
        auth, track, sc, item, approval = await _linked_proposal()
        step = await RunStep.create(
            run_id="approval-test-run",
            step_key="approval-test-step",
            work_item_id=item.work_item_id,
            principal_id=auth.id,
            workspace_id=sc.workspace_id,
            capability_key="integral_create_entry",
            capability_source="core",
            capability_op_class="propose",
            input_fingerprint=capability_broker._fingerprint({}),
            status="waiting_for_human",
            approval_ref=sc.token,
        )
        await postgres_raw_db.find_one_and_update(
            "object",
            {"id": approval.id},
            {
                "$set": {
                    "context.authority_digest": effect_key(
                        work_item_id=item.work_item_id, logical_step_key="capability:0"
                    )
                }
            },
        )
        applied = await staging_apply.bless_and_execute(user_id=auth.id, token=sc.token)
        assert applied["consumed"] is True
        mutations = {
            "marker": (_decision_record_id(sc.token), "context.intent_fingerprint"),
            "receipt": (execution_record_id(sc.token), "context.request_hash"),
            "step": (step.id, "context.input_fingerprint"),
            "proof": (approval.id, "context.staging_intent_fingerprint"),
        }
        if tamper != "none":
            object_id, field = mutations[tamper]
            await postgres_raw_db.find_one_and_update(
                "object", {"id": object_id}, {"$set": {field: "changed"}}
            )
        claimed = await work_items.claim_due_candidate(
            worker_id="recovery-test", work_item_id=item.work_item_id, lease_seconds=60
        )
        assert claimed is not None

        async def never_dispatch(*args: Any, **kwargs: Any):
            pytest.fail("An applied proposal was dispatched again")

        monkeypatch.setattr(
            capability_broker, "invoke_declared_capability", never_dispatch
        )
        result = await work_worker.execute_claimed_work(
            claimed, worker_id="recovery-test"
        )
        assert result.status == ("succeeded" if tamper == "none" else "failed")
        if tamper != "none":
            assert result.failure["code"] == "work.non_replayable_effect"
        assert len(await Entry.find({"track_id": track.id})) == 1


@pytest.mark.parametrize("crash_after_claim", [False, True])
async def test_real_broker_and_fresh_worker_recover_approved_capability(
    postgres_raw_db, monkeypatch, crash_after_claim
):
    """A real broker proposal survives approval and a new worker process."""
    from app.agentive import staging
    from app.agentive.services import work_worker

    monkeypatch.setenv("INTEGRAL_CREDENTIAL_ENC_KEY", "22" * 32)
    async with scoped_default_context_async(GraphContext(database=postgres_raw_db)):
        auth, workspace, track, unrelated = await proposal()
        await staging.revoke_token(user_id=auth.id, token=unrelated.token)
        work = await work_items.enqueue_work_item(
            kind="capability",
            origin="http",
            principal_id=auth.id,
            workspace_id=workspace.id,
            idempotency_key=f"real-broker-{unrelated.token}",
            input_payload={
                "capability_key": "integral_create_entry",
                "source": "core",
                "op_class": "propose",
                "arguments": {
                    "track_id": track.id,
                    "title": "Worker recovery",
                    "text": "Worker recovery",
                },
            },
        )
        parked = await work_worker.process_one_due_item(
            worker_id="first-worker", work_item_id=work.work_item_id, lease_seconds=60
        )
        assert parked.status == "waiting_for_human", parked.failure
        links = await WorkApproval.find({"context.work_item_id": work.work_item_id})
        assert len(links) == 1
        staged = await staging.get_token(links[0].staging_token)
        assert staged is not None and staged.state == "pending"
        assert "work_approval_id" not in staged.payload
        applied = await staging_apply.bless_and_execute(
            user_id=auth.id, token=staged.token
        )
        assert applied["consumed"] is True, applied

        worker = """
import asyncio, json, os, sys
from jvspatial.core.context import GraphContext, set_default_context
from jvspatial.db.factory import create_database
from app.agentive.services import work_items, work_worker

async def main():
    db = create_database(db_type='postgres')
    set_default_context(GraphContext(database=db))
    try:
        if sys.argv[2] == 'crash':
            work_worker.set_crash_point('after_claim')
            result = await work_worker.process_one_due_item(worker_id='crash-worker', work_item_id=sys.argv[1])
        elif sys.argv[2] == 'reclaim':
            item = await work_items.reclaim_expired_lease(sys.argv[1], worker_id='fresh-worker')
            result = await work_worker.execute_claimed_work(item, worker_id='fresh-worker')
        else:
            result = await work_worker.process_one_due_item(worker_id='fresh-worker', work_item_id=sys.argv[1])
        print('__WORK_RESULT__' + json.dumps({'pid': os.getpid(), 'status': result.status, 'failure': result.failure}))
    finally:
        await db.close()

asyncio.run(main())
"""

        async def run_child(mode):
            child = await asyncio.create_subprocess_exec(
                sys.executable,
                "-c",
                worker,
                work.work_item_id,
                mode,
                env={**os.environ, "EMBEDDING_STORE_DRIVER": "null"},
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            try:
                stdout, stderr = await asyncio.wait_for(child.communicate(), timeout=60)
            except BaseException:
                child.kill()
                await child.wait()
                raise
            return child, stdout, stderr

        if crash_after_claim:
            from datetime import datetime, timedelta, timezone

            crashed, _, stderr = await run_child("crash")
            assert crashed.returncode == 77, stderr.decode()
            stored = await postgres_raw_db.get("object", work.id)
            assert stored["context"]["status"] == "running"
            await postgres_raw_db.find_one_and_update(
                "object",
                {"id": work.id, "context.status": "running"},
                {
                    "$set": {
                        "context.lease_expires_at": (
                            datetime.now(timezone.utc) - timedelta(seconds=1)
                        ).isoformat()
                    }
                },
            )
        child, stdout, stderr = await run_child(
            "reclaim" if crash_after_claim else "normal"
        )
        assert child.returncode == 0, stderr.decode()
        lines = [
            line
            for line in stdout.decode().splitlines()
            if line.startswith("__WORK_RESULT__")
        ]
        assert len(lines) == 1
        observed = json.loads(lines[0].removeprefix("__WORK_RESULT__"))
        assert observed["pid"] != os.getpid()
        assert observed["status"] == "succeeded", observed
        entries = await Entry.find({"track_id": track.id})
        assert len(entries) == 1
        steps = await postgres_raw_db.find(
            "object", {"entity": "RunStep", "context.work_item_id": work.work_item_id}
        )
        assert len(steps) == 1


async def test_stale_work_projection_cannot_replace_reviewed_proposal(
    postgres_raw_db,
):
    """Work presentation cannot rewrite a current proposal or its intent."""
    from app.agentive import staging_store

    async with scoped_default_context_async(GraphContext(database=postgres_raw_db)):
        auth, _, _, sc = await proposal()
        before = await staging_store._find_record(sc.token)
        intent = staging_store.decision_intent_fingerprint(
            before.model_dump(mode="json")
        )
        await work_approvals._persist_staged_projection(
            {
                "token": sc.token,
                "user_id": "another-user",
                "state": "blessed",
                "payload": {"title": "Unreviewed replacement"},
                "work_approval_id": "untrusted-link",
            }
        )
        after = await staging_store._find_record(sc.token)
        assert after.user_id == auth.id
        assert after.state == "pending"
        assert after.payload == before.payload
        assert (
            staging_store.decision_intent_fingerprint(after.model_dump(mode="json"))
            == intent
        )
