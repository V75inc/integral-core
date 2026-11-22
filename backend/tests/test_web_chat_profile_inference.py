"""Unregistered public text inference with synthetic authority, history and model."""

import asyncio
from dataclasses import fields, replace
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.agentive.connectors.external_chat import (
    ExternalChatAssignment,
    ExternalChatConversation,
    ExternalChatProvision,
)
from app.agentive.connectors.web_chat_turn import WebChatClaim, WebChatUnavailable
from app.agentive.services import profile_management, public_web_chat
from app.agentive.services.web_chat_admission import VerifiedFullPageSession
from app.agentive.services.web_chat_profile_inference import (
    MAX_HISTORY_EXCHANGES,
    FullPageWebChatProfileInference,
    StoredWebChatExchange,
    WebChatHistorySnapshot,
    WebChatModelInput,
    WebChatTextResult,
)

pytestmark = [pytest.mark.unit, pytest.mark.asyncio]


class _Store:
    def __init__(self):
        self.receipts = {}
        self.finishes = 0

    async def claim(self, message, assignment):
        key = message.key
        if key not in self.receipts:
            self.receipts[key] = (message, assignment, None)
            return WebChatClaim("claimed", fence="synthetic-unique")
        original, bound, reply = self.receipts[key]
        if message != original or assignment != bound:
            return WebChatClaim("conflict")
        if reply:
            return WebChatClaim("replayed", reply=reply)
        return WebChatClaim("in_progress")

    async def finish(self, key, assignment, fence, reply):
        previous, bound, saved = self.receipts[key]
        if bound != assignment or fence != "synthetic-unique" or saved:
            return False
        self.finishes += 1
        self.receipts[key] = (previous, bound, reply)
        return True


@pytest.fixture
def turn(monkeypatch):
    """Assemble one synthetic visitor and current published configuration."""
    provision = ExternalChatProvision("workspace", "public_web_chat", "provision")
    conversation = ExternalChatConversation(provision, "conversation")
    scope = public_web_chat.PublicWebChatScope("workspace", "visitor", "public", 4, 22)
    session = VerifiedFullPageSession(conversation, scope)
    assignment = ExternalChatAssignment(provision, "public", "4", "22")
    profile = {
        "key": "public",
        "name": "Public guide",
        "version": 4,
        "audience": "public",
        "persona": "Answer in a calm style.",
        "tools": [],
    }
    configuration = SimpleNamespace(
        public_web_chat={"profile_key": "public", "enabled": True, "generation": 22},
        profiles={
            "public": {
                "published": profile,
                "draft": {"persona": "Do not forward this unsaved draft."},
            }
        },
    )
    monkeypatch.setattr(
        profile_management, "record", AsyncMock(return_value=configuration)
    )
    monkeypatch.setattr(
        public_web_chat, "transport_ready", AsyncMock(return_value=True)
    )
    history = WebChatHistorySnapshot(
        conversation,
        "visitor",
        assignment,
        (StoredWebChatExchange("earlier", "First question\r\nThanks", "First answer"),),
    )
    read_session = AsyncMock(return_value=session)
    read_history = AsyncMock(return_value=history)
    model = AsyncMock(return_value="Plain text response.")
    store = _Store()
    return SimpleNamespace(
        session=session,
        assignment=assignment,
        profile=profile,
        configuration=configuration,
        history=history,
        read_session=read_session,
        read_history=read_history,
        model=model,
        store=store,
        runner=FullPageWebChatProfileInference(
            read_session, store, read_history, model
        ),
    )


async def test_one_dispatched_turn_replays_exact_receipt_and_never_passes_scope(turn):
    """Only the durable owner uses the bounded, text-only model port."""
    started, release = asyncio.Event(), asyncio.Event()

    async def complete(payload):
        started.set()
        await release.wait()
        return "Saved answer."

    turn.model.side_effect = complete
    original = asyncio.create_task(turn.runner.submit("event", "<plain>\r\nquestion"))
    await asyncio.wait_for(started.wait(), timeout=2)
    assert await turn.runner.submit(
        "event", "<plain>\r\nquestion"
    ) == WebChatTextResult("in_progress")
    assert await turn.runner.submit("event", "Changed") == WebChatTextResult("conflict")
    release.set()
    original_result = await original
    assert original_result == WebChatTextResult("ready", "Saved answer.")
    assert await turn.runner.submit("event", "<plain>\r\nquestion") == original_result
    assert turn.store.finishes == 1
    turn.model.assert_awaited_once()
    payload = turn.model.await_args.args[0]
    assert isinstance(payload, WebChatModelInput)
    assert {field.name for field in fields(payload)} == {
        "instructions",
        "persona",
        "history",
        "visitor_text",
    }
    assert "no tools" in payload.instructions
    assert payload.persona == "Answer in a calm style."
    assert payload.history == (("First question\nThanks", "First answer"),)
    assert payload.visitor_text == "<plain>\nquestion"


@pytest.mark.parametrize(
    "kind",
    [
        "visitor",
        "conversation",
        "assignment",
        "too_many",
        "too_long",
        "duplicate",
        "current",
        "control",
    ],
)
async def test_mismatched_or_unbounded_history_prevents_inference(turn, kind):
    """Never forward cross-scope, invalid, duplicated or oversized history."""
    history = turn.history
    if kind == "visitor":
        history = replace(history, visitor_id="other")
    elif kind == "conversation":
        history = replace(
            history,
            conversation=replace(
                history.conversation, external_conversation_id="other"
            ),
        )
    elif kind == "assignment":
        history = replace(
            history, assignment=replace(history.assignment, route_epoch="other")
        )
    elif kind == "too_many":
        history = replace(
            history,
            exchanges=tuple(
                StoredWebChatExchange(str(i), "x", "y")
                for i in range(MAX_HISTORY_EXCHANGES + 1)
            ),
        )
    elif kind == "too_long":
        history = replace(
            history,
            exchanges=(
                StoredWebChatExchange("a", "x" * 1000, "y" * 4000),
                StoredWebChatExchange("b", "x" * 1000, "y" * 4000),
            ),
        )
    elif kind == "duplicate":
        history = replace(history, exchanges=history.exchanges * 2)
    elif kind == "current":
        history = replace(
            history, exchanges=(replace(history.exchanges[0], event_id="event"),)
        )
    else:
        history = replace(
            history, exchanges=(replace(history.exchanges[0], assistant_text="x\0"),)
        )
    turn.read_history.return_value = history
    with pytest.raises(WebChatUnavailable):
        await turn.runner.submit("event", "Question")
    turn.model.assert_not_awaited()


@pytest.mark.parametrize("change", ["tools", "new_revision", "readiness", "private"])
async def test_ineligible_or_revoked_profile_never_reaches_model(turn, change):
    """Independently check the current public profile and venue before execution."""
    if change == "tools":
        turn.profile["tools"] = ["synthetic"]
    elif change == "new_revision":
        turn.profile["version"] += 1
    elif change == "readiness":
        public_web_chat.transport_ready.return_value = False
    else:
        turn.profile["audience"] = "staff"
    with pytest.raises(WebChatUnavailable):
        await turn.runner.submit("event", "Question")
    turn.read_history.assert_not_awaited()
    turn.model.assert_not_awaited()


async def test_disable_or_expired_session_during_model_does_not_finish(turn):
    """Never return or persist the reply if live authorization is revoked."""

    async def complete(_):
        turn.configuration.public_web_chat["enabled"] = False
        return "Must not escape."

    turn.model.side_effect = complete
    with pytest.raises(WebChatUnavailable):
        await turn.runner.submit("event", "Question")
    assert turn.store.finishes == 0


async def test_model_error_leaves_reservation_pending_without_retry(turn):
    """An uncertain dispatch may not be replayed as a second model call."""
    turn.model.side_effect = RuntimeError("Private provider details")
    with pytest.raises(WebChatUnavailable, match="Web Chat text turn is unavailable"):
        await turn.runner.submit("event", "Question")
    assert await turn.runner.submit("event", "Question") == WebChatTextResult(
        "in_progress"
    )
    turn.model.assert_awaited_once()
    assert turn.store.finishes == 0


async def test_durable_readback_requires_original_verified_session(turn):
    """A session replacement after storage cannot obtain the saved reply."""
    original_finish = turn.store.finish

    async def finish(key, assignment, fence, reply):
        result = await original_finish(key, assignment, fence, reply)
        scope = replace(turn.session.scope, visitor_id="new-visitor")
        turn.read_session.return_value = replace(turn.session, scope=scope)
        return result

    turn.store.finish = finish
    with pytest.raises(WebChatUnavailable):
        await turn.runner.submit("event", "Question")
    assert turn.store.finishes == 1
    assert len(turn.store.receipts) == 1
