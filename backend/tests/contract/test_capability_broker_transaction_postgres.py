"""Actual-store capability broker reads within worker transactions."""

from __future__ import annotations

import uuid

import pytest
from jvspatial.core.context import GraphContext, set_default_context

from app.agentive.services.capability_broker import invoke
from app.schemas.capability_broker import ERR_RUN_NOT_FOUND, CapabilityInvocation
from app.services.app_operations.transaction_scope import postgres_graph_transaction


@pytest.mark.contract
@pytest.mark.postgres
@pytest.mark.asyncio
async def test_transactional_broker_missing_run_returns_structured_denial(
    postgres_raw_db,
):
    """The worker's transaction adapter must support the broker lookup path."""
    from jvspatial.core.context import _default_context_var

    token = set_default_context(GraphContext(database=postgres_raw_db))
    try:
        async with postgres_graph_transaction(postgres_raw_db):
            result = await invoke(
                CapabilityInvocation(
                    run_id=f"missing-{uuid.uuid4().hex}",
                    principal_id="synthetic-principal",
                    workspace_id="synthetic-workspace",
                    origin="chat",
                    capability_key="integral_get_attachment_text",
                )
            )
        assert result.ok is False
        assert result.error_code == ERR_RUN_NOT_FOUND
    finally:
        _default_context_var.reset(token)


@pytest.mark.contract
@pytest.mark.postgres
@pytest.mark.asyncio
async def test_transactional_run_and_receipt_queries_preserve_scope(postgres_raw_db):
    from jvspatial.core.context import _default_context_var

    from app.agentive.services.execution_runs import AgentRun, RunStep

    run_id = f"transactional-{uuid.uuid4().hex}"
    token = set_default_context(GraphContext(database=postgres_raw_db))
    try:
        run = await AgentRun.create(
            run_id=run_id, user_id="owner", workspace_id="scope"
        )
        step = await RunStep.create(
            run_id=run_id, step_key="read:1", idempotency_key="same"
        )
        async with postgres_graph_transaction(postgres_raw_db):
            loaded = await AgentRun.find_one({"run_id": run_id})
            receipt = await RunStep.find_one(
                {"run_id": run_id, "idempotency_key": "same"}
            )
            assert loaded is not None and loaded.id == run.id
            assert loaded.user_id == "owner" and loaded.workspace_id == "scope"
            assert receipt is not None and receipt.id == step.id
            assert await AgentRun.find_one({"run_id": "other-run"}) is None
            assert (
                await RunStep.find_one(
                    {"run_id": "other-run", "idempotency_key": "same"}
                )
                is None
            )
    finally:
        _default_context_var.reset(token)


@pytest.mark.contract
@pytest.mark.postgres
@pytest.mark.asyncio
async def test_transactional_permission_fanout_preserves_entries_and_denials(
    postgres_raw_db,
):
    from jvspatial.core.context import _default_context_var

    from app.models.edges import CONTAINS, EXCLUDED_FROM, IS_MEMBER_OF, OWNS
    from app.models.nodes import App, Entry, Track, User, Workspace
    from app.services.app_graph import (
        catalog_app,
        catalog_track,
        catalog_user,
        catalog_workspace,
    )
    from app.services.permissions import get_user_accessible_entries
    from app.utils.time import utc_now_iso

    token = set_default_context(GraphContext(database=postgres_raw_db))
    try:
        suffix = uuid.uuid4().hex
        now = utc_now_iso()
        user = await User.create(user_id=f"fanout-{suffix}")
        workspace = await Workspace.create(kind="personal", name=f"Fanout {suffix}")
        await user.connect(workspace, edge=IS_MEMBER_OF, role="owner", joined_at=now)
        await catalog_user(user)
        await catalog_workspace(workspace)
        app = await App.create(
            name=f"Fanout {suffix}",
            workspace_id=workspace.id,
            owner_user_id=user.id,
            visibility="private",
        )
        await user.connect(app, edge=OWNS, role="owner", granted_at=now)
        await catalog_app(app)
        entries = []
        for index in range(5):
            track = await Track.create(
                title=f"Track {index}", workspace_id=workspace.id, visibility="private"
            )
            await app.connect(track, edge=CONTAINS, added_at=now)
            await catalog_track(track)
            entry = await Entry.create(
                title=f"Record {index}", track_id=track.id, author_id=user.id
            )
            await track.connect(entry, edge=CONTAINS, added_at=now)
            entries.append(entry)
        await user.connect(entries[-1], edge=EXCLUDED_FROM)

        kwargs = {"app_id": app.id, "workspace_id": workspace.id, "strict": True}
        expected = {entry.id for entry in entries[:-1]}
        outside = await get_user_accessible_entries(user.id, **kwargs)
        assert {entry.id for entry in outside} == expected
        async with postgres_graph_transaction(postgres_raw_db):
            inside = await get_user_accessible_entries(user.id, **kwargs)
            assert {entry.id for entry in inside} == expected
    finally:
        _default_context_var.reset(token)


@pytest.mark.contract
@pytest.mark.postgres
@pytest.mark.asyncio
async def test_native_tool_calls_link_distinct_receipts_to_durable_work(
    postgres_raw_db, monkeypatch
):
    """Exercise native wrapper, lease fence, broker metadata and actual receipts."""
    from types import SimpleNamespace

    from jvspatial.core.context import _default_context_var

    from app.agentive.harness.broker_tools import build_brokered_tools
    from app.agentive.harness.contracts import HarnessExecutionScope
    from app.agentive.services import capability_broker, work_items
    from app.agentive.services.execution_runs import AgentRun, RunStep
    from app.agentive.services.work_execution import build_work_execution_context
    from tests.contract.test_chat_turn_submission_postgres import _submission_context

    token = set_default_context(GraphContext(database=postgres_raw_db))
    try:
        thread, principal, workspace = await _submission_context()
        queued = await work_items.enqueue_work_item(
            kind="chat_turn",
            origin="interactive_chat",
            principal_id=principal,
            workspace_id=workspace,
            thread_id=thread.id,
            idempotency_key=uuid.uuid4().hex,
            input_payload={},
        )
        item = await work_items.claim_due_candidate(
            worker_id="native-tool-test",
            lease_seconds=60,
            work_item_id=queued.work_item_id,
        )
        parent = build_work_execution_context(
            work_item=item, logical_step_key="provider:0"
        )
        snapshot = {
            "manifest_version": "1.0.0",
            "fingerprint": "synthetic",
            "capabilities": [{"name": "integral_list_tracks", "op_class": "read"}],
        }
        run = await AgentRun.create(
            run_id=parent.run_id,
            user_id=principal,
            workspace_id=workspace,
            thread_id=thread.id,
            work_item_id=item.work_item_id,
            status="running",
            capability_snapshot=snapshot,
        )
        calls = []

        async def current_snapshot(workspace_id):
            assert workspace_id == workspace
            return snapshot

        async def adapter(invocation, declaration):
            calls.append(invocation.work_execution_context)
            return {"tracks": []}

        monkeypatch.setattr(
            capability_broker, "build_capability_snapshot", current_snapshot
        )
        monkeypatch.setattr(capability_broker, "_call_adapter", adapter)
        scope = HarnessExecutionScope(
            tenant_id=workspace,
            principal_id=principal,
            workspace_id=workspace,
            thread_id=thread.id,
            session_id=thread.id,
            run_id=parent.run_id,
            permission_revision="synthetic",
            capability_version="1.0.0",
        )
        tools = build_brokered_tools(
            scope=scope,
            work_execution_context=parent,
            catalogue=[
                {
                    "name": "integral_list_tracks",
                    "description": "List tracks.",
                    "input_schema": {"type": "object", "properties": {}},
                }
            ],
        )
        tool = next(t for t in tools if t.name == "integral_list_tracks")
        for identity in ("call-a", "call-b", "call-a"):
            result = await tool.function_schema.call(
                {}, SimpleNamespace(tool_call_id=identity, active_capability_ids=set())
            )
            assert not result.get("error"), result
        assert len(calls) == 2
        assert calls[0].effect_key != calls[1].effect_key
        steps = await RunStep.find({"run_id": parent.run_id})
        assert len(steps) == 2
        assert all(
            s.work_item_id == item.work_item_id and s.status == "succeeded"
            for s in steps
        )
        persisted = await AgentRun.get(run.id)
        assert set(persisted.metadata["logical_steps"]) == {
            c.logical_step_key for c in calls
        }
    finally:
        _default_context_var.reset(token)
