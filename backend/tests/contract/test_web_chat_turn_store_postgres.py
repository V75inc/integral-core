"""Qualify receipt races against independent postgres pools, not a Python lock."""

import asyncio
import json
from dataclasses import replace

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
from app.agentive.connectors.web_chat_turn import WebChatUnavailable
from app.agentive.connectors.web_chat_turn_store import JvSpatialWebChatTurnStore

pytestmark = [pytest.mark.contract, pytest.mark.postgres, pytest.mark.asyncio]


@pytest.fixture
async def stores(postgres_raw_db, monkeypatch):
    """Use a second PostgreSQL connection pool against the disposable test schema."""
    monkeypatch.setenv("INTEGRAL_CREDENTIAL_ENC_KEY", "8a" * 32)
    independent = create_database(db_type="postgres")
    try:
        yield (
            JvSpatialWebChatTurnStore(database=postgres_raw_db),
            JvSpatialWebChatTurnStore(database=independent),
        )
    finally:
        await independent.close()


@pytest.fixture
def event():
    """Provide synthetic text and scoped identifiers only."""
    scope = ExternalChatProvision("workspace", "web-chat", "provision")
    key = ExternalChatEventKey(ExternalChatConversation(scope, "conversation"), "event")
    return (
        VerifiedExternalChatMessage(
            key, "synthetic-visitor", "Hello from the fixture."
        ),
        ExternalChatAssignment(scope, "profile", "revision", "epoch"),
        ExternalChatReply(key, "Only one terminal reply."),
    )


async def test_independent_clients_get_one_fence_and_one_terminal_receipt(
    stores, event
):
    """Independent workers may settle the one durable claim at most once."""
    message, assignment, original = event
    claims = await asyncio.gather(
        *(store.claim(message, assignment) for store in stores)
    )
    assert sorted(claim.state for claim in claims) == ["claimed", "in_progress"]
    (fence,) = [claim.fence for claim in claims if claim.fence]
    assert (await stores[1].claim(message, assignment)).state == "in_progress"
    assert not await stores[1].finish(message.key, assignment, "wrong-fence", original)
    assert not await stores[0].finish(
        message.key, replace(assignment, route_epoch="new-epoch"), fence, original
    )
    assert not await stores[0].finish(
        message.key,
        assignment,
        fence,
        replace(original, in_reply_to=replace(message.key, external_event_id="other")),
    )
    replies = [original, replace(original, text="Another concurrent reply.")]
    accepted = await asyncio.gather(
        *(
            store.finish(message.key, assignment, fence, reply)
            for store, reply in zip(stores, replies)
        )
    )
    assert sorted(accepted) == [False, True]
    for store in stores:
        result = await store.claim(message, assignment)
        assert result.state == "replayed"
        assert result.reply == replies[accepted.index(True)]
        assert result.fence is None
        assert not await store.finish(message.key, assignment, fence, original)


async def test_reuse_is_exact_and_other_scopes_get_independent_receipts(stores, event):
    """Same-key changes conflict; other scoped events never share a receipt."""
    message, assignment, reply = event
    first, second = stores
    reservation = await first.claim(message, assignment)
    assert await first.finish(message.key, assignment, reservation.fence, reply)
    for different_message, different_assignment in (
        (replace(message, verified_sender_id="other"), assignment),
        (replace(message, text=message.text + " "), assignment),
        (message, replace(assignment, profile_key="other")),
        (message, replace(assignment, publication_revision="other")),
        (message, replace(assignment, route_epoch="other")),
    ):
        assert (await second.claim(different_message, different_assignment)).state == (
            "conflict"
        )
    original = message.key.conversation
    for scope in (
        replace(original.provision, workspace_id="another-workspace"),
        replace(original.provision, provision_id="another-connector-instance"),
        replace(original.provision, connector_kind="another-channel"),
        original.provision,
    ):
        conversation = replace(
            original,
            provision=scope,
            external_conversation_id=(
                "different-visitor-conversation"
                if scope == original.provision
                else original.external_conversation_id
            ),
        )
        new_message = replace(
            message, key=replace(message.key, conversation=conversation)
        )
        scoped = replace(assignment, provision=scope)
        assert (await second.claim(new_message, scoped)).state == "claimed"
        assert not await second.finish(
            new_message.key,
            scoped,
            reservation.fence,
            replace(reply, in_reply_to=new_message.key),
        )
    assert (await second.claim(message, assignment)).reply == reply


async def test_storage_contains_no_plaintext_fence_or_reply_and_fails_closed(
    stores, postgres_raw_db, event
):
    """Check raw stored values and fail closed if receipt integrity is absent."""
    message, assignment, reply = event
    first, second = stores
    claimed = await first.claim(message, assignment)
    assert await first.finish(message.key, assignment, claimed.fence, reply)
    (document,) = await postgres_raw_db.find(
        "object", {"entity": "WebChatTurnReceipt"}, limit=2
    )
    serialized = json.dumps(document)
    assert message.text not in serialized
    assert message.verified_sender_id not in serialized
    assert reply.text not in serialized
    assert claimed.fence not in serialized
    assert document["context"]["request_ciphertext"].startswith("v1:")
    assert document["context"]["reply_ciphertext"].startswith("v1:")
    await postgres_raw_db.find_one_and_update(
        "object",
        {"id": document["id"]},
        {"$set": {"context.reply_ciphertext": "not a versioned receipt"}},
    )
    with pytest.raises(WebChatUnavailable, match="receipt is unavailable"):
        await second.claim(message, assignment)
