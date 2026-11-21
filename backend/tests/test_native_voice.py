import asyncio
import base64
import json

import pytest

from app.services.native_voice import DELEGATE, VoiceRelay, VoiceScope


class Browser:
    def __init__(self):
        self.inbox = asyncio.Queue()
        self.outbox = asyncio.Queue()

    async def receive(self):
        return await self.inbox.get()

    async def send_json(self, message):
        await self.outbox.put(message)

    async def send_bytes(self, message):
        await self.outbox.put(message)


class Provider:
    def __init__(self):
        self.inbox = asyncio.Queue()
        self.outbox = asyncio.Queue()

    def __aiter__(self):
        return self

    async def __anext__(self):
        return json.dumps(await self.inbox.get())

    async def send(self, event):
        await self.outbox.put(json.loads(event))


async def event(queue):
    return await asyncio.wait_for(queue.get(), 2)


async def authorize(_scope):
    pass


def request(call_id, response_id, text):
    return {
        "type": "response.done",
        "response": {
            "id": response_id,
            "status": "completed",
            "output": [
                {
                    "type": "function_call",
                    "name": DELEGATE,
                    "call_id": call_id,
                    "arguments": json.dumps({"request": text}),
                }
            ],
        },
    }


@pytest.mark.asyncio
async def test_voice_relays_audio_and_delegates_unique_completed_call_once():
    browser, provider = Browser(), Provider()
    scope = VoiceScope("caller", "workspace", "profile", "thread")
    calls = []

    async def heavy(request_scope, call_id, text):
        calls.append((request_scope, call_id, text))
        return "Cedar fixture found."

    relay = VoiceRelay(browser, provider, scope, check=authorize, work=heavy)
    task = asyncio.create_task(
        relay.run("Explain payroll plainly.", name="Payroll Guide")
    )
    event_config = await event(provider.outbox)
    instructions = event_config["session"]["instructions"]
    assert 'Your assistant name is "Payroll Guide"' in instructions
    assert "Explain payroll plainly." in instructions
    assert "do not accept approval by voice" in instructions
    assert (
        event_config["session"]["audio"]["input"]["turn_detection"][
            "interrupt_response"
        ]
        is True
    )
    assert [tool["name"] for tool in event_config["session"]["tools"]] == [DELEGATE]
    await provider.inbox.put({"type": "session.updated"})
    assert await event(browser.outbox) == {"type": "ready"}
    await browser.inbox.put({"type": "websocket.receive", "bytes": b"\x00\x01"})
    assert await event(provider.outbox) == {
        "type": "input_audio_buffer.append",
        "audio": "AAE=",
    }
    await provider.inbox.put({"type": "response.created", "response": {"id": "r1"}})
    unique = "Read fixture Cedar-981 once."
    completed = request("lookup-1", "r1", unique)
    await provider.inbox.put(completed)
    await provider.inbox.put(completed)
    output = await event(provider.outbox)
    assert output["item"]["call_id"] == "lookup-1"
    assert json.loads(output["item"]["output"]) == {"result": "Cedar fixture found."}
    assert await event(provider.outbox) == {"type": "response.create"}
    await provider.inbox.put({"type": "response.created", "response": {"id": "r2"}})
    audio = b"\x01\x00\x03\x00"
    await provider.inbox.put(
        {
            "type": "response.output_audio.delta",
            "response_id": "r2",
            "delta": base64.b64encode(audio).decode(),
        }
    )
    assert await event(browser.outbox) == audio
    await browser.inbox.put({"type": "websocket.disconnect"})
    await asyncio.wait_for(task, 2)
    assert calls == [(scope, "lookup-1", unique)]
    assert provider.outbox.empty()


@pytest.mark.asyncio
@pytest.mark.parametrize("hang_up", [False, True])
async def test_delayed_work_and_audio_do_not_survive_interrupt_or_hangup(hang_up):
    browser, provider = Browser(), Provider()
    started = asyncio.Event()
    cancelled = asyncio.Event()

    async def uncooperative_fixture(_scope, _call_id, _text):
        started.set()
        try:
            await asyncio.Future()
        except asyncio.CancelledError:
            cancelled.set()
            return "Old delayed result"

    scope = VoiceScope("caller", "workspace", "profile", "thread")
    relay = VoiceRelay(
        browser, provider, scope, check=authorize, work=uncooperative_fixture
    )
    task = asyncio.create_task(relay.run())
    await event(provider.outbox)
    await provider.inbox.put({"type": "session.updated"})
    assert await event(browser.outbox) == {"type": "ready"}
    await provider.inbox.put({"type": "response.created", "response": {"id": "r1"}})
    await provider.inbox.put(request("lookup-1", "r1", "Read Cedar-981"))
    await asyncio.wait_for(started.wait(), 2)
    if not hang_up:
        await provider.inbox.put({"type": "input_audio_buffer.speech_started"})
        assert await event(browser.outbox) == {"type": "flush"}
        await asyncio.wait_for(cancelled.wait(), 2)
        await provider.inbox.put(
            {
                "type": "response.output_audio.delta",
                "response_id": "r1",
                "delta": "AAE=",
            }
        )
        await provider.inbox.put({"type": "response.created", "response": {"id": "r2"}})
        await provider.inbox.put(
            {
                "type": "response.output_audio.delta",
                "response_id": "r2",
                "delta": "AAA=",
            }
        )
        assert await event(browser.outbox) == b"\x00\x00"
    await browser.inbox.put({"type": "websocket.disconnect"})
    await asyncio.wait_for(task, 2)
    assert cancelled.is_set()
    assert provider.outbox.empty()


def test_voice_socket_denies_wrong_origin_and_does_not_open_without_configuration(
    monkeypatch,
):
    from unittest.mock import AsyncMock

    from fastapi import FastAPI
    from starlette.testclient import TestClient
    from starlette.websockets import WebSocketDisconnect

    from app.agentive.api import native_voice

    api = FastAPI()
    api.include_router(native_voice.router)
    monkeypatch.setattr(
        native_voice.settings, "FRONTEND_ORIGIN", "http://127.0.0.1:9150"
    )
    monkeypatch.setattr(
        native_voice.settings, "INTEGRAL_NATIVE_DURABLE_CHAT_ENABLED", True
    )
    provider = AsyncMock()
    monkeypatch.setattr(native_voice, "open_upstream", provider)
    redeem = AsyncMock(return_value="fixture-user")
    monkeypatch.setattr(native_voice, "redeem_ws_ticket_shared", redeem)
    monkeypatch.setattr(native_voice, "get_user_node", AsyncMock(return_value=object()))
    monkeypatch.setattr(native_voice, "authorize", AsyncMock())
    monkeypatch.setattr(
        native_voice, "resolve_realtime_credential", AsyncMock(return_value=None)
    )

    client = TestClient(api)
    with pytest.raises(WebSocketDisconnect):
        with client.websocket_connect(
            "/ws/native-voice", headers={"Origin": "https://other.example"}
        ):
            pass
    redeem.assert_not_awaited()
    with client.websocket_connect(
        "/ws/native-voice", headers={"Origin": "http://127.0.0.1:9150"}
    ) as socket:
        socket.send_json({"ticket": "x" * 43, "workspace_id": "fixture-workspace"})
        assert socket.receive_json() == {
            "type": "error",
            "message": "Configure a live voice model in AI Models.",
        }
        with pytest.raises(WebSocketDisconnect) as closed:
            socket.receive_json()
        assert closed.value.code == 4404
    provider.assert_not_awaited()


@pytest.mark.asyncio
async def test_delegate_submits_the_native_worker_with_heavy_route_and_pinned_thread(
    monkeypatch,
):
    from types import SimpleNamespace
    from unittest.mock import AsyncMock

    from app.agentive.work_models import WorkItem
    from app.models.nodes import ChatMessage
    from app.services import chat_turn_submissions, native_voice
    from app.services.chat_turn_transcript import _message_id

    scope = VoiceScope("caller", "workspace", "profile", "thread")
    graph = SimpleNamespace(find_edges_between=AsyncMock(return_value=[object()]))
    thread = SimpleNamespace(id=scope.thread, get_context=AsyncMock(return_value=graph))
    monkeypatch.setattr(native_voice, "authorize", AsyncMock(return_value=thread))
    receipt = SimpleNamespace(work_item_id="worker-run", message_id="accepted")
    submit = AsyncMock(return_value=receipt)
    monkeypatch.setattr(chat_turn_submissions, "submit_chat_turn", submit)
    monkeypatch.setattr(
        WorkItem,
        "get",
        AsyncMock(
            return_value=SimpleNamespace(
                principal_id=scope.principal,
                workspace_id=scope.workspace,
                thread_id=scope.thread,
                status="succeeded",
            )
        ),
    )
    message_get = AsyncMock(
        return_value=SimpleNamespace(
            id=_message_id(receipt.work_item_id),
            thread_id=scope.thread,
            role="assistant",
            parent_id=receipt.message_id,
            parts=[
                {"type": "text", "text": "Cedar exists."},
                {"type": "tool-result", "text": "internal"},
            ],
        )
    )
    monkeypatch.setattr(ChatMessage, "get", message_get)
    assert (
        await native_voice.delegate(scope, "provider-call", "Read Cedar-981")
        == "Cedar exists."
    )
    command = submit.await_args.args[0]
    assert (command.principal_id, command.workspace_id, command.thread_id) == (
        "caller",
        "workspace",
        "thread",
    )
    assert command.execution_context.extra_data == {"voice_delegate": True}
    assert command.execution_context.host_control is None
    assert command.client_request_id.startswith("voice.")
    assert command.parts == [{"type": "text", "text": "Read Cedar-981"}]
    message_get.assert_awaited_once_with(_message_id(receipt.work_item_id))


@pytest.mark.asyncio
async def test_voice_authority_refuses_threads_outside_the_current_profile(monkeypatch):
    from types import SimpleNamespace
    from unittest.mock import AsyncMock

    from app.api.errors import InsufficientPermissionsError
    from app.models.nodes import ChatThread
    from app.services import native_voice

    monkeypatch.setattr(
        native_voice, "can_access_workspace", AsyncMock(return_value="member")
    )
    check_profile = AsyncMock()
    monkeypatch.setattr(native_voice, "require_native_chat_profile", check_profile)
    thread = SimpleNamespace(
        archived=False,
        provider_id="integral_native",
        provider_session_id="",
        user_id="caller",
        workspace_id="workspace",
        agent_profile_key="other-profile",
    )
    monkeypatch.setattr(ChatThread, "get", AsyncMock(return_value=thread))
    with pytest.raises(InsufficientPermissionsError):
        await native_voice.authorize(
            VoiceScope("caller", "workspace", "profile", "thread")
        )
    check_profile.assert_awaited_once_with("workspace", "caller", "profile")
