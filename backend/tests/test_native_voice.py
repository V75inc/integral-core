import asyncio
import base64
import json

import pytest

from app.services.native_voice import CHECK_CHAT, DELEGATE, VoiceRelay, VoiceScope


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


def request(call_id, response_id, text, *, name=DELEGATE, args=None):
    return {
        "type": "response.done",
        "response": {
            "id": response_id,
            "status": "completed",
            "output": [
                {
                    "type": "function_call",
                    "name": name,
                    "call_id": call_id,
                    "arguments": json.dumps(
                        args if args is not None else {"request": text}
                    ),
                }
            ],
        },
    }


@pytest.mark.smoke
@pytest.mark.asyncio
async def test_voice_relays_audio_and_delegates_unique_completed_call_once():
    browser, provider = Browser(), Provider()
    scope = VoiceScope("caller", "workspace", "profile", "thread")
    calls = []

    async def heavy(request_scope, call_id, text):
        calls.append((request_scope, call_id, text))
        return "You have six contacts. Data: six."

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
    assert [tool["name"] for tool in event_config["session"]["tools"]] == [
        DELEGATE,
        "check_integral_chat",
        "find_integral_voice_capability",
    ]
    await provider.inbox.put({"type": "session.updated"})
    assert await event(browser.outbox) == {"type": "ready", "thread_id": "thread"}
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
    assert output["item"]["output"] == "You have six contacts."
    spoken_result = await event(provider.outbox)
    assert spoken_result["type"] == "response.create"
    assert spoken_result["response"]["tool_choice"] == "none"
    assert "without greeting" in spoken_result["response"]["instructions"]
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
async def test_barge_in_fences_audio_without_cancelling_delegate(hang_up):
    browser, provider = Browser(), Provider()
    started = asyncio.Event()
    cancelled = asyncio.Event()
    finish = asyncio.Event()
    observed = asyncio.Event()

    async def uncooperative_fixture(_scope, _call_id, _text):
        started.set()
        try:
            await finish.wait()
            observed.set()
            return "Old delayed result"
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
    assert await event(browser.outbox) == {"type": "ready", "thread_id": "thread"}
    await provider.inbox.put({"type": "response.created", "response": {"id": "r1"}})
    await provider.inbox.put(request("lookup-1", "r1", "Read Cedar-981"))
    await asyncio.wait_for(started.wait(), 2)
    if not hang_up:
        await provider.inbox.put({"type": "input_audio_buffer.speech_started"})
        assert await event(browser.outbox) == {"type": "flush"}
        assert not cancelled.is_set()
        finish.set()
        await asyncio.wait_for(observed.wait(), 2)
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
    assert cancelled.is_set() == hang_up
    if not hang_up:
        closure = await event(provider.outbox)
        assert closure["item"]["call_id"] == "lookup-1"
        assert "Old delayed result" not in closure["item"]["output"]
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


@pytest.mark.smoke
@pytest.mark.asyncio
async def test_delegate_returns_approval_notice_without_cancelling_parked_work(
    monkeypatch,
):
    from types import SimpleNamespace
    from unittest.mock import AsyncMock

    from app.agentive.services import work_items
    from app.agentive.work_models import WorkItem
    from app.services import chat_turn_submissions, native_voice

    scope = VoiceScope("caller", "workspace", "", "thread")
    monkeypatch.setattr(
        native_voice, "authorize", AsyncMock(return_value=SimpleNamespace(id="thread"))
    )
    monkeypatch.setattr(
        chat_turn_submissions,
        "submit_chat_turn",
        AsyncMock(
            return_value=SimpleNamespace(work_item_id="work", message_id="accepted")
        ),
    )
    monkeypatch.setattr(
        WorkItem,
        "get",
        AsyncMock(
            return_value=SimpleNamespace(
                principal_id="caller",
                workspace_id="workspace",
                thread_id="thread",
                status="waiting_for_human",
            )
        ),
    )
    cancel = AsyncMock()
    monkeypatch.setattr(work_items, "cancel_work_item", cancel)
    result = await native_voice.delegate(scope, "call", "Prepare a change for review")
    assert "needs approval" in result
    cancel.assert_not_awaited()


@pytest.mark.asyncio
async def test_voice_cancellation_preserves_human_review(monkeypatch):
    from types import SimpleNamespace
    from unittest.mock import AsyncMock

    from app.agentive.services import work_items, work_outbox

    item = SimpleNamespace(status="waiting_for_human")
    monkeypatch.setattr(work_items, "_current_work_item", AsyncMock(return_value=item))
    update = AsyncMock()
    monkeypatch.setattr(work_outbox, "cas_work_item_update", update)
    assert await work_items.cancel_work_item("work", preserve_human_review=True) is item
    update.assert_not_awaited()


@pytest.mark.asyncio
async def test_delegate_disconnect_does_not_cancel_durable_work(monkeypatch):
    from types import SimpleNamespace
    from unittest.mock import AsyncMock

    from app.agentive.services import work_items
    from app.agentive.work_models import WorkItem
    from app.services import chat_turn_submissions, native_voice

    scope = VoiceScope("caller", "workspace", "", "thread")
    monkeypatch.setattr(
        native_voice, "authorize", AsyncMock(return_value=SimpleNamespace(id="thread"))
    )
    monkeypatch.setattr(
        chat_turn_submissions,
        "submit_chat_turn",
        AsyncMock(
            return_value=SimpleNamespace(work_item_id="work", message_id="accepted")
        ),
    )
    monkeypatch.setattr(
        WorkItem, "get", AsyncMock(side_effect=asyncio.CancelledError())
    )
    cancel = AsyncMock()
    monkeypatch.setattr(work_items, "cancel_work_item", cancel)
    with pytest.raises(asyncio.CancelledError):
        await native_voice.delegate(scope, "call", "Read the current status")
    cancel.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("interrupted", [True, False])
async def test_tagged_opening_cannot_claim_or_replace_ordinary_response(
    monkeypatch, interrupted
):
    from app.services import native_voice as service

    monkeypatch.setattr(service, "OPENING_GRACE_SECONDS", 0)
    browser, provider = Browser(), Provider()
    relay = VoiceRelay(
        browser,
        provider,
        VoiceScope("caller", "workspace", "", "thread"),
        check=authorize,
    )
    call = asyncio.create_task(relay.run())
    try:
        await event(provider.outbox)
        await provider.inbox.put({"type": "session.updated"})
        await event(browser.outbox)
        await browser.inbox.put(
            {"type": "websocket.receive", "text": '{"type":"opening"}'}
        )
        opening = (await event(provider.outbox))["response"]
        assert opening["conversation"] == "none"
        if interrupted:
            await provider.inbox.put({"type": "input_audio_buffer.speech_started"})
            assert await event(browser.outbox) == {"type": "flush"}
            await provider.inbox.put(
                {"type": "response.created", "response": {"id": "normal"}}
            )
        await provider.inbox.put(
            {
                "type": "response.created",
                "response": {
                    "id": "greeting",
                    "conversation_id": None,
                    "metadata": opening["metadata"],
                },
            }
        )
        await provider.inbox.put(
            {
                "type": "response.output_audio.delta",
                "response_id": "greeting",
                "delta": "AAE=",
            }
        )
        if interrupted:
            await provider.inbox.put(
                {
                    "type": "response.output_audio.delta",
                    "response_id": "normal",
                    "delta": "AAI=",
                }
            )
            assert await event(browser.outbox) == b"\x00\x02"
        else:
            assert await event(browser.outbox) == b"\x00\x01"
        assert browser.outbox.empty()
    finally:
        await browser.inbox.put({"type": "websocket.disconnect"})
        await call


@pytest.mark.asyncio
@pytest.mark.parametrize("speech_first", [True, False])
async def test_opening_is_one_shot_and_yields_to_speech(monkeypatch, speech_first):
    from app.services import native_voice

    monkeypatch.setattr(native_voice, "OPENING_GRACE_SECONDS", 0.01)
    browser, provider = Browser(), Provider()
    relay = VoiceRelay(
        browser,
        provider,
        VoiceScope("caller", "workspace", "", "thread"),
        check=authorize,
    )
    task = asyncio.create_task(
        relay.run("A helpful persona.", name="Nia", continuity="new")
    )
    try:
        assert (await event(provider.outbox))["type"] == "session.update"
        await provider.inbox.put({"type": "session.updated"})
        assert await event(browser.outbox) == {"type": "ready", "thread_id": "thread"}
        opening = {"type": "websocket.receive", "text": '{"type":"opening"}'}
        await browser.inbox.put(opening)
        await browser.inbox.put(opening)
        if speech_first:
            await provider.inbox.put({"type": "input_audio_buffer.speech_started"})
            assert await event(browser.outbox) == {"type": "flush"}
        else:
            response = await event(provider.outbox)
            assert response["response"]["tool_choice"] == "none"
            assert (
                'introduce yourself once as "Nia"'
                in response["response"]["instructions"]
            )
        with pytest.raises(TimeoutError):
            await asyncio.wait_for(provider.outbox.get(), 0.05)
    finally:
        await browser.inbox.put({"type": "websocket.disconnect"})
        await task


def test_authorized_thread_continuity_uses_only_activity_recency():
    from datetime import datetime, timezone
    from types import SimpleNamespace

    from app.services.native_voice import voice_thread_continuity

    now = datetime(2026, 11, 22, 2, tzinfo=timezone.utc)
    assert voice_thread_continuity(None, now=now) == "new"
    recent = SimpleNamespace(last_message_at="2026-11-22T01:40:00+00:00")
    old = SimpleNamespace(last_message_at="2026-11-22T01:00:00+00:00")
    not_metadata = SimpleNamespace(last_message_at="wrong")
    assert voice_thread_continuity(recent, now=now) == "recent"
    assert voice_thread_continuity(old, now=now) == "older"
    assert voice_thread_continuity(not_metadata, now=now) == "unknown"


@pytest.mark.asyncio
@pytest.mark.parametrize("provider_ready", [True, False])
async def test_voice_first_creates_one_scoped_thread_only_on_provider_ready(
    monkeypatch, provider_ready
):
    from types import SimpleNamespace
    from unittest.mock import AsyncMock

    from app.api import ai_chat
    from app.services import chat_threads
    from app.services.chat_providers import registry

    monkeypatch.setattr(
        ai_chat,
        "_resolve_agent_for_new_thread",
        AsyncMock(return_value="catalog-agent"),
    )
    monkeypatch.setattr(
        registry,
        "get_registry",
        lambda: SimpleNamespace(get=lambda _: "native-provider"),
    )
    create = AsyncMock(return_value=SimpleNamespace(id="voice-first"))
    monkeypatch.setattr(chat_threads, "create_thread", create)
    browser, provider = Browser(), Provider()
    scope = VoiceScope("caller", "workspace", "profile", "")
    work = AsyncMock(return_value="Done.")
    relay = VoiceRelay(browser, provider, scope, check=AsyncMock(), work=work)
    task = asyncio.create_task(relay.run())
    try:
        await event(provider.outbox)
        create.assert_not_awaited()
        if provider_ready:
            await provider.inbox.put({"type": "session.updated"})
            assert await event(browser.outbox) == {
                "type": "ready",
                "thread_id": "voice-first",
            }
            await provider.inbox.put({"type": "session.updated"})
            await provider.inbox.put(
                {"type": "response.created", "response": {"id": "response"}}
            )
            await provider.inbox.put(request("tool", "response", "Read a record"))
            await event(provider.outbox)
            work.assert_awaited_once_with(scope, "tool", "Read a record")
            assert scope.thread == "voice-first"
            create.assert_awaited_once_with(
                user_id="caller",
                workspace_id="workspace",
                provider_id="integral_native",
                agent_id="catalog-agent",
                agent_profile_key="profile",
            )
            assert browser.outbox.empty()
        else:
            assert scope.thread == ""
            create.assert_not_awaited()
    finally:
        await browser.inbox.put({"type": "websocket.disconnect"})
        await task


@pytest.mark.parametrize(
    ("result", "spoken"),
    [
        ("You have **6** contacts.\n**Data:** 6.", "You have **6** contacts."),
        ("You have six contacts. Data: 6.", "You have six contacts."),
        (
            "You have six contacts. Data: two archived.",
            "You have six contacts. Data: two archived.",
        ),
        ("You have six contacts. Data: 5.", "You have six contacts. Data: 5."),
        (
            "You have six contacts, two inactive. Result: 6.",
            "You have six contacts, two inactive. Result: 6.",
        ),
        ("Data: six", "Data: six"),
    ],
)
def test_speech_projection_preserves_new_details_and_written_answer(result, spoken):
    from app.services.native_voice import _speech_ready_result

    assert _speech_ready_result(result) == spoken


@pytest.mark.asyncio
@pytest.mark.parametrize("accepted", [False, True])
async def test_delegate_observer_timeout_never_cancels_and_only_reports_known_receipt(
    monkeypatch, accepted
):
    from types import SimpleNamespace
    from unittest.mock import AsyncMock

    from app.agentive.services import work_items
    from app.services import chat_turn_submissions, native_voice

    receipt = SimpleNamespace(work_item_id="work", message_id="message")
    calls = []
    cancel = AsyncMock()
    monkeypatch.setattr(work_items, "cancel_work_item", cancel)
    monkeypatch.setattr(
        native_voice, "authorize", AsyncMock(return_value=SimpleNamespace(id="thread"))
    )
    monkeypatch.setattr(native_voice, "DELEGATE_WAIT_SECONDS", 0.02)

    async def submit(query):
        calls.append(query)
        if accepted:
            return receipt
        await asyncio.Future()

    async def observe(*_args):
        await asyncio.Future()

    monkeypatch.setattr(chat_turn_submissions, "submit_chat_turn", submit)
    monkeypatch.setattr(native_voice, "_delegated_status", observe)
    scope = VoiceScope("caller", "workspace", "", "thread")
    answer = await native_voice.delegate(scope, "id", "Explain Integral")
    assert ("was accepted" in answer) == accepted
    assert ("couldn't confirm" in answer) != accepted
    assert len(calls) == 1 and calls[0].thread_id == "thread"
    assert calls[0].client_request_id.startswith("voice.")
    cancel.assert_not_awaited()


@pytest.mark.asyncio
async def test_late_tagged_delegate_audio_cannot_replace_next_response():
    browser, provider = Browser(), Provider()
    work_calls = []

    async def answer(_scope, call_id, text):
        work_calls.append((call_id, text))
        return "Finished exact durable request"

    relay = VoiceRelay(
        browser,
        provider,
        VoiceScope("caller", "workspace", "", "thread"),
        check=authorize,
        work=answer,
    )
    call = asyncio.create_task(relay.run())
    try:
        await event(provider.outbox)
        await provider.inbox.put({"type": "session.updated"})
        await event(browser.outbox)
        await provider.inbox.put(
            {"type": "response.created", "response": {"id": "first"}}
        )
        await provider.inbox.put(request("id", "first", "Explain Integral"))
        await event(provider.outbox)  # function_call_output
        delivery = await event(provider.outbox)
        marker = delivery["response"]["metadata"]
        await provider.inbox.put({"type": "input_audio_buffer.speech_started"})
        assert await event(browser.outbox) == {"type": "flush"}
        await provider.inbox.put(
            {"type": "response.created", "response": {"id": "new"}}
        )
        await provider.inbox.put(
            {"type": "response.created", "response": {"id": "late", "metadata": marker}}
        )
        await provider.inbox.put(
            {
                "type": "response.output_audio.delta",
                "response_id": "late",
                "delta": "AAE=",
            }
        )
        await provider.inbox.put(
            {
                "type": "response.output_audio.delta",
                "response_id": "new",
                "delta": "AAI=",
            }
        )
        assert await event(browser.outbox) == b"\x00\x02"
        assert browser.outbox.empty()
        assert work_calls == [("id", "Explain Integral")]
    finally:
        await browser.inbox.put({"type": "websocket.disconnect"})
        await call


@pytest.mark.asyncio
async def test_vad_during_acceptance_keeps_exact_same_thread_submission(monkeypatch):
    from types import SimpleNamespace
    from unittest.mock import AsyncMock

    from app.agentive.services import work_items
    from app.services import chat_turn_submissions, native_voice

    browser, provider = Browser(), Provider()
    started, release = asyncio.Event(), asyncio.Event()
    calls = []
    receipt = SimpleNamespace(work_item_id="work", message_id="accepted")
    cancel = AsyncMock()
    monkeypatch.setattr(work_items, "cancel_work_item", cancel)
    monkeypatch.setattr(
        native_voice, "authorize", AsyncMock(return_value=SimpleNamespace(id="thread"))
    )
    status = AsyncMock(return_value=("succeeded", "Same-thread result"))
    monkeypatch.setattr(native_voice, "_delegated_status", status)

    async def submit(submission):
        calls.append(submission)
        started.set()
        await release.wait()
        return receipt

    monkeypatch.setattr(chat_turn_submissions, "submit_chat_turn", submit)
    scope = VoiceScope("caller", "workspace", "", "thread")
    relay = VoiceRelay(browser, provider, scope, check=authorize)
    call = asyncio.create_task(relay.run())
    try:
        await event(provider.outbox)
        await provider.inbox.put({"type": "session.updated"})
        await event(browser.outbox)
        await provider.inbox.put(
            {"type": "response.created", "response": {"id": "first"}}
        )
        await provider.inbox.put(request("id", "first", "Explain Integral"))
        await asyncio.wait_for(started.wait(), 2)
        await provider.inbox.put({"type": "input_audio_buffer.speech_started"})
        assert await event(browser.outbox) == {"type": "flush"}
        release.set()
        await asyncio.wait_for(asyncio.shield(relay.pending), 2)
        # Replayed provider event is not another durable submission.
        await provider.inbox.put(
            {"type": "response.created", "response": {"id": "duplicate"}}
        )
        await provider.inbox.put(request("id", "duplicate", "Explain Integral"))
        await provider.inbox.put({"type": "input_audio_buffer.speech_started"})
        assert await event(browser.outbox) == {"type": "flush"}
        closure = await event(provider.outbox)
        assert closure["item"]["call_id"] == "id"
        assert "Same-thread result" not in closure["item"]["output"]
        assert provider.outbox.empty()
        assert len(calls) == 1
        assert (calls[0].thread_id, calls[0].principal_id, calls[0].workspace_id) == (
            "thread",
            "caller",
            "workspace",
        )
        status.assert_awaited_once()
        assert status.await_args.args[2] is receipt
        cancel.assert_not_awaited()
    finally:
        release.set()
        await browser.inbox.put({"type": "websocket.disconnect"})
        await call


@pytest.mark.smoke
@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("question", "view", "role"),
    [
        ("Something popped up in the chat there.", "latest_chat", "assistant"),
        ("What did I just type in this chat?", "latest_typed", "user"),
        ("What did chat just reply in this conversation?", "latest_reply", "assistant"),
        ("Something popped up in the chat there.", "latest_chat", None),
    ],
)
async def test_spoken_chat_lookup_reads_current_thread_without_submitting(
    monkeypatch, question, view, role
):
    """Run the advertised read tool against real scoped chat with a fake provider."""
    from unittest.mock import AsyncMock

    from app.api.errors import InsufficientPermissionsError
    from app.services import chat_turn_submissions, native_voice
    from app.services.chat_threads import append_message, create_thread
    from app.services.native_voice_thread_status import VoiceThreadStatus
    from tests.test_access_perimeter_regressions import rooted_graph

    owner, outsider, workspace, _, _, _ = await rooted_graph()
    current = await create_thread(
        user_id=owner.id,
        workspace_id=workspace.id,
        provider_id="integral_native",
        agent_profile_key="guide",
    )
    private = await create_thread(
        user_id=outsider.id,
        workspace_id=workspace.id,
        provider_id="integral_native",
        agent_profile_key="guide",
    )
    same = {
        "user": "Cedar meeting at noon.",
        "assistant": "I can summarize Cedar. " + "A" * 500 + " NOT_IN_SPOKEN_EXCERPT",
    }
    other = {"user": "Private Alder question.", "assistant": "Private Alder answer."}
    for side in ("user", "assistant"):
        await append_message(
            thread=private, role=side, parts=[{"type": "text", "text": other[side]}]
        )
    await append_message(
        thread=current,
        role="assistant",
        parts=[
            {"type": "reasoning", "text": "HIDDEN_REASONING"},
            {"type": "file", "data": "HIDDEN_ATTACHMENT"},
        ],
    )
    await append_message(
        thread=current,
        role="system",
        parts=[{"type": "text", "text": "HIDDEN_SYSTEM"}],
    )

    require = AsyncMock()
    monkeypatch.setattr(native_voice, "require_native_chat_profile", require)
    scope = VoiceScope(owner.id, workspace.id, "guide", current.id)
    with pytest.raises(InsufficientPermissionsError):
        await native_voice.authorize(
            VoiceScope(owner.id, workspace.id, "guide", private.id)
        )
    submit = AsyncMock()
    monkeypatch.setattr(chat_turn_submissions, "submit_chat_turn", submit)

    browser, provider = Browser(), Provider()
    relay = VoiceRelay(
        browser,
        provider,
        scope,
        thread_status=AsyncMock(
            return_value=(VoiceThreadStatus(typed_turn="finished"), ())
        ),
    )
    task = asyncio.create_task(relay.run())
    try:
        config = await event(provider.outbox)
        instructions = config["session"]["instructions"]
        assert question.lower().rstrip(".") in instructions or view != "latest_chat"
        chat_tool = next(
            t for t in config["session"]["tools"] if t["name"] == CHECK_CHAT
        )
        assert view in chat_tool["parameters"]["properties"]["view"]["enum"]
        await provider.inbox.put({"type": "session.updated"})
        assert (await event(browser.outbox))["thread_id"] == current.id
        await relay._sync_thread_status()
        quiet = await event(provider.outbox)
        assert quiet["type"] == "conversation.item.create"
        assert quiet["item"]["role"] == "system"
        assert "Cedar" not in json.dumps([config, quiet])
        assert "Alder" not in json.dumps([config, quiet])
        if role is not None:
            # The chat update appears after voice joins, not in opening context.
            for side in ("user", "assistant"):
                await append_message(
                    thread=current,
                    role=side,
                    parts=[{"type": "text", "text": same[side]}],
                )
        # Status ownership stays independent; this read must work while Heavy runs.
        relay.pending = asyncio.create_task(asyncio.Event().wait())

        await provider.inbox.put(
            {"type": "response.created", "response": {"id": "lookup"}}
        )
        await provider.inbox.put(
            request(
                "read-tool", "lookup", question, name=CHECK_CHAT, args={"view": view}
            )
        )
        reply = await event(provider.outbox)
        assert reply["item"]["output"] == "Integral checked your request."
        spoken = await event(provider.outbox)
        assert spoken["type"] == "response.create"
        assert spoken["response"]["conversation"] == "none"
        spoken_text = spoken["response"]["instructions"]
        if role is None:
            assert "couldn't find a recent saved message" in spoken_text
            assert "can't see unsaved text or interface popups" in spoken_text
        else:
            assert same[role][:35].strip() in spoken_text
            if view == "latest_chat":
                assert "can't see the popup itself" in spoken_text
        for excluded in (*other.values(), "HIDDEN_", "NOT_IN_SPOKEN_EXCERPT"):
            assert excluded not in spoken_text
        submit.assert_not_awaited()
        assert not relay.pending.done()
        require.assert_awaited_with(workspace.id, owner.id, "guide")
    finally:
        await browser.inbox.put({"type": "websocket.disconnect"})
        await task
