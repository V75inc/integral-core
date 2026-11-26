"""Unit tests for a fake server audio peer. No LiveKit, Meta, or database."""

from __future__ import annotations

import asyncio
import base64
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from app.services import native_voice
from app.services.native_voice import (
    MAX_AUDIO_CHUNK,
    PCM_SAMPLE_RATE,
    ServerAudioPeer,
    VoiceCallActions,
    VoiceRelay,
    VoiceScope,
    session_update,
)


class _FakeUpstream:
    """Records JSON events and yields scripted provider messages."""

    def __init__(self) -> None:
        self.sent: list[dict] = []
        self._events: asyncio.Queue = asyncio.Queue()

    async def send(self, event: str) -> None:
        self.sent.append(json.loads(event))

    def __aiter__(self):
        return self

    async def __anext__(self):
        item = await self._events.get()
        if item is None:
            raise StopAsyncIteration
        return json.dumps(item)

    async def push(self, event: dict | None) -> None:
        await self._events.put(event)


def _scope() -> VoiceScope:
    return VoiceScope("user-1", "ws-1", "", "")


def _relay(peer: ServerAudioPeer, upstream: _FakeUpstream) -> VoiceRelay:
    async def allow(_scope):
        return None

    relay = VoiceRelay(peer, upstream, _scope(), check=allow)
    relay.ready = True
    return relay


@pytest.mark.unit
def test_session_audio_stays_pcm_at_24000():
    """The existing session format is audio/pcm at PCM_SAMPLE_RATE."""
    update = session_update("")
    assert PCM_SAMPLE_RATE == 24000
    assert update["session"]["audio"]["input"]["format"] == {
        "type": "audio/pcm",
        "rate": 24000,
    }
    assert update["session"]["audio"]["output"]["format"] == {
        "type": "audio/pcm",
        "rate": 24000,
    }


@pytest.mark.unit
@pytest.mark.asyncio
async def test_even_pcm_chunk_appends_without_page_context():
    """One even PCM chunk under 4800 bytes is appended and does not set a page."""
    peer = ServerAudioPeer()
    upstream = _FakeUpstream()
    relay = _relay(peer, upstream)
    chunk = b"\x00\x01" * 8
    assert len(chunk) < MAX_AUDIO_CHUNK
    assert len(chunk) % 2 == 0

    task = asyncio.create_task(relay._read_browser())
    await peer.push_audio(chunk)
    await peer.close()
    await asyncio.wait_for(task, 2)

    assert upstream.sent == [
        {
            "type": "input_audio_buffer.append",
            "audio": base64.b64encode(chunk).decode("ascii"),
        }
    ]
    assert relay.scope.page_context is None


@pytest.mark.unit
@pytest.mark.asyncio
@pytest.mark.parametrize(
    "chunk",
    [b"\x00", b"\x00\x01" * ((MAX_AUDIO_CHUNK // 2) + 1)],
)
async def test_odd_or_oversized_pcm_is_rejected(chunk: bytes):
    """Odd-length and oversized chunks match _read_browser and are not forwarded."""
    peer = ServerAudioPeer()
    upstream = _FakeUpstream()
    relay = _relay(peer, upstream)
    task = asyncio.create_task(relay._read_browser())
    await peer.push_audio(chunk)

    with pytest.raises(ValueError, match="Invalid voice frame"):
        await asyncio.wait_for(task, 2)

    assert upstream.sent == []
    assert relay.scope.page_context is None


@pytest.mark.unit
@pytest.mark.asyncio
async def test_output_audio_delta_becomes_peer_pcm():
    """A fake response.output_audio.delta is playback bytes on the peer."""
    peer = ServerAudioPeer()
    upstream = _FakeUpstream()
    relay = _relay(peer, upstream)
    pcm = b"\x01\x00\x02\x00"
    task = asyncio.create_task(relay._read_provider())
    await upstream.push({"type": "response.created", "response": {"id": "resp-1"}})
    await upstream.push(
        {
            "type": "response.output_audio.delta",
            "response_id": "resp-1",
            "delta": base64.b64encode(pcm).decode("ascii"),
        }
    )

    async def _played() -> bytes:
        while not peer.playback:
            await asyncio.sleep(0)
        return peer.playback[0]

    assert await asyncio.wait_for(_played(), 2) == pcm
    await upstream.push(None)
    await asyncio.wait_for(task, 2)
    assert relay.scope.page_context is None


_PHONE_OMITTED = {
    "open_integral_page",
    "request_integral_voice_review_decision",
    "revise_integral_voice_review",
}


def _tool_names(update: dict) -> set[str]:
    return {tool["name"] for tool in update["session"]["tools"]}


@pytest.mark.unit
def test_phone_session_omits_navigation_and_review_tools():
    """A phone session withholds navigation and spoken review tools."""
    update = session_update(
        "",
        review_confirmation=False,
        actions=VoiceCallActions(),
    )
    assert _PHONE_OMITTED.isdisjoint(_tool_names(update))
    assert "This call cannot open a screen." in update["session"]["instructions"]
    relay = native_voice.open_phone_relay(ServerAudioPeer(), _FakeUpstream(), _scope())
    assert relay.review_confirmation_supported is False
    assert relay.scope.actions.open_page is False
    phone = session_update(
        "",
        review_confirmation=relay.review_confirmation_supported,
        actions=relay.scope.actions,
    )
    assert _PHONE_OMITTED.isdisjoint(_tool_names(phone))
    assert (
        "telephone admission" in (native_voice.open_phone_relay.__doc__ or "").lower()
    )
    assert "ADR 019" in (native_voice.open_phone_relay.__doc__ or "")


@pytest.mark.unit
def test_browser_session_keeps_navigation_and_review_tools():
    """Default browser sessions still pass review confirmation and navigation."""
    names = _tool_names(session_update(""))
    assert _PHONE_OMITTED <= names
    relay = VoiceRelay(ServerAudioPeer(), _FakeUpstream(), _scope())
    assert relay.review_confirmation_supported is True
    assert relay.scope.actions.open_page is True


@pytest.mark.unit
@pytest.mark.asyncio
async def test_phone_navigation_event_does_not_move_a_page(monkeypatch):
    """A navigation event leaves page context empty and does not open a view."""
    opened = AsyncMock()
    monkeypatch.setattr("app.services.agent_navigation.open_view_for_dispatch", opened)
    peer = ServerAudioPeer()
    relay = native_voice.open_phone_relay(
        peer, _FakeUpstream(), _scope(), check=AsyncMock(return_value=None)
    )
    relay.ready = True
    page = {
        "type": "page_context",
        "page": {"url": "/apps/one", "route_path": "/apps/one"},
    }
    task = asyncio.create_task(relay._read_browser())
    await peer._inbound.put(
        {
            "type": "websocket.receive",
            "text": json.dumps(page, separators=(",", ":")),
        }
    )
    await peer.send_json(
        {"type": "navigation", "id": "nav-1", "navigation": {"path": "/apps/one"}}
    )
    await peer.close()
    await asyncio.wait_for(task, 2)

    assert relay.scope.page_context is None
    assert all(message.get("type") != "navigation" for message in peer.messages)
    opened.assert_not_called()


@pytest.mark.unit
@pytest.mark.asyncio
async def test_phone_waiting_for_human_directs_caller_to_whatsapp_chat(monkeypatch):
    """A waiting result is not applied and points at the existing WhatsApp chat."""

    async def waiting(_scope, _thread, _receipt):
        return "waiting_for_human", "yes"

    monkeypatch.setattr(native_voice, "_delegated_status", waiting)
    relay = native_voice.open_phone_relay(
        ServerAudioPeer(),
        _FakeUpstream(),
        _scope(),
        check=AsyncMock(return_value=SimpleNamespace(id="thread")),
    )
    relay.accepted = SimpleNamespace(
        text="Add a note", work_item_id="work-1", message_id="msg-1"
    )
    spoken, _key = await relay._check_chat(describe_active=True)
    assert "waiting for your review" in spoken
    assert "has been applied" in spoken
    assert "was applied" not in spoken.casefold()


@pytest.mark.unit
@pytest.mark.asyncio
async def test_spoken_yes_does_not_decide_or_match_confirmation(monkeypatch):
    """A transcript or tool argument of yes is not a staged decision."""
    decide = AsyncMock()
    parse = Mock(return_value="approve")
    monkeypatch.setattr(
        "app.agentive.services.approval_decisions.decide_staged_write", decide
    )
    monkeypatch.setattr(
        "app.agentive.services.approval_intent.parse_approval_intent", parse
    )
    submitted: list[str] = []

    async def work(_scope, _call_id, request):
        submitted.append(request)
        return "submitted"

    peer = ServerAudioPeer()
    upstream = _FakeUpstream()
    relay = native_voice.open_phone_relay(
        peer,
        upstream,
        _scope(),
        work=work,
        check=AsyncMock(return_value=None),
    )
    relay.ready = True
    relay.response_generation = "response"
    await relay._completed_response(
        {
            "response": {
                "id": "response",
                "status": "completed",
                "output": [
                    {
                        "type": "function_call",
                        "name": native_voice.DELEGATE,
                        "call_id": "yes-call",
                        "arguments": json.dumps({"request": "yes"}),
                    }
                ],
            }
        }
    )
    if relay.pending is not None:
        await relay.pending
    task = asyncio.create_task(relay._read_provider())
    await upstream.push(
        {
            "type": "conversation.item.input_audio_transcription.completed",
            "transcript": "yes",
        }
    )
    await upstream.push(None)
    await asyncio.wait_for(task, 2)

    decide.assert_not_called()
    parse.assert_not_called()
    assert relay.review_confirmation is None
    assert submitted == ["yes"]


@pytest.mark.unit
@pytest.mark.asyncio
async def test_phone_scope_uses_existing_whatsapp_thread_once(monkeypatch):
    """One delegate uses the wa: thread and a repeated call id does not submit again."""
    from app.agentive.services.whatsapp_call_admission import CallerAdmission
    from app.agentive.services.whatsapp_chat_bridge import _session_id

    workspace_id = "ws-1"
    e164 = "+15551212000"
    session_key = _session_id(workspace_id, e164)
    thread = SimpleNamespace(
        id="n.ChatThread.existing",
        provider_session_id=session_key,
        user_id="user-1",
        workspace_id=workspace_id,
    )
    created = Mock(side_effect=AssertionError("parallel phone thread"))
    dedicated = AsyncMock(return_value=thread)
    monkeypatch.setattr(
        "app.agentive.services.whatsapp_chat_bridge._dedicated_thread", dedicated
    )
    monkeypatch.setattr(
        "app.services.chat_threads.create_thread",
        created,
    )
    submitted: list[str] = []

    async def fake_delegate(scope, call_id, text, **_kwargs):
        submitted.append(scope.thread)
        assert text == "list open invoices"
        assert call_id == "call-1"
        return "submitted"

    monkeypatch.setattr(native_voice, "delegate", fake_delegate)
    from app.agentive.services.whatsapp_voice_bridge import phone_voice_scope

    admission = CallerAdmission(
        decision="answer",
        reason="answer",
        verified_e164=e164,
        workspace_id=workspace_id,
        principal_id="user-1",
    )
    scope = await phone_voice_scope(admission)
    assert scope.principal == "user-1"
    assert scope.workspace == workspace_id
    assert scope.thread == thread.id
    assert scope.session != session_key
    assert not scope.session.startswith("wa:")
    assert scope.channel_session == session_key
    assert scope.channel_session.startswith("wa:")
    dedicated.assert_awaited_once_with("user-1", workspace_id, e164)
    created.assert_not_called()

    peer = ServerAudioPeer()
    upstream = _FakeUpstream()
    relay = native_voice.open_phone_relay(
        peer,
        upstream,
        scope,
        work=native_voice.delegate,
        check=AsyncMock(return_value=thread),
    )
    relay.ready = True
    relay.response_generation = "response"
    event = {
        "response": {
            "id": "response",
            "status": "completed",
            "output": [
                {
                    "type": "function_call",
                    "name": native_voice.DELEGATE,
                    "call_id": "call-1",
                    "arguments": json.dumps({"request": "list open invoices"}),
                }
            ],
        }
    }
    await relay._completed_response(event)
    assert relay.pending is not None
    await relay.pending
    relay.response_generation = "response"
    await relay._completed_response(event)
    assert submitted == [thread.id]


def _restore_active_calls(saved: set[str]) -> None:
    from app.agentive.api import native_voice as endpoint

    endpoint._active_calls.clear()
    endpoint._active_calls.update(saved)


@pytest.mark.unit
@pytest.mark.asyncio
async def test_busy_principal_does_not_open_a_second_phone_relay(monkeypatch):
    """A principal already on a live relay is disconnected and not bridged."""
    import httpx

    from app.agentive.api import native_voice as endpoint
    from app.agentive.services.whatsapp_voice_bridge import start_phone_relay
    from app.config import settings

    saved = set(endpoint._active_calls)
    endpoint._active_calls.clear()
    endpoint._active_calls.add("user-1")
    opened = AsyncMock()
    urls: list[str] = []
    bodies: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        urls.append(str(request.url))
        bodies.append(json.loads(request.content))
        return httpx.Response(200, json={"status": "disconnected"})

    monkeypatch.setattr(settings, "JVVOICE_BASE_URL", "https://voice.example.test")
    monkeypatch.setattr(settings, "JVVOICE_API_KEY", "jvv_test_key")
    try:
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            started = await start_phone_relay(
                principal="user-1",
                whatsapp_call_id="wacid.1",
                whatsapp_api_key="meta-not-logged",
                client=client,
                open_upstream=opened,
            )
    finally:
        _restore_active_calls(saved)
    assert started is False
    opened.assert_not_awaited()
    assert urls == ["https://voice.example.test/api/calls/disconnect"]
    assert bodies[0]["user_initiated"] is False
    assert bodies[0]["whatsapp_call_id"] == "wacid.1"
    assert "meta-not-logged" not in urls[0]


@pytest.mark.unit
@pytest.mark.asyncio
async def test_idle_principal_is_not_disconnected_for_an_existing_relay(monkeypatch):
    """A principal outside the live set is not disconnected by that rule."""
    import httpx

    from app.agentive.api import native_voice as endpoint
    from app.agentive.services.whatsapp_voice_bridge import start_phone_relay
    from app.config import settings

    saved = set(endpoint._active_calls)
    endpoint._active_calls.clear()
    opened = AsyncMock(return_value="upstream")
    urls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        urls.append(str(request.url))
        return httpx.Response(200, json={"status": "disconnected"})

    monkeypatch.setattr(settings, "JVVOICE_BASE_URL", "https://voice.example.test")
    monkeypatch.setattr(settings, "JVVOICE_API_KEY", "jvv_test_key")
    try:
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            started = await start_phone_relay(
                principal="user-2",
                whatsapp_call_id="wacid.2",
                whatsapp_api_key="meta-not-logged",
                client=client,
                open_upstream=opened,
            )
    finally:
        _restore_active_calls(saved)
    assert started is True
    assert urls == []
    opened.assert_awaited_once()


@pytest.mark.unit
@pytest.mark.asyncio
async def test_browser_socket_rejects_second_call_for_active_principal(monkeypatch):
    """The browser socket still closes when the principal is already live."""
    from starlette.websockets import WebSocketState

    from app.agentive.api import native_voice as endpoint

    class _Socket:
        def __init__(self) -> None:
            self.headers = {"origin": "http://127.0.0.1:9150"}
            self.client_state = WebSocketState.CONNECTING
            self.application_state = WebSocketState.CONNECTING
            self.closed: int | None = None
            self.messages: list[dict] = []

        async def accept(self) -> None:
            self.client_state = self.application_state = WebSocketState.CONNECTED

        async def receive_text(self) -> str:
            return json.dumps(
                {
                    "ticket": "x" * 43,
                    "workspace_id": "workspace",
                    "thread_id": "thread",
                }
            )

        async def close(self, code: int = 1000) -> None:
            self.closed = code
            self.application_state = WebSocketState.DISCONNECTED

        async def send_json(self, message: dict) -> None:
            self.messages.append(message)

    saved = set(endpoint._active_calls)
    endpoint._active_calls.clear()
    endpoint._active_calls.add("caller")
    opened = AsyncMock()
    monkeypatch.setattr(endpoint.settings, "FRONTEND_ORIGIN", "http://127.0.0.1:9150")
    monkeypatch.setattr(endpoint.settings, "INTEGRAL_NATIVE_DURABLE_CHAT_ENABLED", True)
    monkeypatch.setattr(endpoint, "get_user_node", AsyncMock(return_value=object()))
    monkeypatch.setattr(
        endpoint, "redeem_ws_ticket_shared", AsyncMock(return_value="caller")
    )
    monkeypatch.setattr(endpoint, "open_upstream", opened)
    socket = _Socket()
    try:
        await endpoint.native_voice(socket)
    finally:
        _restore_active_calls(saved)
    assert socket.closed == 4409
    assert socket.messages and socket.messages[0]["type"] == "error"
    opened.assert_not_awaited()
    assert "caller" not in endpoint._active_calls or "caller" in saved


class _CloseableUpstream:
    def __init__(self) -> None:
        self.closed = False

    async def close(self) -> None:
        self.closed = True


class _HangupReceipts:
    def __init__(self) -> None:
        self.rows: list[SimpleNamespace] = []

    async def find(self, binding_id: str, meta_message_id: str):
        return [
            row
            for row in self.rows
            if row.binding_id == binding_id and row.meta_message_id == meta_message_id
        ]

    async def create(self, **fields):
        row = SimpleNamespace(**fields)
        self.rows.append(row)
        return row


def _no_sdp_call(call_id: str = "wacid.1") -> dict:
    return {
        "entry": [
            {
                "changes": [
                    {
                        "field": "calls",
                        "value": {
                            "metadata": {"phone_number_id": "pn-1"},
                            "calls": [
                                {
                                    "id": call_id,
                                    "from": "15551212000",
                                    "event": "terminate",
                                }
                            ],
                        },
                    }
                ]
            }
        ]
    }


def _reset_phone_hangups() -> None:
    from app.agentive.services import whatsapp_voice_bridge as bridge
    from app.agentive.services.whatsapp_call_control import clear_process_call_claims

    bridge._phone_relays.clear()
    bridge._hung_up_calls.clear()
    bearers = getattr(bridge, "_relay_bearers", None)
    if bearers is not None:
        bearers.clear()
    clear_process_call_claims()


@pytest.mark.unit
@pytest.mark.asyncio
async def test_hangup_closes_upstream_discards_bearer_and_disconnects_once(
    monkeypatch,
):
    """Either side of hangup closes the relay and posts one disconnect."""
    import httpx

    from app.agentive.services import whatsapp_voice_bridge as bridge
    from app.config import settings

    _reset_phone_hangups()
    upstream = _CloseableUpstream()
    submitted: list[str] = []
    relay = bridge.attach_phone_relay(
        call_id="wacid.1",
        upstream=upstream,
        bearer="relay-bearer-secret",
        submit=lambda: submitted.append("turn"),
    )
    urls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        urls.append(str(request.url))
        return httpx.Response(200, json={"status": "disconnected"})

    monkeypatch.setattr(settings, "JVVOICE_BASE_URL", "https://voice.example.test")
    monkeypatch.setattr(settings, "JVVOICE_API_KEY", "jvv_test_key")
    try:
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            await bridge.hang_up_phone_call(
                "wacid.1",
                whatsapp_api_key="meta-not-logged",
                client=client,
            )
            await bridge.hang_up_phone_call(
                "wacid.1",
                whatsapp_api_key="meta-not-logged",
                client=client,
            )
    finally:
        _reset_phone_hangups()
    assert upstream.closed is True
    assert relay.bearer == ""
    assert submitted == []
    assert urls == ["https://voice.example.test/api/calls/disconnect"]


@pytest.mark.unit
@pytest.mark.asyncio
async def test_accepted_call_without_sdp_hangs_up_without_another_accept(
    monkeypatch, caplog
):
    """A later no-SDP calls event for an accepted id disconnects and does not accept."""
    import httpx

    from app.agentive.services import whatsapp_voice_bridge as bridge
    from app.agentive.services.whatsapp_call_control import (
        handle_whatsapp_call,
        parse_whatsapp_call,
    )
    from app.config import settings

    caplog.set_level("WARNING")
    _reset_phone_hangups()
    upstream = _CloseableUpstream()
    submitted: list[str] = []
    bridge.attach_phone_relay(
        call_id="wacid.1",
        upstream=upstream,
        bearer="relay-bearer-secret",
        submit=lambda: submitted.append("turn"),
    )
    store = _HangupReceipts()
    await store.create(
        binding_id="binding-1",
        meta_message_id="wacid.1",
        kind="call",
        status="accepted",
        last_error="",
    )
    urls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        urls.append(str(request.url))
        if str(request.url).endswith("/api/calls/disconnect"):
            return httpx.Response(500, json={"status": "error"})
        return httpx.Response(200, json={"status": "connected"})

    monkeypatch.setattr(settings, "JVVOICE_BASE_URL", "https://voice.example.test")
    monkeypatch.setattr(settings, "JVVOICE_API_KEY", "jvv_test_key")
    parsed = parse_whatsapp_call(_no_sdp_call())
    binding = SimpleNamespace(id="binding-1")
    try:
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            result = await handle_whatsapp_call(
                binding,
                parsed,
                [],
                [],
                jvconnect_api_key="jvk_not_logged",
                http_client=client,
                receipt_store=store,
            )
            await handle_whatsapp_call(
                binding,
                parsed,
                [],
                [],
                jvconnect_api_key="jvk_not_logged",
                http_client=client,
                receipt_store=store,
            )
    finally:
        _reset_phone_hangups()
    assert result == {"ok": True}
    assert urls == ["https://voice.example.test/api/calls/disconnect"]
    assert upstream.closed is True
    assert submitted == []
    assert "relay-bearer-secret" not in caplog.text
    assert "meta-not-logged" not in caplog.text
    assert "v=0" not in caplog.text
    assert "status=500" in caplog.text


_META_TOKEN = "meta-token-secret"
_JVVOICE_KEY = "jvv_test_key"


def _answer_binding() -> SimpleNamespace:
    return SimpleNamespace(
        id="binding-1",
        user_id="user-1",
        workspace_id="",
        jvconnect_base_url="https://connect.example.test",
        phone_number_id="pn-1",
        display_phone="+15550009999",
        enabled=True,
        last_error="",
        api_key_last4="test",
        webhook_secret_ciphertext="stored",
    )


def _offer_call_payload() -> dict:
    return {
        "entry": [
            {
                "changes": [
                    {
                        "field": "calls",
                        "value": {
                            "metadata": {"phone_number_id": "pn-1"},
                            "calls": [
                                {
                                    "id": "wacid.1",
                                    "from": "15551212000",
                                    "event": "connect",
                                    "session": {
                                        "sdp": "v=0\r\no=- 1 1 IN IP4 127.0.0.1",
                                        "sdp_type": "offer",
                                    },
                                }
                            ],
                        },
                    }
                ]
            }
        ]
    }


def _owner_inputs(*, calling_enabled: bool = True):
    identity = SimpleNamespace(
        user_id="user-1",
        channel_user_id="+15551212000",
        verified=True,
    )
    grant = SimpleNamespace(
        workspace_id="ws-1",
        enabled=True,
        calling_enabled=calling_enabled,
    )
    return [identity], [grant]


@pytest.mark.unit
@pytest.mark.asyncio
async def test_connected_accept_mints_a_bearer_bound_to_the_call(monkeypatch, caplog):
    """A connected answer posts integral_native metadata and keeps one call bearer."""
    import httpx

    from app.agentive.services import whatsapp_voice_bridge as bridge
    from app.agentive.services.whatsapp_call_control import (
        handle_whatsapp_call,
        parse_whatsapp_call,
    )
    from app.agentive.services.whatsapp_chat_bridge import _session_id
    from app.config import settings

    caplog.set_level("DEBUG")
    _reset_phone_hangups()
    thread = SimpleNamespace(
        id="thread-1",
        provider_session_id=_session_id("ws-1", "+15551212000"),
    )
    binding = _answer_binding()
    started: list[dict] = []
    captured: dict = {}

    async def dedicated(_user_id, _workspace_id, _e164):
        return thread

    async def start_relay(**kwargs):
        started.append(
            {
                "principal": kwargs.get("principal"),
                "whatsapp_call_id": kwargs.get("whatsapp_call_id"),
                "opened": kwargs.get("open_upstream") is not None,
            }
        )
        return True

    monkeypatch.setattr(
        "app.agentive.services.whatsapp_chat_bridge._dedicated_thread", dedicated
    )
    monkeypatch.setattr(bridge, "start_phone_relay", start_relay)
    monkeypatch.setattr(settings, "JVVOICE_BASE_URL", "https://voice.example.test")
    monkeypatch.setattr(settings, "JVVOICE_API_KEY", _JVVOICE_KEY)
    monkeypatch.setattr(settings, "PUBLIC_API_BASE_URL", "https://api.example.test")

    def handler(request: httpx.Request) -> httpx.Response:
        if str(request.url).endswith("/calling/credentials"):
            return httpx.Response(200, json={"access_token": _META_TOKEN})
        captured["body"] = json.loads(request.content)
        return httpx.Response(200, json={"status": "connected"})

    identities, grants = _owner_inputs()
    parsed = parse_whatsapp_call(_offer_call_payload())
    try:
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            result = await handle_whatsapp_call(
                binding,
                parsed,
                identities,
                grants,
                jvconnect_api_key="jvk_not_logged",
                http_client=client,
                receipt_store=_HangupReceipts(),
            )
            assert result == {"ok": True}
            assert started == [
                {
                    "principal": "user-1",
                    "whatsapp_call_id": "wacid.1",
                    "opened": False,
                }
            ]
            body = captured["body"]
            bearer = body["relay_bearer"]
            assert body["bridge"] == "integral_native"
            assert body["relay_url"] == "wss://api.example.test/ws/phone-voice"
            assert body["whatsapp_call_id"] == "wacid.1"
            assert body["user_id"] == "user-1"
            assert body["session_id"] == "thread-1"
            assert "thread_id" not in body
            assert "workspace_id" not in body
            assert "principal_id" not in body
            assert "jvagent_agent_id" not in body
            assert "jvagent_base_url" not in body
            assert bearer != _JVVOICE_KEY
            assert bearer != _META_TOKEN
            assert bearer.count(".") != 2
            assert bridge.relay_bearer_allows(
                bearer,
                call_id="wacid.1",
                principal_id="user-1",
                workspace_id="ws-1",
                thread_id="thread-1",
            )
            assert not bridge.relay_bearer_allows(
                bearer,
                call_id="wacid.other",
                principal_id="user-1",
                workspace_id="ws-1",
                thread_id="thread-1",
            )
            assert not bridge.relay_bearer_allows(
                bearer,
                call_id="wacid.1",
                principal_id="other-user",
                workspace_id="ws-1",
                thread_id="thread-1",
            )
            assert bearer not in caplog.text
            assert _META_TOKEN not in caplog.text
            assert "v=0" not in caplog.text
            assert bearer not in json.dumps(thread.__dict__)
            assert bearer not in json.dumps(binding.__dict__)
            await bridge.hang_up_phone_call(
                "wacid.1",
                whatsapp_api_key=_META_TOKEN,
                client=client,
            )
            assert bridge.issued_relay_bearer("wacid.1") == ""
    finally:
        _reset_phone_hangups()


@pytest.mark.unit
@pytest.mark.asyncio
async def test_refused_admission_mints_no_bearer_and_does_not_accept(monkeypatch):
    """A refused caller gets no accept and no relay bearer."""
    import httpx

    from app.agentive.services import whatsapp_voice_bridge as bridge
    from app.agentive.services.whatsapp_call_control import (
        handle_whatsapp_call,
        parse_whatsapp_call,
    )
    from app.config import settings

    _reset_phone_hangups()
    urls: list[str] = []
    monkeypatch.setattr(settings, "JVVOICE_BASE_URL", "https://voice.example.test")
    monkeypatch.setattr(settings, "JVVOICE_API_KEY", _JVVOICE_KEY)

    def handler(request: httpx.Request) -> httpx.Response:
        urls.append(str(request.url))
        return httpx.Response(200, json={"status": "connected"})

    identities, grants = _owner_inputs(calling_enabled=False)
    parsed = parse_whatsapp_call(_offer_call_payload())
    try:
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            result = await handle_whatsapp_call(
                _answer_binding(),
                parsed,
                identities,
                grants,
                jvconnect_api_key="jvk_not_logged",
                http_client=client,
                receipt_store=_HangupReceipts(),
            )
            assert result == {"ok": True}
            assert all("/api/calls/accept" not in url for url in urls)
            assert bridge.issued_relay_bearer("wacid.1") == ""
    finally:
        _reset_phone_hangups()


@pytest.mark.unit
@pytest.mark.asyncio
async def test_non_connected_accept_discards_the_bearer_and_does_not_start(
    monkeypatch,
):
    """A status other than connected leaves no bearer and does not start a relay."""
    import httpx

    from app.agentive.services import whatsapp_voice_bridge as bridge
    from app.agentive.services.whatsapp_call_control import (
        handle_whatsapp_call,
        parse_whatsapp_call,
    )
    from app.agentive.services.whatsapp_chat_bridge import _session_id
    from app.config import settings

    _reset_phone_hangups()
    thread = SimpleNamespace(
        id="thread-1",
        provider_session_id=_session_id("ws-1", "+15551212000"),
    )
    started: list[str] = []
    accepts = {"n": 0}

    async def dedicated(_user_id, _workspace_id, _e164):
        return thread

    async def start_relay(**_kwargs):
        started.append("relay")
        return True

    monkeypatch.setattr(
        "app.agentive.services.whatsapp_chat_bridge._dedicated_thread", dedicated
    )
    monkeypatch.setattr(bridge, "start_phone_relay", start_relay)
    monkeypatch.setattr(settings, "JVVOICE_BASE_URL", "https://voice.example.test")
    monkeypatch.setattr(settings, "JVVOICE_API_KEY", _JVVOICE_KEY)
    monkeypatch.setattr(settings, "PUBLIC_API_BASE_URL", "https://api.example.test")

    def handler(request: httpx.Request) -> httpx.Response:
        if str(request.url).endswith("/calling/credentials"):
            return httpx.Response(200, json={"access_token": _META_TOKEN})
        accepts["n"] += 1
        return httpx.Response(200, json={"status": "error"})

    identities, grants = _owner_inputs()
    parsed = parse_whatsapp_call(_offer_call_payload())
    try:
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            result = await handle_whatsapp_call(
                _answer_binding(),
                parsed,
                identities,
                grants,
                jvconnect_api_key="jvk_not_logged",
                http_client=client,
                receipt_store=_HangupReceipts(),
            )
            assert result == {"ok": True}
            assert accepts["n"] == 1
            assert started == ["relay"]
            assert bridge.issued_relay_bearer("wacid.1") == ""
    finally:
        _reset_phone_hangups()


@pytest.mark.unit
@pytest.mark.asyncio
async def test_busy_principal_is_refused_without_accept(monkeypatch, caplog):
    """A live principal is disconnected once and is not accepted."""
    import httpx

    from app.agentive.api import native_voice as endpoint
    from app.agentive.services import whatsapp_voice_bridge as bridge
    from app.agentive.services.whatsapp_call_control import (
        handle_whatsapp_call,
        parse_whatsapp_call,
    )
    from app.config import settings

    caplog.set_level("DEBUG")
    _reset_phone_hangups()
    saved = set(endpoint._active_calls)
    endpoint._active_calls.clear()
    endpoint._active_calls.add("user-1")
    urls: list[str] = []
    bodies: list[dict] = []
    monkeypatch.setattr(settings, "JVVOICE_BASE_URL", "https://voice.example.test")
    monkeypatch.setattr(settings, "JVVOICE_API_KEY", _JVVOICE_KEY)

    def handler(request: httpx.Request) -> httpx.Response:
        urls.append(str(request.url))
        if request.content:
            bodies.append(json.loads(request.content))
        if str(request.url).endswith("/calling/credentials"):
            return httpx.Response(200, json={"access_token": _META_TOKEN})
        return httpx.Response(200, json={"status": "disconnected"})

    identities, grants = _owner_inputs()
    parsed = parse_whatsapp_call(_offer_call_payload())
    store = _HangupReceipts()
    try:
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            result = await handle_whatsapp_call(
                _answer_binding(),
                parsed,
                identities,
                grants,
                jvconnect_api_key="jvk_not_logged",
                http_client=client,
                receipt_store=store,
            )
    finally:
        endpoint._active_calls.clear()
        endpoint._active_calls.update(saved)
        _reset_phone_hangups()
    assert result == {"ok": True}
    assert not any(url.endswith("/api/calls/accept") for url in urls)
    disconnects = [url for url in urls if url.endswith("/api/calls/disconnect")]
    assert disconnects == ["https://voice.example.test/api/calls/disconnect"]
    assert bodies[-1]["user_initiated"] is False
    assert bodies[-1]["whatsapp_call_id"] == "wacid.1"
    assert bridge.issued_relay_bearer("wacid.1") == ""
    assert store.rows[0].status == "refused"
    assert store.rows[0].last_error == "principal_busy"
    assert _META_TOKEN not in caplog.text
    assert "v=0" not in caplog.text


@pytest.mark.unit
@pytest.mark.asyncio
async def test_relative_relay_url_does_not_accept(monkeypatch, caplog):
    """An empty public base leaves a relative relay URL and does not accept."""
    import httpx

    from app.agentive.api import native_voice as endpoint
    from app.agentive.services import whatsapp_voice_bridge as bridge
    from app.agentive.services.whatsapp_call_control import (
        handle_whatsapp_call,
        parse_whatsapp_call,
    )
    from app.agentive.services.whatsapp_chat_bridge import _session_id
    from app.config import settings

    caplog.set_level("DEBUG")
    _reset_phone_hangups()
    saved = set(endpoint._active_calls)
    endpoint._active_calls.clear()
    minted: list[str] = []
    urls: list[str] = []
    real_mint = bridge.mint_relay_bearer

    def spy_mint(**kwargs):
        token = real_mint(**kwargs)
        minted.append(token)
        return token

    async def dedicated(_user_id, workspace_id, e164):
        return SimpleNamespace(
            id="thread-1",
            provider_session_id=_session_id(workspace_id, e164),
        )

    monkeypatch.setattr(bridge, "mint_relay_bearer", spy_mint)
    monkeypatch.setattr(
        "app.agentive.services.whatsapp_chat_bridge._dedicated_thread", dedicated
    )
    monkeypatch.setattr(settings, "JVVOICE_BASE_URL", "https://voice.example.test")
    monkeypatch.setattr(settings, "JVVOICE_API_KEY", _JVVOICE_KEY)
    monkeypatch.setattr(settings, "PUBLIC_API_BASE_URL", "")

    def handler(request: httpx.Request) -> httpx.Response:
        urls.append(str(request.url))
        if str(request.url).endswith("/calling/credentials"):
            return httpx.Response(200, json={"access_token": _META_TOKEN})
        return httpx.Response(200, json={"status": "connected"})

    store = _HangupReceipts()
    identities, grants = _owner_inputs()
    parsed = parse_whatsapp_call(_offer_call_payload())
    try:
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            result = await handle_whatsapp_call(
                _answer_binding(),
                parsed,
                identities,
                grants,
                jvconnect_api_key="jvk_not_logged",
                http_client=client,
                receipt_store=store,
            )
    finally:
        endpoint._active_calls.clear()
        endpoint._active_calls.update(saved)
        _reset_phone_hangups()
    assert result == {"ok": True}
    assert not any(url.endswith("/api/calls/accept") for url in urls)
    assert bridge.issued_relay_bearer("wacid.1") == ""
    assert minted
    assert store.rows[0].status == "failed"
    assert store.rows[0].last_error == "relay_url_unavailable"
    assert minted[0] not in caplog.text
    assert _META_TOKEN not in caplog.text


@pytest.mark.unit
@pytest.mark.asyncio
async def test_thread_mismatch_does_not_accept(monkeypatch, caplog):
    """A thread bound to another caller is failed and not accepted."""
    import httpx

    from app.agentive.api import native_voice as endpoint
    from app.agentive.services import whatsapp_voice_bridge as bridge
    from app.agentive.services.whatsapp_call_control import (
        handle_whatsapp_call,
        parse_whatsapp_call,
    )
    from app.config import settings

    caplog.set_level("DEBUG")
    _reset_phone_hangups()
    saved = set(endpoint._active_calls)
    endpoint._active_calls.clear()
    urls: list[str] = []

    async def dedicated(_user_id, _workspace_id, _e164):
        return SimpleNamespace(
            id="thread-1",
            provider_session_id="wa:ws-1:+19995550000",
        )

    monkeypatch.setattr(
        "app.agentive.services.whatsapp_chat_bridge._dedicated_thread", dedicated
    )
    monkeypatch.setattr(settings, "JVVOICE_BASE_URL", "https://voice.example.test")
    monkeypatch.setattr(settings, "JVVOICE_API_KEY", _JVVOICE_KEY)
    monkeypatch.setattr(settings, "PUBLIC_API_BASE_URL", "https://api.example.test")

    def handler(request: httpx.Request) -> httpx.Response:
        urls.append(str(request.url))
        if str(request.url).endswith("/calling/credentials"):
            return httpx.Response(200, json={"access_token": _META_TOKEN})
        return httpx.Response(200, json={"status": "connected"})

    store = _HangupReceipts()
    identities, grants = _owner_inputs()
    parsed = parse_whatsapp_call(_offer_call_payload())
    try:
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            result = await handle_whatsapp_call(
                _answer_binding(),
                parsed,
                identities,
                grants,
                jvconnect_api_key="jvk_not_logged",
                http_client=client,
                receipt_store=store,
            )
    finally:
        endpoint._active_calls.clear()
        endpoint._active_calls.update(saved)
        _reset_phone_hangups()
    assert result == {"ok": True}
    assert not any(url.endswith("/api/calls/accept") for url in urls)
    assert bridge.issued_relay_bearer("wacid.1") == ""
    assert store.rows[0].status == "failed"
    assert store.rows[0].last_error == "thread_mismatch"
    assert _META_TOKEN not in caplog.text
    assert "v=0" not in caplog.text


def _mint_call_bearer() -> str:
    from app.agentive.services import whatsapp_voice_bridge as bridge

    return bridge.mint_relay_bearer(
        call_id="wacid.1",
        principal_id="user-1",
        workspace_id="ws-1",
        thread_id="thread-1",
    )


class _ReadyUpstream(_FakeUpstream):
    """Queues session.updated when the relay sends its session configuration."""

    async def send(self, event: str) -> None:
        await super().send(event)
        payload = json.loads(event)
        if payload.get("type") == "session.update":
            await self._events.put({"type": "session.updated", "session": {}})


@pytest.mark.unit
@pytest.mark.asyncio
async def test_phone_socket_delivers_one_pcm_chunk_for_a_valid_bearer(monkeypatch):
    """A valid bearer runs the phone relay: one PCM chunk in, playback bytes out."""
    import time
    from pathlib import Path

    from fastapi import FastAPI
    from starlette.testclient import TestClient

    from app.agentive.api import native_voice
    from app.agentive.services import whatsapp_voice_bridge as bridge
    from app.services import chat_conversation_updates

    _reset_phone_hangups()
    native_voice._active_calls.clear()
    token = _mint_call_bearer()
    upstream = _ReadyUpstream()
    opened: list[str] = []
    holder: dict = {}

    async def open_fake(_credential):
        holder["loop"] = asyncio.get_running_loop()
        opened.append("upstream")
        return upstream

    async def credential(_workspace):
        assert native_voice.principal_has_live_call("user-1")
        return SimpleNamespace(model="fake-model", api_key="not-logged")

    async def allow(scope):
        return SimpleNamespace(id=scope.thread)

    async def no_history(self):
        return []

    real_open = native_voice.open_phone_relay

    def open_quiet(*args, **kwargs):
        relay = real_open(*args, **kwargs)

        async def no_reviews(*_args, **_kwargs):
            raise RuntimeError("no review store")

        relay.thread_status = no_reviews
        return relay

    class _Subscription:
        def __init__(self) -> None:
            self._closed = asyncio.Event()

        async def wait(self) -> None:
            await self._closed.wait()

        def close(self) -> None:
            self._closed.set()

    async def snapshot(*_args, **_kwargs):
        return SimpleNamespace(cursor="0", gap=False, updates=[])

    monkeypatch.setattr(native_voice, "open_upstream", open_fake)
    monkeypatch.setattr(native_voice, "resolve_realtime_credential", credential)
    monkeypatch.setattr(native_voice, "authorize", allow)
    monkeypatch.setattr(native_voice, "open_phone_relay", open_quiet)
    monkeypatch.setattr(VoiceRelay, "_current_conversation_history", no_history)
    monkeypatch.setattr(
        chat_conversation_updates,
        "subscribe_conversation_updates",
        lambda *_args: _Subscription(),
    )
    monkeypatch.setattr(
        chat_conversation_updates, "read_conversation_snapshot", snapshot
    )
    monkeypatch.setattr(
        native_voice.settings, "FRONTEND_ORIGIN", "https://browser.example"
    )
    monkeypatch.setattr(
        native_voice.settings, "INTEGRAL_NATIVE_DURABLE_CHAT_ENABLED", False
    )
    chunk = b"\x00\x01" * 8
    playback = b"\x02\x00"
    api = FastAPI()
    api.include_router(native_voice.router)

    def wait_for(predicate) -> None:
        deadline = time.monotonic() + 2
        while time.monotonic() < deadline:
            if predicate():
                return
            time.sleep(0.01)
        raise AssertionError("phone relay did not reach the expected event")

    try:
        with TestClient(api) as client:
            with client.websocket_connect(
                "/ws/phone-voice",
                headers={"x-integral-phone-bearer": token},
            ) as socket:
                wait_for(
                    lambda: any(
                        item.get("type") == "session.update" for item in upstream.sent
                    )
                )
                ready = socket.receive_json()
                assert ready["type"] == "ready"
                assert ready["thread_id"] == "thread-1"
                socket.send_bytes(chunk)
                wait_for(
                    lambda: any(
                        item.get("type") == "input_audio_buffer.append"
                        for item in upstream.sent
                    )
                )
                loop = holder["loop"]
                loop.call_soon_threadsafe(
                    upstream._events.put_nowait,
                    {"type": "response.created", "response": {"id": "resp-1"}},
                )
                loop.call_soon_threadsafe(
                    upstream._events.put_nowait,
                    {
                        "type": "response.output_audio.delta",
                        "response_id": "resp-1",
                        "delta": base64.b64encode(playback).decode("ascii"),
                    },
                )
                assert socket.receive_bytes() == playback
                socket.close()
        assert opened == ["upstream"]
        assert upstream.sent[0]["type"] == "session.update"
        assert {
            "type": "input_audio_buffer.append",
            "audio": base64.b64encode(chunk).decode("ascii"),
        } in upstream.sent
        assert bridge.issued_relay_bearer("wacid.1") == ""
        assert native_voice.principal_has_live_call("user-1") is False
        source = Path(native_voice.__file__).read_text(encoding="utf-8")
        assert "jvspatial @endpoint has no WebSocket primitive" in source
        assert "/ws/phone-voice" in source
        assert "relay.run" in source
    finally:
        _reset_phone_hangups()
        native_voice._active_calls.clear()


@pytest.mark.unit
@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["missing", "wrong", "expired"])
async def test_phone_socket_rejects_a_bearer_without_opening_upstream(
    monkeypatch, kind
):
    """A missing, wrong, or expired bearer closes the socket and does not open upstream."""
    from fastapi import FastAPI
    from starlette.testclient import TestClient
    from starlette.websockets import WebSocketDisconnect

    from app.agentive.api import native_voice
    from app.agentive.services import whatsapp_voice_bridge as bridge

    _reset_phone_hangups()
    native_voice._active_calls.clear()
    token = _mint_call_bearer()
    if kind == "expired":
        bridge.discard_relay_bearer("wacid.1")
        presented = token
    elif kind == "wrong":
        presented = "wrong-bearer"
    else:
        presented = ""

    async def open_fake(_credential):
        raise AssertionError("upstream opened")

    hung: list[str] = []

    async def spy_hangup(call_id, **_kwargs):
        hung.append(call_id)

    monkeypatch.setattr(bridge, "hang_up_phone_call", spy_hangup)
    monkeypatch.setattr(native_voice, "open_upstream", open_fake)
    api = FastAPI()
    api.include_router(native_voice.router)
    headers = {}
    if presented:
        headers["x-integral-phone-bearer"] = presented
    try:
        with TestClient(api) as client:
            with pytest.raises(WebSocketDisconnect):
                with client.websocket_connect("/ws/phone-voice", headers=headers):
                    pass
        if kind == "expired":
            assert bridge.issued_relay_bearer("wacid.1") == ""
        else:
            assert bridge.issued_relay_bearer("wacid.1") == token
        assert native_voice.principal_has_live_call("user-1") is False
        assert hung == []
    finally:
        _reset_phone_hangups()
        native_voice._active_calls.clear()


@pytest.mark.unit
def test_phone_voice_attaches_the_relay_and_hangs_up_on_close(monkeypatch):
    """A started phone socket registers the call and hangs it up when it closes."""
    import threading
    import time

    from fastapi import FastAPI
    from starlette.testclient import TestClient
    from starlette.websockets import WebSocketDisconnect

    from app.agentive.api import native_voice
    from app.agentive.services import whatsapp_voice_bridge as bridge
    from app.config import settings

    _reset_phone_hangups()
    native_voice._active_calls.clear()
    token = _mint_call_bearer()
    release = threading.Event()
    entered = threading.Event()
    attached: list[str] = []
    hung: list[str] = []
    real_attach = bridge.attach_phone_relay
    real_hangup = bridge.hang_up_phone_call

    def spy_attach(**kwargs):
        attached.append(kwargs["call_id"])
        return real_attach(**kwargs)

    async def spy_hangup(call_id, **kwargs):
        hung.append(call_id)
        await real_hangup(call_id, **kwargs)

    class _Hold:
        async def run(self):
            entered.set()
            while not release.is_set():
                await asyncio.sleep(0.01)

    async def open_fake(_credential):
        return SimpleNamespace()

    async def credential(_workspace):
        return SimpleNamespace(model="fake-model", api_key="not-logged")

    monkeypatch.setattr(bridge, "attach_phone_relay", spy_attach)
    monkeypatch.setattr(bridge, "hang_up_phone_call", spy_hangup)
    monkeypatch.setattr(native_voice, "open_upstream", open_fake)
    monkeypatch.setattr(native_voice, "resolve_realtime_credential", credential)
    monkeypatch.setattr(native_voice, "open_phone_relay", lambda *_a, **_k: _Hold())
    monkeypatch.setattr(settings, "JVVOICE_BASE_URL", "")
    monkeypatch.setattr(settings, "JVVOICE_API_KEY", "")
    api = FastAPI()
    api.include_router(native_voice.router)
    try:
        with TestClient(api) as client:
            with client.websocket_connect(
                "/ws/phone-voice",
                headers={"x-integral-phone-bearer": token},
            ) as socket:
                deadline = time.monotonic() + 2
                while time.monotonic() < deadline and not entered.is_set():
                    time.sleep(0.01)
                assert entered.is_set()
                assert attached == ["wacid.1"]
                assert "wacid.1" in bridge._phone_relays
                assert bridge._phone_relays["wacid.1"].bearer == token
                release.set()
                with pytest.raises(WebSocketDisconnect):
                    socket.receive_json()
        assert hung == ["wacid.1"]
        assert native_voice.principal_has_live_call("user-1") is False
        assert "wacid.1" not in bridge._phone_relays
    finally:
        release.set()
        _reset_phone_hangups()
        native_voice._active_calls.clear()


@pytest.mark.unit
def test_phone_socket_disconnects_when_the_principal_is_already_live(monkeypatch):
    """A valid bearer for a live principal closes 4403 and disconnects that call."""
    from fastapi import FastAPI
    from starlette.testclient import TestClient
    from starlette.websockets import WebSocketDisconnect

    from app.agentive.api import native_voice
    from app.agentive.services import whatsapp_voice_bridge as bridge

    _reset_phone_hangups()
    native_voice._active_calls.clear()
    native_voice._active_calls.add("user-1")
    token = _mint_call_bearer()
    hung: list[str] = []

    async def spy_hangup(call_id, **_kwargs):
        hung.append(call_id)

    async def open_fake(_credential):
        raise AssertionError("upstream opened")

    monkeypatch.setattr(bridge, "hang_up_phone_call", spy_hangup)
    monkeypatch.setattr(native_voice, "open_upstream", open_fake)
    api = FastAPI()
    api.include_router(native_voice.router)
    try:
        with TestClient(api) as client:
            with client.websocket_connect(
                "/ws/phone-voice",
                headers={"x-integral-phone-bearer": token},
            ) as socket:
                with pytest.raises(WebSocketDisconnect) as caught:
                    socket.receive_json()
        assert caught.value.code == 4403
        assert hung == ["wacid.1"]
        assert native_voice.principal_has_live_call("user-1") is True
        assert bridge.issued_relay_bearer("wacid.1") == ""
    finally:
        _reset_phone_hangups()
        native_voice._active_calls.clear()


@pytest.mark.unit
def test_phone_socket_disconnects_when_realtime_credential_is_missing(monkeypatch):
    """A valid bearer with no realtime credential hangs up and does not open upstream."""
    from fastapi import FastAPI
    from starlette.testclient import TestClient
    from starlette.websockets import WebSocketDisconnect

    from app.agentive.api import native_voice
    from app.agentive.services import whatsapp_voice_bridge as bridge

    _reset_phone_hangups()
    native_voice._active_calls.clear()
    token = _mint_call_bearer()
    hung: list[str] = []

    async def spy_hangup(call_id, **_kwargs):
        hung.append(call_id)

    async def open_fake(_credential):
        raise AssertionError("upstream opened")

    async def no_credential(_workspace):
        return None

    monkeypatch.setattr(bridge, "hang_up_phone_call", spy_hangup)
    monkeypatch.setattr(native_voice, "open_upstream", open_fake)
    monkeypatch.setattr(native_voice, "resolve_realtime_credential", no_credential)
    api = FastAPI()
    api.include_router(native_voice.router)
    try:
        with TestClient(api) as client:
            with client.websocket_connect(
                "/ws/phone-voice",
                headers={"x-integral-phone-bearer": token},
            ) as socket:
                with pytest.raises(WebSocketDisconnect) as caught:
                    socket.receive_json()
        assert caught.value.code == 4404
        assert hung == ["wacid.1"]
        assert native_voice.principal_has_live_call("user-1") is False
        assert bridge.issued_relay_bearer("wacid.1") == ""
    finally:
        _reset_phone_hangups()
        native_voice._active_calls.clear()


@pytest.mark.unit
def test_browser_voice_socket_does_not_admit_a_phone_bearer(monkeypatch):
    """The browser socket still requires origin and a ticket. A phone bearer is not admission."""
    from unittest.mock import AsyncMock

    from fastapi import FastAPI
    from starlette.testclient import TestClient
    from starlette.websockets import WebSocketDisconnect

    from app.agentive.api import native_voice

    _reset_phone_hangups()
    token = _mint_call_bearer()
    api = FastAPI()
    api.include_router(native_voice.router)
    monkeypatch.setattr(
        native_voice.settings, "FRONTEND_ORIGIN", "http://127.0.0.1:9150"
    )
    monkeypatch.setattr(
        native_voice.settings, "INTEGRAL_NATIVE_DURABLE_CHAT_ENABLED", True
    )
    redeem = AsyncMock(return_value="fixture-user")
    opener = AsyncMock()
    monkeypatch.setattr(native_voice, "redeem_ws_ticket_shared", redeem)
    monkeypatch.setattr(native_voice, "open_upstream", opener)
    try:
        with TestClient(api) as client:
            with pytest.raises(WebSocketDisconnect):
                with client.websocket_connect(
                    "/ws/native-voice",
                    headers={
                        "origin": "https://phone.example",
                        "x-integral-phone-bearer": token,
                    },
                ):
                    pass
        redeem.assert_not_awaited()
        opener.assert_not_awaited()
    finally:
        _reset_phone_hangups()


# --- Phone channel session authorization (real authorize, no database) ---


def _wa_thread(workspace: str, e164: str, **overrides) -> SimpleNamespace:
    from app.agentive.services.whatsapp_chat_bridge import _session_id

    fields = {
        "id": "thread-wa",
        "archived": False,
        "provider_id": "integral_native",
        "user_id": "user-1",
        "workspace_id": workspace,
        "agent_profile_key": "",
        "provider_session_id": _session_id(workspace, e164),
    }
    fields.update(overrides)
    return SimpleNamespace(**fields)


def _patch_real_authorize(monkeypatch, thread) -> None:
    from app.models.nodes import ChatThread

    monkeypatch.setattr(ChatThread, "get", AsyncMock(return_value=thread))
    monkeypatch.setattr(
        native_voice, "can_access_workspace", AsyncMock(return_value="member")
    )


@pytest.mark.unit
@pytest.mark.asyncio
async def test_authorize_allows_wa_thread_only_for_its_phone_channel_session(
    monkeypatch,
):
    from app.agentive.services.whatsapp_chat_bridge import _session_id
    from app.api.errors import InsufficientPermissionsError

    e164 = "+15551212000"
    thread = _wa_thread("ws-1", e164)
    _patch_real_authorize(monkeypatch, thread)
    channel = _session_id("ws-1", e164)

    phone = VoiceScope("user-1", "ws-1", "", "thread-wa", channel_session=channel)
    assert await native_voice.authorize(phone) is thread

    # Browser scope: empty channel session keeps refusing the wa: thread.
    browser = VoiceScope("user-1", "ws-1", "", "thread-wa")
    with pytest.raises(InsufficientPermissionsError):
        await native_voice.authorize(browser)

    # The relay token is never accepted in place of the channel session.
    token_only = VoiceScope(
        "user-1", "ws-1", "", "thread-wa", session=channel, channel_session=""
    )
    with pytest.raises(InsufficientPermissionsError):
        await native_voice.authorize(token_only)


@pytest.mark.unit
@pytest.mark.asyncio
@pytest.mark.parametrize(
    "channel_session",
    [
        "wa:ws-1:+15559999999",  # another caller's session
        "wa:ws-2:+15551212000",  # another workspace
        "ws-1:+15551212000",  # not a wa: id
    ],
)
async def test_authorize_refuses_mismatched_channel_session(
    monkeypatch, channel_session
):
    from app.api.errors import InsufficientPermissionsError

    _patch_real_authorize(monkeypatch, _wa_thread("ws-1", "+15551212000"))
    scope = VoiceScope(
        "user-1", "ws-1", "", "thread-wa", channel_session=channel_session
    )
    with pytest.raises(InsufficientPermissionsError):
        await native_voice.authorize(scope)


@pytest.mark.unit
@pytest.mark.asyncio
async def test_authorize_channel_session_cannot_widen_other_checks(monkeypatch):
    from app.agentive.services.whatsapp_chat_bridge import _session_id
    from app.api.errors import InsufficientPermissionsError

    channel = _session_id("ws-1", "+15551212000")
    scope = VoiceScope("user-1", "ws-1", "", "thread-wa", channel_session=channel)
    for overrides in (
        {"user_id": "someone-else"},
        {"archived": True},
        {"provider_id": "other"},
        {"agent_profile_key": "profile"},
    ):
        _patch_real_authorize(
            monkeypatch, _wa_thread("ws-1", "+15551212000", **overrides)
        )
        with pytest.raises(InsufficientPermissionsError):
            await native_voice.authorize(scope)

    # A non-wa thread is not reachable with a phone channel session either.
    _patch_real_authorize(
        monkeypatch,
        _wa_thread("ws-1", "+15551212000", provider_session_id="browser-session"),
    )
    with pytest.raises(InsufficientPermissionsError):
        await native_voice.authorize(scope)


@pytest.mark.unit
def test_is_workspace_voice_thread_requires_matching_channel_session():
    from app.agentive.services.whatsapp_chat_bridge import _session_id
    from app.services.chat_voice_fence import is_workspace_voice_thread

    channel = _session_id("ws-1", "+15551212000")

    def row(session: str) -> dict:
        return {
            "entity": "ChatThread",
            "context": {
                "user_id": "user-1",
                "workspace_id": "ws-1",
                "provider_id": "integral_native",
                "provider_session_id": session,
            },
        }

    assert is_workspace_voice_thread(row(channel), "user-1", "ws-1", channel)
    assert not is_workspace_voice_thread(row(channel), "user-1", "ws-1")
    assert not is_workspace_voice_thread(row(channel), "user-1", "ws-1", "")
    assert not is_workspace_voice_thread(
        row(channel), "user-1", "ws-1", "wa:ws-1:+15559999999"
    )
    assert not is_workspace_voice_thread(row(channel), "user-1", "ws-2", channel)
    assert not is_workspace_voice_thread(row("browser"), "user-1", "ws-1", channel)
    assert is_workspace_voice_thread(row("browser"), "user-1", "ws-1")


@pytest.mark.unit
@pytest.mark.asyncio
async def test_phone_follow_up_target_carries_channel_session_into_the_fence(
    monkeypatch,
):
    from app.agentive.services.whatsapp_chat_bridge import _session_id
    from app.api.errors import InsufficientPermissionsError
    from app.services import native_voice_followups as followups
    from app.services.chat_voice_fence import VoiceFollowUpFence

    channel = _session_id("ws-1", "+15551212000")
    row = {
        "entity": "ChatThread",
        "context": {
            "user_id": "user-1",
            "workspace_id": "ws-1",
            "provider_id": "integral_native",
            "provider_session_id": channel,
            "active_work_item_id": "work-1",
        },
    }
    database = SimpleNamespace(get=AsyncMock(return_value=row))
    monkeypatch.setattr(
        followups, "get_default_context", lambda: SimpleNamespace(database=database)
    )
    monkeypatch.setattr(followups, "require_voice_workspace_role", AsyncMock())

    phone = VoiceScope("user-1", "ws-1", "", "thread-wa", channel_session=channel)
    target = await followups.resolve_voice_follow_up_target(phone)
    assert target is not None
    assert target.fence == VoiceFollowUpFence(
        "user-1", "ws-1", "thread-wa", "work-1", channel
    )
    # Existing four-field constructors still mean "no channel session".
    assert VoiceFollowUpFence("u", "w", "t", "i").channel_session == ""

    browser = VoiceScope("user-1", "ws-1", "", "thread-wa")
    with pytest.raises(InsufficientPermissionsError):
        await followups.resolve_voice_follow_up_target(browser)


@pytest.mark.unit
@pytest.mark.asyncio
async def test_read_conversation_snapshot_wa_thread_needs_channel_session(
    monkeypatch,
):
    from contextlib import asynccontextmanager

    from app.agentive.harness import adaptive
    from app.agentive.services.whatsapp_chat_bridge import _session_id
    from app.api.errors import InsufficientPermissionsError
    from app.models.nodes import ChatThread
    from app.services import (
        chat_conversation_updates,
        chat_history_authority,
        permissions,
    )
    from app.services import release_admission as admission
    from app.services import (
        workspace_permissions,
    )

    channel = _session_id("ws-1", "+15551212000")
    thread = _wa_thread("ws-1", "+15551212000")

    @asynccontextmanager
    async def no_admission(*_args, **_kwargs):
        yield

    monkeypatch.setattr(admission, "release_admission", no_admission)
    monkeypatch.setattr(permissions, "get_user_node", AsyncMock(return_value=object()))
    monkeypatch.setattr(
        workspace_permissions, "can_access_workspace", AsyncMock(return_value="member")
    )
    monkeypatch.setattr(
        chat_history_authority, "chat_history_workspace", AsyncMock(return_value="ws-1")
    )
    monkeypatch.setattr(ChatThread, "find_one", AsyncMock(return_value=thread))
    monkeypatch.setattr(adaptive, "recent_public_messages", AsyncMock(return_value=[]))

    read = chat_conversation_updates.read_conversation_snapshot
    scope = ("user-1", "ws-1", "thread-wa")

    # Existing callers keep the empty default and keep refusing wa: threads.
    with pytest.raises(InsufficientPermissionsError):
        await read(*scope)
    with pytest.raises(InsufficientPermissionsError):
        await read(*scope, channel_session="wa:ws-1:+15559999999")
    with pytest.raises(InsufficientPermissionsError):
        await read(*scope, channel_session="wa:ws-2:+15551212000")

    snapshot = await read(*scope, channel_session=channel)
    assert snapshot.updates == ()


@pytest.mark.unit
@pytest.mark.asyncio
async def test_phone_peer_watch_passes_channel_session_to_the_snapshot_read():
    reads: list[dict] = []

    async def read(*key, profile_key, cursor, **kwargs):
        reads.append({"key": key, "profile_key": profile_key, **kwargs})
        return SimpleNamespace(cursor="0", gap=False, updates=())

    class _Subscription:
        async def wait(self) -> None:
            await asyncio.Event().wait()

        def close(self) -> None:
            return None

    async def allow(_scope):
        return None

    for channel, expected in (("wa:ws-1:+15551212000", True), ("", False)):
        reads.clear()
        scope = VoiceScope("user-1", "ws-1", "", "thread-1", channel_session=channel)
        relay = VoiceRelay(
            ServerAudioPeer(),
            _FakeUpstream(),
            scope,
            check=allow,
            conversation_subscribe=lambda *_key: _Subscription(),
            conversation_snapshot=read,
        )
        relay.conversation_ready.set()
        task = asyncio.create_task(relay._watch_peer_conversation())
        await asyncio.wait_for(relay.conversation_baselined.wait(), 2)
        relay.closing = True
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        assert reads and ("channel_session" in reads[0]) is expected
        if expected:
            assert reads[0]["channel_session"] == channel


@pytest.mark.unit
def test_relay_bearer_carries_channel_session_and_defaults_to_empty():
    from app.agentive.services import whatsapp_voice_bridge as bridge

    _reset_phone_hangups()
    try:
        legacy = _mint_call_bearer()
        assert bridge.relay_bearer_record(legacy)["channel_session"] == ""
        bridge.discard_relay_bearer("wacid.1")
        token = bridge.mint_relay_bearer(
            call_id="wacid.1",
            principal_id="user-1",
            workspace_id="ws-1",
            thread_id="thread-1",
            channel_session="wa:ws-1:+15551212000",
        )
        record = bridge.relay_bearer_record(token)
        assert record["channel_session"] == "wa:ws-1:+15551212000"
        assert bridge.relay_bearer_allows(
            token,
            call_id="wacid.1",
            principal_id="user-1",
            workspace_id="ws-1",
            thread_id="thread-1",
        )
        assert not bridge.relay_bearer_allows(
            token,
            call_id="wacid.1",
            principal_id="user-2",
            workspace_id="ws-1",
            thread_id="thread-1",
        )
    finally:
        _reset_phone_hangups()
