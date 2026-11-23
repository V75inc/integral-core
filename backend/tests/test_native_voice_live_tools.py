"""Provider protocol checks for explicit live voice tools."""

import asyncio
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.services import (
    chat_turn_submissions,
    native_voice,
    native_voice_capabilities,
    native_voice_followups,
)
from tests.test_native_voice import Browser, Provider, event, request


async def _tool(provider, identifier, name, args):
    await provider.inbox.put(
        {"type": "response.created", "response": {"id": identifier}}
    )
    frame = request(identifier, identifier, "ignored")
    item = frame["response"]["output"][0]
    item.update(name=name, arguments=json.dumps(args))
    await provider.inbox.put(frame)


async def _negotiate(provider, identifier, query, title):
    await _tool(provider, identifier, native_voice.FIND_CAPABILITY, {"query": query})
    assert "searched" in (await event(provider.outbox))["item"]["output"]
    selection = (await event(provider.outbox))["response"]
    assert selection["conversation"] == "none"
    assert selection["output_modalities"] == ["text"]
    assert selection["tool_choice"] == {
        "type": "function",
        "name": native_voice.INVOKE_CAPABILITY,
    }
    assert [row["name"] for row in selection["tools"]] == [
        native_voice.INVOKE_CAPABILITY
    ]
    payload = json.loads(
        selection["instructions"][selection["instructions"].index("{") :]
    )
    [key] = [row["offer"] for row in payload["offers"] if row["title"] == title]
    return selection, key


async def _choose(provider, identifier, selection, offer):
    response_id = f"{identifier}-selection"
    await provider.inbox.put(
        {
            "type": "response.created",
            "response": {"id": response_id, "metadata": selection["metadata"]},
        }
    )
    frame = request(identifier, response_id, "ignored")
    item = frame["response"]["output"][0]
    item.update(
        name=native_voice.INVOKE_CAPABILITY, arguments=json.dumps({"offer": offer})
    )
    frame["response"]["output"].insert(
        0, {"type": "message", "role": "assistant", "content": []}
    )
    await provider.inbox.put(frame)


async def _quick_read(provider, identifier, query, title):
    selection, offer = await _negotiate(provider, identifier, query, title)
    await _choose(provider, identifier, selection, offer)
    speech = (await event(provider.outbox))["response"]
    assert speech["tool_choice"] == "none" and speech["conversation"] == "none"
    return speech["instructions"]


@pytest.mark.asyncio
async def test_named_live_tools_work_without_interrupting_accepted_turn(monkeypatch):
    """Explicit reads and follow-ups have separate paths while Heavy is running."""
    browser, provider = Browser(), Provider()
    scope = native_voice.VoiceScope("caller", "workspace", "", "thread")
    thread = SimpleNamespace(id=scope.thread)
    check = AsyncMock(return_value=thread)
    accepted = SimpleNamespace(work_item_id="job", message_id="message")
    submit = AsyncMock(return_value=accepted)
    monkeypatch.setattr(native_voice, "authorize", check)
    monkeypatch.setattr(
        native_voice, "_delegated_status", AsyncMock(return_value=("running", None))
    )
    monkeypatch.setattr(chat_turn_submissions, "submit_chat_turn", submit)
    from app.agentive.tooling import dispatch

    direct = AsyncMock(
        side_effect=[
            dispatch.ToolResult(
                data={"apps": [{"name": "Visible Projects", "id": "hidden-app-id"}]}
            ),
            dispatch.ToolResult(
                data={
                    "workspaces": [
                        {"name": "Operations", "id": scope.workspace},
                        {"name": "Allowed Workspace", "id": "other-allowed"},
                    ]
                }
            ),
        ]
    )
    monkeypatch.setattr(dispatch, "dispatch_tool", direct)
    target = SimpleNamespace(fence=SimpleNamespace(work_item_id="job"))
    monkeypatch.setattr(
        native_voice_followups,
        "resolve_voice_follow_up_target",
        AsyncMock(return_value=target),
    )
    follow_up = AsyncMock(
        side_effect=[
            SimpleNamespace(status="queued", client_request_id="queue-receipt"),
            SimpleNamespace(status="steering_accepted"),
        ]
    )
    monkeypatch.setattr(native_voice_followups, "route_voice_follow_up", follow_up)

    relay = native_voice.VoiceRelay(browser, provider, scope, check=check)
    task = asyncio.create_task(relay.run())
    try:
        registered = (await event(provider.outbox))["session"]["tools"]
        assert {tool["name"] for tool in registered} == {
            native_voice.DELEGATE,
            native_voice.CHECK_CHAT,
            native_voice.FIND_CAPABILITY,
            native_voice.QUEUE_FOLLOW_UP,
            native_voice.STEER_ACTIVE,
        }
        await provider.inbox.put({"type": "session.updated"})
        assert (await event(browser.outbox))["thread_id"] == scope.thread
        await _tool(
            provider, "begin", native_voice.DELEGATE, {"request": "Draft the report."}
        )
        assert "accepted" in (await event(provider.outbox))["item"]["output"]
        assert not relay.pending.done()

        await _tool(
            provider, "status", native_voice.CHECK_CHAT, {"view": "current_task"}
        )
        assert "checked" in (await event(provider.outbox))["item"]["output"]
        speech = (await event(provider.outbox))["response"]
        assert "running: Draft the report." in speech["instructions"]
        assert speech["conversation"] == "none" and speech["tool_choice"] == "none"

        speech = await _quick_read(provider, "apps", "show apps", "Visible apps here")
        assert "Visible Projects" in speech and "hidden-app-id" not in speech
        speech = await _quick_read(
            provider, "workspaces", "list workspaces", "Accessible workspaces"
        )
        assert "Operations" in speech and "Allowed Workspace" in speech
        assert [item.args[0] for item in direct.await_args_list] == [
            "integral_list_apps",
            "integral_list_workspaces",
        ]
        assert all(
            item.kwargs["principal_id"] == scope.principal
            and item.kwargs["scope"] == scope.workspace
            and item.args[1] == {}
            for item in direct.await_args_list
        )

        await _tool(
            provider,
            "next",
            native_voice.QUEUE_FOLLOW_UP,
            {"request": "Then summarize the projects."},
        )
        assert (
            "queued that as a separate"
            in (await event(provider.outbox))["item"]["output"]
        )
        await event(provider.outbox)
        await _tool(
            provider, "foreground", native_voice.CHECK_CHAT, {"view": "current_task"}
        )
        await event(provider.outbox)
        assert (
            "running: Draft the report."
            in (await event(provider.outbox))["response"]["instructions"]
        )
        await _tool(
            provider,
            "edit",
            native_voice.STEER_ACTIVE,
            {"request": "For the report, only include March."},
        )
        assert (
            "not confirmed the change was applied"
            in (await event(provider.outbox))["item"]["output"]
        )
        await event(provider.outbox)
        assert [call.kwargs["operation"] for call in follow_up.call_args_list] == [
            "queue",
            "steer",
        ]
        assert all(call.kwargs["target"] is target for call in follow_up.call_args_list)
        assert relay.accepted.work_item_id == "job"
        assert not relay.pending.done()
        submit.assert_awaited_once()
    finally:
        await browser.inbox.put({"type": "websocket.disconnect"})
        await task


@pytest.mark.asyncio
async def test_selected_profile_directory_is_not_read(monkeypatch):
    """A signed-in caller does not override selected profile restrictions."""
    lookup = AsyncMock()
    monkeypatch.setattr(native_voice_capabilities, "discover_voice_reads", lookup)
    browser, provider = Browser(), Provider()
    relay = native_voice.VoiceRelay(
        browser,
        provider,
        native_voice.VoiceScope("caller", "workspace", "profile", "thread"),
        check=AsyncMock(return_value=SimpleNamespace(id="thread")),
    )
    task = asyncio.create_task(relay.run())
    try:
        await event(provider.outbox)
        await provider.inbox.put({"type": "session.updated"})
        await event(browser.outbox)
        await _tool(
            provider,
            "workspaces",
            native_voice.FIND_CAPABILITY,
            {"query": "list workspaces"},
        )
        await event(provider.outbox)
        answer = (await event(provider.outbox))["response"]["instructions"]
        assert "unavailable in this selected profile" in answer
        lookup.assert_not_awaited()
    finally:
        await browser.inbox.put({"type": "websocket.disconnect"})
        await task


@pytest.mark.asyncio
async def test_interruption_retires_private_capability_continuation(monkeypatch):
    """A locally interrupted selection cannot run or replace normal speech."""
    run = AsyncMock(return_value="not current")
    monkeypatch.setattr(native_voice_capabilities, "invoke_voice_read", run)
    browser, provider = Browser(), Provider()
    relay = native_voice.VoiceRelay(
        browser,
        provider,
        native_voice.VoiceScope("caller", "workspace", "", "thread"),
        check=AsyncMock(return_value=SimpleNamespace(id="thread")),
    )
    task = asyncio.create_task(relay.run())
    try:
        await event(provider.outbox)
        await provider.inbox.put({"type": "session.updated"})
        await event(browser.outbox)
        selection, offer = await _negotiate(
            provider, "apps", "visible apps", "Visible apps here"
        )
        await provider.inbox.put({"type": "input_audio_buffer.speech_started"})
        assert await event(browser.outbox) == {"type": "flush"}
        await _choose(provider, "apps", selection, offer)
        with pytest.raises(TimeoutError):
            await asyncio.wait_for(provider.outbox.get(), 0.1)
        run.assert_not_awaited()
    finally:
        await browser.inbox.put({"type": "websocket.disconnect"})
        await task


@pytest.mark.asyncio
async def test_only_reviewed_routes_appear_and_changed_offer_never_reaches_route(
    monkeypatch,
):
    """Canonical enrollment can add a read without touching realtime schemas."""
    from app.agentive.tooling import catalogue, dispatch
    from app.agentive.tooling.manifest import load_manifest

    all_tools = load_manifest()
    tools = catalogue.build_voice_quick_read_catalogue()
    assert set(tools) == {
        "integral_list_workspaces",
        "integral_list_apps",
        "integral_list_tracks",
    }
    track_offers = await native_voice_capabilities.discover_voice_reads("tracks")
    assert {spec.name for spec in track_offers.values()} == {"integral_list_tracks"}
    assert not await native_voice_capabilities.discover_voice_reads("delete something")
    offer = tools["integral_list_tracks"]
    unsafe = offer.model_copy(update={"op_class": "execute"})
    assert not catalogue.admits_voice_quick_read(
        unsafe, catalogue.TOOL_BINDINGS[offer.name]
    )
    monkeypatch.setattr(
        dispatch, "load_manifest", lambda: {**all_tools, offer.name: unsafe}
    )
    invoke = AsyncMock()
    monkeypatch.setattr(dispatch, "invoke_route_in_process", invoke)
    result = await dispatch.dispatch_tool(
        offer.name,
        {},
        principal_id="caller",
        scope="workspace",
        voice_read_offer=offer,
    )
    assert result.is_error
    invoke.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("provider_rejects", [False, True])
async def test_optional_lookup_failure_keeps_call_ready(monkeypatch, provider_rejects):
    """An optional correlated selection error is not a live audio failure."""
    monkeypatch.setattr(native_voice, "CAPABILITY_OFFER_SECONDS", 0.2)
    monkeypatch.setattr(native_voice, "RESULT_POLL_SECONDS", 0.02)
    browser, provider = Browser(), Provider()
    relay = native_voice.VoiceRelay(
        browser,
        provider,
        native_voice.VoiceScope("caller", "workspace", "", "thread"),
        check=AsyncMock(return_value=SimpleNamespace(id="thread")),
    )
    task = asyncio.create_task(relay.run())
    try:
        await event(provider.outbox)
        await provider.inbox.put({"type": "session.updated"})
        await event(browser.outbox)
        await _tool(provider, "apps", native_voice.FIND_CAPABILITY, {"query": "apps"})
        await event(provider.outbox)
        created = await event(provider.outbox)
        if provider_rejects:
            await provider.inbox.put(
                {"type": "error", "error": {"event_id": created["event_id"]}}
            )
        report = (await event(provider.outbox))["response"]
        expected = (
            "quick lookup is unavailable"
            if provider_rejects
            else "quick lookup did not finish"
        )
        assert expected in report["instructions"]
        assert not task.done() and relay.capability_offer is None
    finally:
        await browser.inbox.put({"type": "websocket.disconnect"})
        await task
