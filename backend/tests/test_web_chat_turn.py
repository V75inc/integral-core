from unittest.mock import AsyncMock

import pytest

from app.agentive.connectors.external_chat import (
    ExternalChatAssignment,
    ExternalChatConversation,
    ExternalChatEventKey,
    ExternalChatProvision,
    ExternalChatReply,
    VerifiedExternalChatMessage,
)
from app.agentive.connectors.web_chat_turn import (
    WebChatClaim,
    WebChatTurnCoordinator,
    WebChatUnavailable,
)


@pytest.fixture
def chat():
    provision = ExternalChatProvision("workspace", "web-chat", "provision")
    key = ExternalChatEventKey(ExternalChatConversation(provision, "room"), "event")
    return (
        VerifiedExternalChatMessage(key, "visitor", "Hello"),
        ExternalChatAssignment(provision, "public", "r1", "epoch1"),
        ExternalChatReply(key, "Welcome"),
    )


@pytest.mark.asyncio
async def test_begin_only_one_ownership_fence_and_returns_store_outcomes(chat):
    message, assignment, reply = chat
    admission = AsyncMock()
    admission.text_only_assignment.return_value = assignment
    store = AsyncMock()
    store.claim.side_effect = [
        WebChatClaim("claimed", fence="fence"),
        WebChatClaim("in_progress"),
        WebChatClaim("replayed", reply=reply),
        WebChatClaim("conflict"),
    ]
    coordinator = WebChatTurnCoordinator(admission, store)
    assert (await coordinator.begin(message))[1].fence == "fence"
    assert (await coordinator.begin(message))[1].state == "in_progress"
    assert (await coordinator.begin(message))[1].reply == reply
    changed = VerifiedExternalChatMessage(message.key, "visitor", "Different")
    assert (await coordinator.begin(changed))[1].state == "conflict"
    assert admission.text_only_assignment.await_count == 8


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "disposition", ["denied", "changed", "same-publication-new-route", "other-reply"]
)
async def test_begin_does_not_expose_replay_without_current_exact_scope(
    chat, disposition
):
    message, assignment, reply = chat
    admission = AsyncMock()
    store = AsyncMock()
    admission.text_only_assignment.side_effect = [assignment, assignment]
    if disposition == "denied":
        admission.text_only_assignment.side_effect = [None]
    if disposition == "changed":
        admission.text_only_assignment.side_effect = [
            assignment,
            ExternalChatAssignment(assignment.provision, "public", "r2", "epoch1"),
        ]
    if disposition == "same-publication-new-route":
        admission.text_only_assignment.side_effect = [
            assignment,
            ExternalChatAssignment(assignment.provision, "public", "r1", "epoch2"),
        ]
    if disposition == "other-reply":
        other = ExternalChatEventKey(message.key.conversation, "another")
        reply = ExternalChatReply(other, "Wrong receipt")
    store.claim.return_value = WebChatClaim("replayed", reply=reply)
    with pytest.raises(WebChatUnavailable):
        await WebChatTurnCoordinator(admission, store).begin(message)
    assert store.claim.await_count == (0 if disposition == "denied" else 1)


@pytest.mark.asyncio
async def test_finish_requires_current_authority_and_current_fence(chat):
    message, assignment, reply = chat
    admission = AsyncMock()
    admission.text_only_assignment.side_effect = [assignment, assignment, None]
    store = AsyncMock()
    store.finish.side_effect = [True, False]
    coordinator = WebChatTurnCoordinator(admission, store)
    await coordinator.finish(message, assignment, "fence", reply)
    with pytest.raises(WebChatUnavailable):
        await coordinator.finish(message, assignment, "fence", reply)
    with pytest.raises(WebChatUnavailable):
        await coordinator.finish(message, assignment, "fence", reply)
    assert store.finish.await_count == 2
