"""Exercise the native voice API's real authority wrapper and close lifetime."""

import asyncio
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from starlette.websockets import WebSocketState

from app.agentive.api import native_voice as endpoint
from app.services import chat_conversation_updates, chat_turn_submissions, native_voice
from tests.test_native_voice import Browser, Provider, event
from tests.test_native_voice_completion import _send_tool


class _EndpointBrowser(Browser):
    def __init__(self, *, fail_close=False):
        super().__init__()
        self.headers = {"origin": "http://127.0.0.1:9150"}
        self.client_state = self.application_state = WebSocketState.CONNECTING
        self.fail_close = fail_close

    async def accept(self):
        self.client_state = self.application_state = WebSocketState.CONNECTED

    async def receive_text(self):
        return json.dumps(
            {"ticket": "x" * 43, "workspace_id": "workspace", "thread_id": "thread"}
        )

    async def receive(self):
        frame = await super().receive()
        if frame["type"] == "websocket.disconnect" and not self.fail_close:
            self.client_state = WebSocketState.DISCONNECTED
        return frame

    async def close(self, code=1000):
        self.application_state = WebSocketState.DISCONNECTED
        if self.fail_close:
            raise OSError("Synthetic browser close transport error")


def _patch_endpoint(monkeypatch, provider):
    thread = SimpleNamespace(id="thread")
    check = AsyncMock(return_value=thread)
    credential = SimpleNamespace(model="synthetic-model", api_key="synthetic-key")
    monkeypatch.setattr(endpoint.settings, "FRONTEND_ORIGIN", "http://127.0.0.1:9150")
    monkeypatch.setattr(endpoint.settings, "INTEGRAL_NATIVE_DURABLE_CHAT_ENABLED", True)
    monkeypatch.setattr(endpoint, "authorize", check)
    monkeypatch.setattr(native_voice, "authorize", check)
    monkeypatch.setattr(endpoint, "get_user_node", AsyncMock(return_value=object()))
    monkeypatch.setattr(
        endpoint, "redeem_ws_ticket_shared", AsyncMock(return_value="caller")
    )
    monkeypatch.setattr(
        endpoint, "resolve_realtime_credential", AsyncMock(return_value=credential)
    )
    monkeypatch.setattr(
        endpoint,
        "workspace_voice_profile_identity",
        AsyncMock(return_value=SimpleNamespace(name="Assistant", persona="")),
    )
    monkeypatch.setattr(endpoint, "open_upstream", AsyncMock(return_value=provider))
    monkeypatch.setattr(
        chat_conversation_updates,
        "read_conversation_snapshot",
        AsyncMock(
            return_value=SimpleNamespace(cursor="baseline", updates=(), gap=False)
        ),
    )
    provider.close = AsyncMock()
    return thread


@pytest.mark.asyncio
async def test_endpoint_returns_authorized_thread_into_real_relay_and_reads_completion(
    monkeypatch,
):
    """Run production websocket handler and relay with synthetic provider frames."""
    browser, provider = _EndpointBrowser(), Provider()
    thread = _patch_endpoint(monkeypatch, provider)
    submit = AsyncMock(
        return_value=SimpleNamespace(work_item_id="work", message_id="user")
    )
    status = AsyncMock(return_value=("running", None))
    monkeypatch.setattr(chat_turn_submissions, "submit_chat_turn", submit)
    monkeypatch.setattr(native_voice, "_delegated_status", status)
    monkeypatch.setattr(native_voice, "RESULT_IDLE_SECONDS", 0.01)
    monkeypatch.setattr(native_voice, "RESULT_POLL_SECONDS", 0.01)
    task = asyncio.create_task(endpoint.native_voice(browser))
    try:
        assert (await event(provider.outbox))["type"] == "session.update"
        await provider.inbox.put({"type": "session.updated"})
        assert (await event(browser.outbox))["thread_id"] == thread.id
        await _send_tool(provider, "delegate", "work")
        assert "accepted" in (await event(provider.outbox))["item"]["output"]

        status.return_value = ("succeeded", "The verified delegated result is ready.")
        readout = (await event(provider.outbox))["response"]
        assert "The verified delegated result is ready." in readout["instructions"]
        assert all(args.args[1] is thread for args in status.await_args_list)
        assert endpoint.resolve_realtime_credential.await_count >= 2
        assert "caller" in endpoint._active_calls
        await provider.inbox.put(
            {
                "type": "response.created",
                "response": {"id": "done", "metadata": readout["metadata"]},
            }
        )
        await provider.inbox.put(
            {
                "type": "response.output_audio.delta",
                "response_id": "done",
                "delta": "AAE=",
            }
        )
        assert await event(browser.outbox) == b"\x00\x01"
        submit.assert_awaited_once()
    finally:
        await browser.inbox.put({"type": "websocket.disconnect"})
        await task
    provider.close.assert_awaited_once()
    assert "caller" not in endpoint._active_calls


@pytest.mark.asyncio
async def test_endpoint_releases_upstream_and_claim_even_when_browser_close_raises(
    monkeypatch,
):
    """Independent cleanup survives an unexpected browser close transport error."""
    browser, provider = _EndpointBrowser(fail_close=True), Provider()
    _patch_endpoint(monkeypatch, provider)
    task = asyncio.create_task(endpoint.native_voice(browser))
    await event(provider.outbox)
    await provider.inbox.put({"type": "session.updated"})
    await event(browser.outbox)
    assert "caller" in endpoint._active_calls
    await browser.inbox.put({"type": "websocket.disconnect"})
    with pytest.raises(OSError):
        await task
    provider.close.assert_awaited_once()
    assert "caller" not in endpoint._active_calls


@pytest.mark.asyncio
async def test_disconnect_during_upstream_dial_abandons_provider_and_releases_claim(
    monkeypatch,
):
    """A refresh before browser Ready cannot leave a provider dialing."""
    browser, provider = _EndpointBrowser(), Provider()
    _patch_endpoint(monkeypatch, provider)
    opening, abandoned = asyncio.Event(), asyncio.Event()

    async def dial(_credential):
        opening.set()
        try:
            await asyncio.Future()
        finally:
            abandoned.set()

    monkeypatch.setattr(endpoint, "open_upstream", dial)
    task = asyncio.create_task(endpoint.native_voice(browser))
    await asyncio.wait_for(opening.wait(), 2)
    assert "caller" in endpoint._active_calls
    await browser.inbox.put({"type": "websocket.disconnect"})
    await asyncio.wait_for(task, 2)
    assert abandoned.is_set()
    assert "caller" not in endpoint._active_calls
    assert provider.outbox.empty()


@pytest.mark.asyncio
async def test_disconnect_after_admission_releases_provider_without_cancelling_work(
    monkeypatch,
):
    """Endpoint teardown detaches its observer, not the admitted chat job."""
    from app.agentive.services import work_items

    browser, provider = _EndpointBrowser(), Provider()
    _patch_endpoint(monkeypatch, provider)
    submit = AsyncMock(
        return_value=SimpleNamespace(work_item_id="work", message_id="user")
    )
    status = AsyncMock(return_value=("running", None))
    cancel = AsyncMock()
    monkeypatch.setattr(chat_turn_submissions, "submit_chat_turn", submit)
    monkeypatch.setattr(native_voice, "_delegated_status", status)
    monkeypatch.setattr(work_items, "cancel_work_item", cancel)
    task = asyncio.create_task(endpoint.native_voice(browser))
    await event(provider.outbox)
    await provider.inbox.put({"type": "session.updated"})
    await event(browser.outbox)
    await _send_tool(provider, "delegate", "work")
    assert "accepted" in (await event(provider.outbox))["item"]["output"]
    await browser.inbox.put({"type": "websocket.disconnect"})
    await asyncio.wait_for(task, 2)
    submit.assert_awaited_once()
    cancel.assert_not_awaited()
    provider.close.assert_awaited_once()
    assert "caller" not in endpoint._active_calls


@pytest.mark.asyncio
@pytest.mark.parametrize("provider_closes", [False, True])
async def test_simultaneous_refresh_balances_acquired_provider_markers(
    monkeypatch, caplog, provider_closes
):
    """An acquired connection has a paired close marker even without Ready."""
    import logging

    browser, provider = _EndpointBrowser(), Provider()
    _patch_endpoint(monkeypatch, provider)
    if not provider_closes:
        provider.close.side_effect = OSError("Synthetic failed upstream close")

    async def dial(_credential):
        await browser.inbox.put({"type": "websocket.disconnect"})
        return provider

    monkeypatch.setattr(endpoint, "open_upstream", dial)
    with caplog.at_level(logging.INFO, logger=endpoint.__name__):
        await endpoint.native_voice(browser)
    assert [
        record.getMessage()
        for record in caplog.records
        if record.name == endpoint.__name__
    ] == [
        "Native voice transport opened",
        f"Native voice transport released; provider_closed={provider_closes}",
    ]
    provider.close.assert_awaited_once()
    assert provider.outbox.empty()
    assert "caller" not in endpoint._active_calls
