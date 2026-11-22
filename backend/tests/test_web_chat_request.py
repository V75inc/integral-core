"""Verify Web Chat request admission and durable reservation composition."""

from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.agentive.connectors.external_chat import (
    ExternalChatConversation,
    ExternalChatProvision,
    ExternalChatReply,
)
from app.agentive.connectors.web_chat_turn import WebChatClaim, WebChatUnavailable
from app.agentive.services import profile_management, public_web_chat
from app.agentive.services.web_chat_admission import VerifiedFullPageSession
from app.agentive.services.web_chat_request import FullPageWebChatRequests


@pytest.fixture
def boundary(monkeypatch):
    """Create an exact verified visitor session and receipt store."""
    provision = ExternalChatProvision("workspace", "public_web_chat", "server-route")
    session = VerifiedFullPageSession(
        ExternalChatConversation(provision, "verified-room"),
        public_web_chat.PublicWebChatScope(
            "workspace", "verified-visitor", "public", 2, 3
        ),
    )
    reader = AsyncMock(return_value=session)
    route = {
        "profile_key": "public",
        "enabled": True,
        "generation": 3,
        "provision_id": provision.provision_id,
    }
    record = SimpleNamespace(
        public_web_chat=route,
        profiles={
            "public": {
                "published": {
                    "key": "public",
                    "audience": "public",
                    "tools": [],
                    "version": 2,
                }
            }
        },
    )
    monkeypatch.setattr(profile_management, "record", AsyncMock(return_value=record))
    monkeypatch.setattr(
        public_web_chat, "transport_ready", AsyncMock(return_value=True)
    )
    store = AsyncMock()
    return SimpleNamespace(
        service=FullPageWebChatRequests(reader, store),
        store=store,
        reader=reader,
        session=session,
        route=route,
    )


@pytest.mark.asyncio
async def test_request_normalizes_scope_and_finishes_only_initial_owner(boundary):
    """Use the verified scope and allow only the initial claimant to finish."""
    boundary.store.claim.return_value = WebChatClaim("claimed", fence="server-fence")
    boundary.store.finish.return_value = True
    prepared = await boundary.service.begin("client-event", "A text question")
    assert prepared.message.key.conversation == boundary.session.conversation
    assert prepared.message.verified_sender_id == boundary.session.scope.visitor_id
    assert prepared.message.key.external_event_id == "client-event"
    await boundary.service.finish(prepared, "One plain reply")
    reply = ExternalChatReply(prepared.message.key, "One plain reply")
    boundary.store.finish.assert_awaited_once_with(
        prepared.message.key, prepared.assignment, "server-fence", reply
    )
    boundary.store.claim.return_value = WebChatClaim("replayed", reply=reply)
    replay = await boundary.service.begin("client-event", "A text question")
    assert replay.claim.reply == reply
    assert (
        boundary.store.claim.await_args_list[0]
        == boundary.store.claim.await_args_list[1]
    )
    with pytest.raises(WebChatUnavailable):
        await boundary.service.finish(replay, "Must not overwrite reply")
    assert boundary.store.finish.await_count == 1


@pytest.mark.asyncio
async def test_changed_session_cannot_claim_or_finish(boundary):
    """Deny a changed session before claim or terminal storage."""
    boundary.store.claim.return_value = WebChatClaim("claimed", fence="server-fence")
    prepared = await boundary.service.begin("client-event", "Hello")
    original = boundary.session
    other = replace(
        original,
        conversation=replace(
            original.conversation,
            external_conversation_id="other-room",
        ),
    )
    boundary.reader.side_effect = [original, other]
    with pytest.raises(WebChatUnavailable):
        await boundary.service.begin("another-event", "Hello")
    assert boundary.store.claim.await_count == 1
    boundary.reader.side_effect = [original]
    boundary.route["generation"] += 1
    with pytest.raises(WebChatUnavailable):
        await boundary.service.finish(prepared, "Must not persist for old route")
    boundary.store.finish.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "event_id,text",
    [("", "Text"), ("route:client", "Text"), ("id", " "), ("id", "x" * 1001)],
)
async def test_invalid_input_never_loads_session_or_store(boundary, event_id, text):
    """Reject invalid input before touching the session or receipt store."""
    with pytest.raises(ValueError, match="Invalid Web Chat message"):
        await boundary.service.begin(event_id, text)
    boundary.reader.assert_not_awaited()
    boundary.store.claim.assert_not_awaited()
