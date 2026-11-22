"""Browser protocol mapping, with no installed visitor transport or provider."""

from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.agentive.connectors.external_chat import (
    ExternalChatAssignment,
    ExternalChatConversation,
    ExternalChatProvision,
)
from app.agentive.connectors.web_chat_turn import WebChatClaim
from app.agentive.services import profile_management, public_web_chat
from app.agentive.services.web_chat_admission import VerifiedFullPageSession
from app.agentive.services.web_chat_browser_transport import (
    BrowserSessionAccess,
    BrowserVisitorSessions,
    BrowserWebChatController,
    BrowserWebChatHistory,
)
from app.agentive.services.web_chat_profile_inference import (
    StoredWebChatExchange,
    WebChatHistorySnapshot,
)

pytestmark = [pytest.mark.unit, pytest.mark.asyncio]


class _Store:
    def __init__(self):
        self.message = None
        self.reply = None

    async def claim(self, message, assignment):
        if self.message is None:
            self.message = message
            return WebChatClaim("claimed", "not-for-browser")
        if message != self.message:
            return WebChatClaim("conflict")
        if self.reply is None:
            return WebChatClaim("in_progress")
        return WebChatClaim("replayed", reply=self.reply)

    async def finish(self, key, assignment, fence, reply):
        assert key == self.message.key and fence == "not-for-browser"
        assert self.reply is None
        self.reply = reply
        return True


@pytest.fixture
def browser(monkeypatch):
    """Provide exact, active fake authority and isolated explicit ports."""
    provision = ExternalChatProvision(
        "work-internal", "public_web_chat", "prov-internal"
    )
    conversation = ExternalChatConversation(provision, "room-internal")
    scope = public_web_chat.PublicWebChatScope(
        provision.workspace_id, "visitor-internal", "public", 4, 22
    )
    session = VerifiedFullPageSession(conversation, scope)
    assignment = ExternalChatAssignment(provision, "public", "4", "22")
    profile = {
        "key": "public",
        "name": "Public guide",
        "version": 4,
        "audience": "public",
        "persona": "Stay concise.",
        "tools": [],
    }
    route = {
        "profile_key": "public",
        "enabled": True,
        "generation": 22,
        "provision_id": provision.provision_id,
    }
    monkeypatch.setattr(
        profile_management,
        "record",
        AsyncMock(
            return_value=SimpleNamespace(
                public_web_chat=route, profiles={"public": {"published": profile}}
            )
        ),
    )
    monkeypatch.setattr(
        public_web_chat, "transport_ready", AsyncMock(return_value=True)
    )
    snapshot = WebChatHistorySnapshot(conversation, scope.visitor_id, assignment, ())
    access = BrowserSessionAccess("current", session)
    current = AsyncMock(return_value=access)
    opened = AsyncMock(return_value=access)
    recent = AsyncMock(return_value=snapshot)
    prior = AsyncMock(return_value=snapshot)
    model = AsyncMock(return_value="Test model response only")
    budget = AsyncMock(return_value=True)
    store = _Store()
    controller = BrowserWebChatController(
        provision,
        BrowserVisitorSessions(opened, current),
        budget,
        BrowserWebChatHistory(prior, recent),
        store,
        model,
    )
    return SimpleNamespace(
        scope=scope,
        session=session,
        route=route,
        provision=provision,
        assignment=assignment,
        opened=opened,
        current=current,
        recent=recent,
        prior=prior,
        snapshot=snapshot,
        budget=budget,
        store=store,
        model=model,
        controller=controller,
    )


async def test_resume_returns_only_literal_messages_and_stable_distinct_display_ids(
    browser,
):
    """Ready has the exact component wire shape without internal identities."""
    browser.recent.return_value = replace(
        browser.snapshot,
        exchanges=(StoredWebChatExchange("opaque-event", "<b>Question</b>", "Answer"),),
    )
    first = (await browser.controller.start("resume")).to_wire()
    assert first == (await browser.controller.start("resume")).to_wire()
    assert set(first) == {"kind", "messages"}
    assert first["kind"] == "ready"
    assert [msg["role"] for msg in first["messages"]] == ["visitor", "assistant"]
    assert [msg["text"] for msg in first["messages"]] == ["<b>Question</b>", "Answer"]
    assert all(set(msg) == {"role", "text", "id"} for msg in first["messages"])
    assert len({msg["id"] for msg in first["messages"]}) == 2
    assert "internal" not in repr(first) and "opaque-event" not in repr(first)
    browser.budget.assert_awaited_with("start", browser.provision, None)
    browser.model.assert_not_awaited()


async def test_send_waits_for_matching_durable_history_and_retry_does_not_call_model_twice(
    browser,
):
    """Receipt-only progress cannot masquerade as reloadable history."""
    first = (await browser.controller.send("event", "Is this ready?")).to_wire()
    assert first == {"kind": "pending"}
    assert browser.store.reply is not None
    browser.recent.return_value = replace(
        browser.snapshot,
        exchanges=(
            StoredWebChatExchange("event", "Is this ready?", browser.store.reply.text),
        ),
    )
    result = (await browser.controller.send("event", "Is this ready?")).to_wire()
    assert result["kind"] == "ready"
    assert (
        len(result["messages"]) == 2
        and result["messages"][1]["text"] == browser.store.reply.text
    )
    browser.model.assert_awaited_once()
    assert (await browser.controller.start("resume")).to_wire() == result
    browser.budget.assert_any_await("send", browser.provision, browser.session)
    assert (await browser.controller.send("event", "Changed text")).to_wire() == {
        "kind": "unavailable"
    }
    browser.model.assert_awaited_once()


async def test_start_expiry_and_new_conversation_do_not_reuse_history(browser):
    """Explicit New must rotate its conversation and return it empty."""
    browser.opened.return_value = BrowserSessionAccess("expired")
    assert (await browser.controller.start("resume")).to_wire() == {"kind": "expired"}
    browser.current.return_value = BrowserSessionAccess("expired")
    assert (await browser.controller.send("event", "Question")).kind == "expired"
    browser.model.assert_not_awaited()
    browser.current.return_value = BrowserSessionAccess("current", browser.session)
    browser.opened.return_value = browser.current.return_value
    assert (await browser.controller.start("new")).kind == "unavailable"
    new = replace(
        browser.session,
        conversation=replace(
            browser.session.conversation, external_conversation_id="new-room"
        ),
    )

    async def open_new(mode):
        assert mode == "new"
        browser.current.return_value = BrowserSessionAccess("current", new)
        return browser.current.return_value

    browser.opened.side_effect = open_new
    browser.recent.return_value = replace(
        browser.snapshot,
        conversation=new.conversation,
        exchanges=(StoredWebChatExchange("old", "Previous", "Never show this"),),
    )
    assert (await browser.controller.start("new")).to_wire() == {"kind": "unavailable"}
    browser.current.return_value = BrowserSessionAccess("expired")
    browser.recent.return_value = replace(
        browser.snapshot, conversation=new.conversation
    )
    assert (await browser.controller.start("new")).to_wire() == {
        "kind": "ready",
        "messages": [],
    }


@pytest.mark.parametrize(
    "mismatch", ["scope", "history", "revoked", "route", "budget", "shape"]
)
async def test_denied_or_malformed_request_redacts_without_inference(browser, mismatch):
    """Trusted routing, rate and history gates all fail without disclosure."""
    if mismatch == "scope":
        different = replace(
            browser.session,
            conversation=replace(
                browser.session.conversation,
                provision=replace(browser.provision, provision_id="wrong-provision"),
            ),
        )
        browser.current.return_value = browser.opened.return_value = (
            BrowserSessionAccess("current", different)
        )
    elif mismatch == "history":
        browser.recent.return_value = replace(browser.snapshot, visitor_id="different")
        browser.prior.return_value = browser.recent.return_value
    elif mismatch == "revoked":
        browser.route["generation"] += 1
    elif mismatch == "route":
        browser.route["provision_id"] = "other"
    elif mismatch == "budget":
        browser.budget.return_value = False
    elif mismatch == "shape":
        assert (await browser.controller.send("bad id", "x")).kind == "unavailable"
        assert (
            await browser.controller.send("event", "x" * 1001)
        ).kind == "unavailable"
        assert (await browser.controller.send("event", "x\0")).kind == "unavailable"
        browser.budget.assert_not_awaited()
        browser.model.assert_not_awaited()
        return

    assert (await browser.controller.start("resume")).to_wire() == {
        "kind": "unavailable"
    }
    assert (await browser.controller.send("event", "Question")).to_wire() == {
        "kind": "unavailable"
    }
    browser.model.assert_not_awaited()


async def test_revocation_during_fresh_history_read_cannot_serve_reply(browser):
    """The history snapshot cannot outlive its route generation."""

    async def recent(_session, _assignment, _event):
        browser.route["enabled"] = False
        return browser.snapshot

    browser.recent.side_effect = recent
    assert (await browser.controller.start("resume")).to_wire() == {
        "kind": "unavailable"
    }
    browser.model.assert_not_awaited()


async def test_expiry_during_model_only_returns_expired_without_receipt(browser):
    """Recognize expired credentials without exposing a pending model reply."""

    async def complete(_input):
        browser.current.return_value = BrowserSessionAccess("expired")
        return "Uncommitted"

    browser.model.side_effect = complete
    result = await browser.controller.send("event", "Question")
    assert result.to_wire() == {"kind": "expired"}
    assert browser.store.reply is None
    browser.recent.assert_not_awaited()
