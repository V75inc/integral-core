"""Public WhatsApp speech. No member relay and no live call."""

from __future__ import annotations

import asyncio
import base64
import json
import time
from types import SimpleNamespace

import pytest

from app.agentive.services.whatsapp_public_voice import public_session_update


def _reset_hangups() -> None:
    from app.agentive.services import whatsapp_voice_bridge as bridge

    bridge._hung_up_calls.clear()
    bridge._phone_relays.clear()
    bridge._relay_bearers.clear()
    bridge._call_api_keys.clear()


@pytest.mark.unit
def test_public_session_update_has_no_tools():
    """The spoken session carries the persona and no workspace tools."""
    update = public_session_update("Be brief and warm.")
    session = update["session"]
    blob = json.dumps(update)

    assert session["tools"] == []
    assert session["tool_choice"] == "none"
    assert "Be brief and warm." in session["instructions"]
    assert "signed-in" not in blob
    assert "resident" not in blob
    assert "delegate_to_integral" not in blob


def _public_bearer() -> str:
    from app.agentive.services import whatsapp_voice_bridge as bridge

    return bridge.mint_public_relay_bearer(
        call_id="wacid.1",
        workspace_id="ws-1",
        binding_id="binding-1",
        grant_id="grant-1",
        revision=4,
        profile_key="desk",
        published_version=2,
    )


class _ScriptedUpstream:
    """Records session events and yields provider messages from this loop."""

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


@pytest.mark.unit
@pytest.mark.asyncio
async def test_member_bearer_is_rejected_by_the_public_socket(monkeypatch):
    """A member phone bearer cannot open the public speech socket."""
    from fastapi import FastAPI
    from starlette.testclient import TestClient
    from starlette.websockets import WebSocketDisconnect

    from app.agentive.api import native_voice
    from app.agentive.services import whatsapp_voice_bridge as bridge

    _reset_hangups()
    native_voice._active_calls.clear()
    token = bridge.mint_relay_bearer(
        call_id="wacid.1",
        principal_id="user-1",
        workspace_id="ws-1",
        thread_id="thread-1",
    )

    async def open_fake(_credential):
        raise AssertionError("upstream opened")

    monkeypatch.setattr(native_voice, "open_upstream", open_fake)
    api = FastAPI()
    api.include_router(native_voice.router)
    try:
        with (
            TestClient(api) as client,
            pytest.raises(WebSocketDisconnect),
            client.websocket_connect(
                "/ws/public-phone-voice",
                headers={"x-integral-phone-bearer": token},
            ),
        ):
            pass
        assert native_voice.principal_has_live_call("user-1") is False
        assert bridge.issued_relay_bearer("wacid.1") == token
    finally:
        _reset_hangups()
        native_voice._active_calls.clear()


@pytest.mark.unit
@pytest.mark.asyncio
async def test_public_bearer_is_rejected_by_the_member_socket(monkeypatch):
    """A public bearer cannot open the member phone relay."""
    from fastapi import FastAPI
    from starlette.testclient import TestClient
    from starlette.websockets import WebSocketDisconnect

    from app.agentive.api import native_voice

    _reset_hangups()
    native_voice._active_calls.clear()
    token = _public_bearer()

    async def open_fake(_credential):
        raise AssertionError("upstream opened")

    monkeypatch.setattr(native_voice, "open_upstream", open_fake)
    api = FastAPI()
    api.include_router(native_voice.router)
    try:
        with (
            TestClient(api) as client,
            pytest.raises(WebSocketDisconnect),
            client.websocket_connect(
                "/ws/phone-voice",
                headers={"x-integral-phone-bearer": token},
            ),
        ):
            pass
        assert native_voice._active_calls == set()
    finally:
        _reset_hangups()
        native_voice._active_calls.clear()


@pytest.mark.unit
@pytest.mark.asyncio
async def test_stale_public_grant_closes_before_the_model(monkeypatch):
    """A public bearer whose profile is no longer live does not open upstream."""
    from fastapi import FastAPI
    from starlette.testclient import TestClient
    from starlette.websockets import WebSocketDisconnect

    from app.agentive.api import native_voice
    from app.agentive.services import whatsapp_voice_bridge as bridge

    _reset_hangups()
    token = _public_bearer()

    async def open_fake(_credential):
        raise AssertionError("upstream opened")

    async def gone(_record):
        return None

    async def quiet_hangup(_call_id, **_kwargs):
        return None

    monkeypatch.setattr(native_voice, "open_upstream", open_fake)
    monkeypatch.setattr(bridge, "confirm_public_call", gone)
    monkeypatch.setattr(bridge, "hang_up_phone_call", quiet_hangup)
    api = FastAPI()
    api.include_router(native_voice.router)
    try:
        with (
            TestClient(api) as client,
            client.websocket_connect(
                "/ws/public-phone-voice",
                headers={"x-integral-phone-bearer": token},
            ) as socket,
            pytest.raises(WebSocketDisconnect),
        ):
            socket.receive_bytes()
    finally:
        _reset_hangups()


@pytest.mark.unit
@pytest.mark.asyncio
async def test_public_socket_sends_an_empty_tool_session(monkeypatch):
    """A live public bearer sends the tool-free session and one PCM chunk."""
    from fastapi import FastAPI
    from starlette.testclient import TestClient

    from app.agentive.api import native_voice
    from app.agentive.services import whatsapp_voice_bridge as bridge
    from app.agentive.services.whatsapp_public_profile import PublicLineGrant

    _reset_hangups()
    native_voice._active_calls.clear()
    token = _public_bearer()
    upstream = _ScriptedUpstream()
    holder: dict = {}
    grant = PublicLineGrant("ws-1", "grant-1", 4, "desk", 2, "Be brief.", "svc-user")

    async def open_fake(_credential):
        holder["loop"] = asyncio.get_running_loop()
        return upstream

    async def credential(_workspace):
        return SimpleNamespace(model="fake-model", api_key="not-logged")

    async def confirmed(_record):
        return grant

    async def quiet_hangup(_call_id, **_kwargs):
        return None

    monkeypatch.setattr(native_voice, "open_upstream", open_fake)
    monkeypatch.setattr(native_voice, "resolve_realtime_credential", credential)
    monkeypatch.setattr(bridge, "confirm_public_call", confirmed)
    monkeypatch.setattr(bridge, "hang_up_phone_call", quiet_hangup)
    api = FastAPI()
    api.include_router(native_voice.router)
    chunk = b"\x00\x01" * 8
    playback = b"\x02\x00"

    def wait_for(predicate) -> None:
        deadline = time.monotonic() + 2
        while time.monotonic() < deadline:
            if predicate():
                return
            time.sleep(0.01)
        raise AssertionError("public relay did not reach the expected event")

    try:
        with (
            TestClient(api) as client,
            client.websocket_connect(
                "/ws/public-phone-voice",
                headers={"x-integral-phone-bearer": token},
            ) as socket,
        ):
            wait_for(
                lambda: any(
                    item.get("type") == "session.update" for item in upstream.sent
                )
            )
            session = next(
                item for item in upstream.sent if item.get("type") == "session.update"
            )
            assert session["session"]["tools"] == []
            assert "signed-in" not in json.dumps(session)
            assert "delegate_to_integral" not in json.dumps(session)
            holder["loop"].call_soon_threadsafe(
                upstream._events.put_nowait,
                {"type": "session.updated", "session": {}},
            )
            wait_for(
                lambda: any(
                    item.get("type") == "response.create" for item in upstream.sent
                )
            )
            greeting = next(
                item for item in upstream.sent if item.get("type") == "response.create"
            )
            assert greeting["response"]["tool_choice"] == "none"
            ready = socket.receive_json()
            assert ready == {"type": "ready"}
            socket.send_bytes(chunk)
            wait_for(
                lambda: any(
                    item.get("type") == "input_audio_buffer.append"
                    for item in upstream.sent
                )
            )
            holder["loop"].call_soon_threadsafe(
                upstream._events.put_nowait,
                {
                    "type": "response.output_audio.delta",
                    "delta": base64.b64encode(playback).decode("ascii"),
                },
            )
            assert socket.receive_bytes() == playback
            socket.close()
        assert native_voice._active_calls == set()
        assert "svc-user" not in json.dumps(upstream.sent)
    finally:
        _reset_hangups()
        native_voice._active_calls.clear()


@pytest.mark.unit
@pytest.mark.asyncio
async def test_caller_audio_does_not_wait_on_the_grant_check():
    """Microphone audio is forwarded while the grant check is still running."""
    from app.agentive.services.whatsapp_public_voice import PublicPhoneRelay

    class _Peer:
        def __init__(self) -> None:
            self._messages: asyncio.Queue = asyncio.Queue()

        async def receive(self):
            return await self._messages.get()

    class _Upstream:
        def __init__(self) -> None:
            self.sent: list[dict] = []

        async def send(self, event: str) -> None:
            self.sent.append(json.loads(event))

    async def stuck():
        await asyncio.Event().wait()

    peer = _Peer()
    upstream = _Upstream()
    relay = PublicPhoneRelay(peer, upstream, "", stuck)
    relay.session_ready = True
    audio = b"\x00\x01" * 20
    await peer._messages.put({"bytes": audio})
    await peer._messages.put({"type": "websocket.disconnect"})
    await asyncio.wait_for(relay._read_peer(), timeout=1)
    assert upstream.sent[0]["type"] == "input_audio_buffer.append"
    assert base64.b64decode(upstream.sent[0]["audio"]) == audio
