"""Verify atomic receipt-backed history on disposable PostgreSQL only."""

from dataclasses import replace
from unittest.mock import AsyncMock

import pytest
from jvspatial.db.factory import create_database

from app.agentive.connectors.external_chat import (
    ExternalChatAssignment,
    ExternalChatConversation,
    ExternalChatEventKey,
    ExternalChatProvision,
    ExternalChatReply,
    VerifiedExternalChatMessage,
)
from app.agentive.connectors.web_chat_turn_store import JvSpatialWebChatTurnStore
from app.agentive.services import public_web_chat
from app.agentive.services.web_chat_admission import VerifiedFullPageSession
from app.agentive.services.web_chat_history import PostgresWebChatHistory

pytestmark = [pytest.mark.contract, pytest.mark.postgres, pytest.mark.asyncio]


async def test_exact_bounded_history_recovers_completed_receipts_after_reopen(
    postgres_raw_db, monkeypatch
):
    """A separate pool sees only completed same-scope encrypted history."""
    monkeypatch.setenv("INTEGRAL_CREDENTIAL_ENC_KEY", "8a" * 32)
    provision = ExternalChatProvision("workspace", "public_web_chat", "route")
    conversation = ExternalChatConversation(provision, "conversation")
    scope = public_web_chat.PublicWebChatScope("workspace", "visitor", "public", 2, 3)
    session = VerifiedFullPageSession(conversation, scope)
    assignment = ExternalChatAssignment(provision, "public", "2", "3")
    verify = AsyncMock(return_value=session)
    monkeypatch.setattr(public_web_chat, "admit", AsyncMock(return_value=scope))
    monkeypatch.setattr(
        public_web_chat, "transport_ready", AsyncMock(return_value=True)
    )
    store = JvSpatialWebChatTurnStore(database=postgres_raw_db)

    async def submit(
        event,
        *,
        target=conversation,
        policy=assignment,
        visitor="visitor",
        complete=True,
    ):
        key = ExternalChatEventKey(target, event)
        message = VerifiedExternalChatMessage(key, visitor, f"Private request {event}.")
        reply = ExternalChatReply(key, f"Private reply {event}.")
        claim = await store.claim(message, policy)
        assert claim.fence
        if complete:
            assert await store.finish(key, policy, claim.fence, reply)
            assert not await store.finish(key, policy, claim.fence, reply)
        return message, reply

    old, old_reply = await submit("old")
    for i in range(10):
        await submit(f"recent-{i}")
    await submit("pending", complete=False)
    await submit("different-visitor", visitor="another")
    await submit(
        "different-conversation",
        target=replace(conversation, external_conversation_id="other"),
    )
    await submit(
        "different-publication", policy=replace(assignment, publication_revision="5")
    )
    await submit("different-epoch", policy=replace(assignment, route_epoch="6"))
    for other in (
        replace(provision, provision_id="other"),
        replace(provision, workspace_id="other"),
    ):
        await submit(
            "different-route",
            target=replace(conversation, provision=other),
            policy=replace(assignment, provision=other),
        )

    independent = create_database(db_type="postgres")
    try:
        reader = PostgresWebChatHistory(
            database=independent, read_verified_session=verify
        )
        recent = await reader.ports.recent(session, assignment, None)
        assert [item.event_id for item in recent.exchanges] == [
            f"recent-{i}" for i in range(2, 10)
        ]
        readback = await reader.ports.recent(session, assignment, "old")
        assert [item.event_id for item in readback.exchanges] == ["old"] + [
            f"recent-{i}" for i in range(3, 10)
        ]
        prior = await reader.ports.prior(
            session, assignment, ExternalChatEventKey(conversation, "recent-9")
        )
        assert [item.event_id for item in prior.exchanges] == [
            f"recent-{i}" for i in range(1, 9)
        ]
        replay = await JvSpatialWebChatTurnStore(database=independent).claim(
            old, assignment
        )
        assert (
            replay.state == "replayed"
            and replay.fence is None
            and replay.reply == old_reply
        )
        verify.side_effect = [session, None]
        with pytest.raises(ValueError, match="unavailable"):
            await reader.ports.recent(session, assignment, None)
        rows = await independent.find(
            "object", {"entity": "WebChatTurnReceipt"}, limit=20
        )
        assert "Private request" not in repr(rows) and "Private reply" not in repr(rows)
        assert all("recent-" not in repr(row) for row in rows)
    finally:
        await independent.close()
