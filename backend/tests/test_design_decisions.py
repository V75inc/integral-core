"""Explicit saved-design lifecycle does not depend on a legacy token."""

from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.api.errors import InsufficientPermissionsError, ResourceConflictError
from app.services import design_decisions as service


@pytest.fixture
def design(monkeypatch):
    """Verify design."""
    thread = SimpleNamespace(
        id="n.ChatThread.a",
        user_id="user-a",
        workspace_id="ws-a",
        provider_id="integral_native",
        provider_session_id="session-a",
        title="Planning",
        active_work_item_id="",
        active_harness_session_id=None,
        save=AsyncMock(),
        updated_at="old",
        last_message_at="message",
        artifacts={"app_design_blueprint": {}, "operation_bridge": {}, "notes": {}},
        prompt_queue={
            "status": "open",
            "items": [
                {"write_kind": "design_proposal", "status": "pending"},
                {"write_kind": "update_entry", "status": "pending"},
            ],
        },
        design_proposed={
            "design_id": "design-a",
            "proposed_at": "time-a",
            "proposed_at_user_turn": 1,
            "blueprint_revision": 2,
            "blueprint_digest": "digest-a",
            "blueprint": {"app": {}},
            "summary": "My design",
            "approved": True,
        },
    )
    writes = []

    class Transaction:
        async def get(self, *args: object):
            """Verify get."""
            return None

        async def find_one_and_update(self, collection, predicate, updates):
            """Verify find one and update."""
            writes.append((collection, predicate, updates))
            if collection == "node":
                return {"id": thread.id, "context": vars(thread)}
            return None

    @asynccontextmanager
    async def transaction():
        """Verify transaction."""
        yield Transaction()

    @asynccontextmanager
    async def admission(*args: object):
        """Verify admission."""
        yield

    monkeypatch.setattr(service, "postgres_graph_transaction", transaction)
    monkeypatch.setattr(service, "release_admission", admission)
    monkeypatch.setattr(service.ChatThread, "get", AsyncMock(return_value=thread))
    monkeypatch.setattr(
        service.chat_threads, "list_threads", AsyncMock(return_value=[thread])
    )
    monkeypatch.setattr(service, "_retire_design_projections", AsyncMock())
    monkeypatch.setattr(service, "_retire_authoritative_tokens", AsyncMock())
    monkeypatch.setattr(
        service, "_capture_legacy_design_tokens", AsyncMock(return_value=[])
    )
    monkeypatch.setattr(service, "_assert_no_ambiguous_draft", AsyncMock())
    monkeypatch.setattr(service, "_assert_settled_build", AsyncMock())
    return thread, writes


async def discard(thread, **kwargs):
    """Verify discard."""
    return await service.discard_design(
        user_id="user-a",
        workspace_id="ws-a",
        thread_id=thread.id,
        design_id="design-a",
        blueprint_revision=2,
        proposed_at="time-a",
        **kwargs,
    )


@pytest.mark.asyncio
async def test_native_marker_without_token_is_discoverable_and_discardable(design):
    """Verify native marker without token is discoverable and discardable."""
    thread, writes = design
    rows = await service.list_design_decisions(user_id="user-a", workspace_id="ws-a")
    assert rows[0]["thread_id"] == thread.id
    assert rows[0]["approved"] is True
    assert rows[0]["blueprint_revision"] == 2
    await discard(thread)
    update = writes[-1][2]["$set"]
    tombstone = update["context.design_proposed"]
    assert tombstone["discarded_at"]
    assert "blueprint" not in tombstone
    assert "proposed_at_user_turn" not in tombstone
    assert update["context.artifacts"] == {"notes": {}}
    assert update["context.prompt_queue"]["items"][0]["status"] == "cancelled"
    assert update["context.prompt_queue"]["items"][1]["status"] == "pending"
    assert writes[0][1]["context.updated_at"] == "old"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "field,value", [("user_id", "other"), ("workspace_id", "other")]
)
async def test_discard_requires_exact_owner_and_workspace(design, field, value):
    """Verify discard requires exact owner and workspace."""
    thread, writes = design
    setattr(thread, field, value)
    with pytest.raises(InsufficientPermissionsError):
        await discard(thread)
    assert writes == []


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "change",
    [
        {"blueprint_revision": 3},
        {"design_id": "replacement"},
        {"partial_build": {"batch_token": "partial"}},
        {"build_receipt": {"batch_token": "applied"}},
        {"discarded_at": "yesterday"},
    ],
)
async def test_stale_or_applied_design_cannot_be_discarded(design, change):
    """Verify stale or applied design cannot be discarded."""
    thread, writes = design
    thread.design_proposed.update(change)
    with pytest.raises(ResourceConflictError):
        await discard(thread)
    assert writes == []


@pytest.mark.asyncio
async def test_active_or_unknown_work_prevents_discard(design):
    """Verify active or unknown work prevents discard."""
    thread, writes = design
    thread.active_work_item_id = "running"
    with pytest.raises(ResourceConflictError):
        await discard(thread)
    assert all(
        "context.design_proposed" not in update.get("$set", {})
        for _, _, update in writes
    )


@pytest.mark.asyncio
async def test_discarded_markers_never_appear_pending(design):
    """Verify discarded markers never appear pending."""
    thread, _ = design
    thread.design_proposed = {"design_id": "design-a", "discarded_at": "now"}
    assert (
        await service.list_design_decisions(user_id="user-a", workspace_id="ws-a") == []
    )


@pytest.mark.asyncio
async def test_pending_design_does_not_block_unrelated_governed_write(monkeypatch):
    """Verify pending design does not block unrelated governed write."""
    from app.agentive.harness.broker_tools import build_brokered_tools
    from app.schemas.capability_broker import CapabilityResult
    from tests.native_harness.wp_06.test_broker_tools import _scope

    invoke = AsyncMock(return_value=CapabilityResult(ok=True, data={"staged": True}))
    monkeypatch.setattr(
        "app.agentive.services.capability_broker.invoke_declared_capability", invoke
    )
    monkeypatch.setattr(
        "app.agentive.services.capability_broker.infer_source_and_op_class",
        lambda _: ("core", "write"),
    )
    tool = build_brokered_tools(
        scope=_scope(),
        run_state={"pending_design": True},
        catalogue=[
            {
                "name": "integral_update_entry",
                "description": "Update an entry",
                "input_schema": {"type": "object", "properties": {}},
            }
        ],
    )[0]
    result = await tool.function_schema.call(
        {},
        SimpleNamespace(
            tool_call_id="edit", active_capability_ids={"integral-entries"}
        ),
    )
    assert result["staged"] is True
    invoke.assert_awaited_once()
