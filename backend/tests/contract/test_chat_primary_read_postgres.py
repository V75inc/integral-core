"""Separate serving contexts must admit native input from current primary rows."""

import base64
import os

import pytest
from jvspatial.cache.memory import MemoryCache
from jvspatial.core.context import GraphContext, scoped_default_context_async
from jvspatial.db.factory import create_database

from app.agentive.services import work_items
from app.models.nodes import ChatMessage, ChatThread
from app.schemas.agentive.work import WorkError
from app.services.app_operations.transaction_scope import postgres_graph_transaction
from app.services.chat_threads import create_thread
from app.services.chat_turn_submissions import submit_chat_turn
from app.services.chat_turn_worker_input import load_claimed_chat_turn_input
from tests.contract.test_chat_turn_submission_postgres import _request
from tests.contract.test_staging_command_apply_postgres import proposal


async def _submission_context():
    # Use actual AuthUser sign-in IDs linked to their canonical graph profile.
    auth, workspace, _, _ = await proposal()
    thread = await create_thread(
        user_id=auth.id, workspace_id=workspace.id, provider_id="integral_native"
    )
    return thread, auth.id, workspace.id


pytestmark = [pytest.mark.contract, pytest.mark.postgres, pytest.mark.asyncio]


@pytest.fixture(autouse=True)
def encryption(monkeypatch):
    monkeypatch.setenv(
        "INTEGRAL_CREDENTIAL_ENC_KEY", base64.b64encode(b"p" * 32).decode()
    )


async def test_secondary_prewarm_cannot_falsely_refuse_committed_native_turn(
    postgres_raw_db,
):
    writer = GraphContext(database=postgres_raw_db)
    secondary_db = create_database(
        db_type="postgres", dsn=os.environ["JVSPATIAL_POSTGRES_DSN"]
    )
    secondary = GraphContext(database=secondary_db, cache_backend=MemoryCache())
    try:
        async with scoped_default_context_async(writer):
            thread, principal, workspace = await _submission_context()
            async with scoped_default_context_async(secondary):
                warm = await ChatThread.get(thread.id)
                assert warm.active_work_item_id == ""
            receipt = await submit_chat_turn(_request(thread, principal, workspace))
            item = await work_items.claim_due_candidate(
                work_item_id=receipt.work_item_id, worker_id="primary-read-fixture"
            )
            assert item is not None
            async with scoped_default_context_async(secondary):
                assert (await ChatThread.get(thread.id)).active_work_item_id == ""
                fresh = await secondary_db.find_one("node", {"id": thread.id})
                assert fresh["context"]["active_work_item_id"] == receipt.work_item_id
                restored = await load_claimed_chat_turn_input(item)
                assert restored.thread.active_work_item_id == receipt.work_item_id
                assert restored.message.id == receipt.message_id
    finally:
        await secondary_db.close()


@pytest.mark.parametrize(
    "field,value,kind",
    [
        ("active_work_item_id", "another-work", "thread"),
        ("workspace_id", "another-workspace", "thread"),
        ("user_id", "another-principal", "thread"),
        ("provider_id", "another-provider", "thread"),
        ("parts", [{"type": "text", "text": "Changed after acceptance"}], "message"),
        ("role", "system", "message"),
        ("provider_metadata", {"untrusted": True}, "message"),
    ],
)
async def test_cached_acceptance_cannot_hide_primary_scope_or_message_change(
    postgres_raw_db, field, value, kind
):
    writer = GraphContext(database=postgres_raw_db)
    secondary_db = create_database(
        db_type="postgres", dsn=os.environ["JVSPATIAL_POSTGRES_DSN"]
    )
    secondary = GraphContext(database=secondary_db, cache_backend=MemoryCache())
    try:
        async with scoped_default_context_async(writer):
            thread, principal, workspace = await _submission_context()
            receipt = await submit_chat_turn(_request(thread, principal, workspace))
            item = await work_items.claim_due_candidate(
                work_item_id=receipt.work_item_id, worker_id="primary-change-fixture"
            )
            assert item is not None
            async with scoped_default_context_async(secondary):
                warm = await ChatThread.get(thread.id)
                assert warm.active_work_item_id == item.work_item_id
                assert await ChatMessage.get(receipt.message_id) is not None
            async with postgres_graph_transaction() as transaction:
                row = await transaction.find_one_and_update(
                    "node",
                    {"id": thread.id if kind == "thread" else receipt.message_id},
                    {"$set": {f"context.{field}": value}},
                )
                assert row is not None
            async with scoped_default_context_async(secondary):
                assert (
                    await ChatThread.get(thread.id)
                ).active_work_item_id == item.work_item_id
                with pytest.raises(WorkError, match="work.policy_denied"):
                    await load_claimed_chat_turn_input(item)
    finally:
        await secondary_db.close()
