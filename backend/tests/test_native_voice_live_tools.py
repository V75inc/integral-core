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


def test_named_session_has_its_identity_and_can_verify_the_requested_detail():
    session = native_voice.session_update("", workspace_label="QA desk")["session"]
    assert '"QA desk"' in session["instructions"]
    assert native_voice.CALL_CONTEXT in {tool["name"] for tool in session["tools"]}
    unnamed = native_voice.session_update("")["session"]
    assert native_voice.CALL_CONTEXT not in {tool["name"] for tool in unnamed["tools"]}


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("args", "expected"),
    [
        ({}, "This call is in Fixture workspace, with Fixture guide."),
        ({"detail": "workspace"}, "Fixture workspace"),
        ({"detail": "assistant"}, "Fixture guide"),
    ],
)
async def test_call_identity_uses_admitted_labels_and_never_submits_work(
    args, expected
):
    """A private identity read uses only the two bound display names."""
    scope = native_voice.VoiceScope("caller-id", "workspace-id", "", "thread-id")
    check, work = AsyncMock(), AsyncMock()
    relay = native_voice.VoiceRelay(
        Browser(), Provider(), scope, check=check, work=work
    )
    relay.workspace_label = "Fixture workspace"
    relay.name = "Fixture guide"
    relay.ready = True
    relay.response_generation = "identity"
    relay._speak_result = AsyncMock()
    relay._start_handoff = (
        AsyncMock()
    )  # isolate operation from external audio in this unit test
    await relay._completed_response(
        request(
            "identity-call", "identity", "", name=native_voice.CALL_CONTEXT, args=args
        )
    )
    await relay.live_read
    assert check.await_count == 2
    relay._speak_result.assert_awaited_once_with(
        relay.generation,
        result=expected,
        read_back=True,
    )
    work.assert_not_awaited()


@pytest.mark.asyncio
async def test_chat_note_rechecks_call_authority_before_writing(monkeypatch):
    """A revoked call cannot leave a note in the previously bound thread."""
    from app.api.errors import InsufficientPermissionsError

    scope = native_voice.VoiceScope("caller", "workspace", "", "thread")
    denied = AsyncMock(side_effect=InsufficientPermissionsError(message="Call closed"))
    save = AsyncMock()
    monkeypatch.setattr(native_voice, "_save_chat_note", save)
    relay = native_voice.VoiceRelay(Browser(), Provider(), scope, check=denied)
    relay.ready = True
    relay.response_generation = "note"
    with pytest.raises(InsufficientPermissionsError):
        await relay._completed_response(
            request(
                "call",
                "note",
                "",
                name=native_voice.WRITE_CHAT_NOTE,
                args={"text": "Save this."},
            )
        )
    save.assert_not_awaited()


async def _negotiate(provider, identifier, query, title):
    await _tool(provider, identifier, native_voice.FIND_CAPABILITY, {"query": query})
    receipt = (await event(provider.outbox))["item"]
    assert receipt["call_id"] == identifier
    assert "action repeat" in receipt["output"]
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
            native_voice.DELEGATE_ACTION,
            native_voice.CHECK_CHAT,
            native_voice.WRITE_CHAT_NOTE,
            native_voice.REVISE_REVIEW,
            native_voice.REQUEST_REVIEW,
            native_voice.CANCEL_WORK,
            native_voice.END_CALL,
            native_voice.FIND_CAPABILITY,
            native_voice.FIND_RECORDS,
            native_voice.OPEN_PAGE,
            native_voice.NAVIGATE,
            native_voice.QUEUE_FOLLOW_UP,
        }
        await provider.inbox.put({"type": "session.updated"})
        assert (await event(browser.outbox))["thread_id"] == scope.thread
        await _tool(
            provider, "begin", native_voice.DELEGATE, {"request": "Draft the report."}
        )
        assert "accepted" in (await event(provider.outbox))["item"]["output"]
        assert not relay.pending.done()

        await provider.inbox.put({"type": "input_audio_buffer.speech_started"})
        await provider.inbox.put({"type": "input_audio_buffer.speech_stopped"})
        assert (await event(provider.outbox))["type"] == "session.update"
        await _tool(
            provider,
            "status",
            native_voice.CHECK_CHAT,
            {"view": "running_chat_request"},
        )
        assert "checked" in (await event(provider.outbox))["item"]["output"]
        speech = (await event(provider.outbox))["response"]
        assert "running: Draft the report." in speech["instructions"]
        assert speech["conversation"] == "none" and speech["tool_choice"] == "none"

        # The next spoken question interrupts the still-pending status readout.
        await provider.inbox.put({"type": "input_audio_buffer.speech_started"})
        await provider.inbox.put({"type": "input_audio_buffer.speech_stopped"})
        speech = await _quick_read(provider, "apps", "show apps", "Visible apps here")
        assert "Visible Projects" in speech and "hidden-app-id" not in speech
        assert "Visible apps here" not in speech
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
            "added that follow-up" in (await event(provider.outbox))["item"]["output"]
        )
        await event(provider.outbox)
        await provider.inbox.put({"type": "input_audio_buffer.speech_started"})
        await provider.inbox.put({"type": "input_audio_buffer.speech_stopped"})
        await _tool(
            provider,
            "foreground",
            native_voice.CHECK_CHAT,
            {"view": "running_chat_request"},
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
            "sent your correction" in (await event(provider.outbox))["item"]["output"]
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


@pytest.mark.asyncio
async def test_fourth_catalogue_enrollment_is_usable_without_new_realtime_tool(
    monkeypatch, tmp_path
):
    """Enroll a synthetic fourth read through normal manifest and route wiring."""
    import yaml

    from app.agentive.tooling import bindings, invoke
    from app.agentive.tooling.catalogue import build_voice_quick_read_catalogue
    from app.agentive.tooling.manifest import DEFAULT_MANIFEST_PATH, load_manifest

    tool_name = "integral_list_fixture_observations"
    title = "Visible observation stations"
    manifest = yaml.safe_load(DEFAULT_MANIFEST_PATH.read_text())
    manifest["domains"]["voice_catalogue_test"] = {
        "tools": [
            {
                "name": tool_name,
                "status": "existing",
                "op_class": "read",
                "priority": "P1",
                "summary": "List the caller's fixture observation stations.",
                "http": "GET /api/fixture-observation-stations",
                "params": {"limit": {"type": "integer", "optional": True}},
                "returns": {"stations": [{"name": "str"}]},
                "voice_quick_read": {
                    "title": title,
                    "terms": "weather observation stations observatory",
                    "collection": "stations",
                    "label_field": "name",
                    "page_limit": True,
                },
            }
        ]
    }
    manifest_path = tmp_path / "tool_manifest.yaml"
    manifest_path.write_text(yaml.safe_dump(manifest))
    monkeypatch.setenv("INTEGRAL_TOOL_MANIFEST", str(manifest_path))

    calls = []

    async def list_stations(request, limit):
        calls.append((request.headers["X-Integral-Scope"], limit))
        return {"stations": [{"name": "Pine Ridge Station", "id": "internal-station"}]}

    monkeypatch.setitem(
        bindings.TOOL_BINDINGS,
        tool_name,
        bindings.ToolBinding(handler_ref=lambda: list_stations),
    )
    assert tool_name in load_manifest()
    assert len(build_voice_quick_read_catalogue()) == 4
    monkeypatch.setattr(
        invoke, "resolve_user", AsyncMock(return_value=SimpleNamespace(id="caller"))
    )
    submit = AsyncMock(
        return_value=SimpleNamespace(work_item_id="foreground", message_id="accepted")
    )
    thread = SimpleNamespace(id="thread")
    check = AsyncMock(return_value=thread)
    monkeypatch.setattr(native_voice, "authorize", check)
    monkeypatch.setattr(chat_turn_submissions, "submit_chat_turn", submit)
    monkeypatch.setattr(
        native_voice, "_delegated_status", AsyncMock(return_value=("running", None))
    )

    for profile in ("", "restricted-profile"):
        browser, provider = Browser(), Provider()
        relay = native_voice.VoiceRelay(
            browser,
            provider,
            native_voice.VoiceScope("caller", "workspace", profile, "thread"),
            check=check,
        )
        task = asyncio.create_task(relay.run())
        try:
            setup = (await event(provider.outbox))["session"]
            assert tool_name not in {tool["name"] for tool in setup["tools"]}
            await provider.inbox.put({"type": "session.updated"})
            assert (await event(browser.outbox))["thread_id"] == "thread"
            if not profile:
                await _tool(
                    provider,
                    "heavy",
                    native_voice.DELEGATE,
                    {"request": "Draft a report."},
                )
                assert "accepted" in (await event(provider.outbox))["item"]["output"]
                assert not relay.pending.done()
                await provider.inbox.put({"type": "input_audio_buffer.speech_started"})
                await provider.inbox.put({"type": "input_audio_buffer.speech_stopped"})
                assert (await event(provider.outbox))["type"] == "session.update"
                answer = await _quick_read(
                    provider, "stations", "weather observation stations", title
                )
                assert (
                    "Pine Ridge Station" in answer and "internal-station" not in answer
                )
                assert title not in answer
                assert not relay.pending.done()
                assert relay.accepted.work_item_id == "foreground"
            else:
                await _tool(
                    provider,
                    "stations",
                    native_voice.FIND_CAPABILITY,
                    {"query": "weather observation stations"},
                )
                await event(provider.outbox)
                denial = (await event(provider.outbox))["response"]
                assert "unavailable in this selected profile" in denial["instructions"]
                assert (
                    title not in denial["instructions"]
                    and "Pine Ridge Station" not in denial["instructions"]
                )
                assert "integral_capability_offer" not in denial["metadata"]
        finally:
            await browser.inbox.put({"type": "websocket.disconnect"})
            await task

    assert calls == [("ws:workspace", 20)]
    submit.assert_awaited_once()


@pytest.mark.asyncio
@pytest.mark.parametrize("still_authorized", [False, True])
async def test_quick_read_checks_authority_at_first_audio_not_each_chunk(
    monkeypatch, still_authorized
):
    """Fence browser playback after upstream synthesis, without per-chunk auth."""
    from app.api.errors import InsufficientPermissionsError

    browser, provider = Browser(), Provider()
    probes = []

    async def authority(_scope):
        probes.append(None)
        if not still_authorized:
            raise InsufficientPermissionsError("Conversation unavailable")

    checked = AsyncMock(return_value=SimpleNamespace(id="thread"))
    relay = native_voice.VoiceRelay(
        browser,
        provider,
        native_voice.VoiceScope("caller", "workspace", "", "thread"),
        check=checked,
    )
    monkeypatch.setattr(
        native_voice_capabilities,
        "invoke_voice_read",
        AsyncMock(return_value="A private answer."),
    )
    task = asyncio.create_task(relay.run())
    try:
        await event(provider.outbox)
        await provider.inbox.put({"type": "session.updated"})
        assert (await event(browser.outbox))["thread_id"] == "thread"
        selection, offer = await _negotiate(
            provider, "apps", "visible apps", "Visible apps here"
        )
        await _choose(provider, "apps", selection, offer)
        speech = (await event(provider.outbox))["response"]
        assert "A private answer." in speech["instructions"]

        relay.check = authority
        await provider.inbox.put(
            {
                "type": "response.created",
                "response": {"id": "spoken", "metadata": speech["metadata"]},
            }
        )
        for _ in range(2):
            await provider.inbox.put(
                {
                    "type": "response.output_audio.delta",
                    "response_id": "spoken",
                    "delta": "AAE=",
                }
            )
        if still_authorized:
            assert await event(browser.outbox) == b"\x00\x01"
            assert await event(browser.outbox) == b"\x00\x01"
            assert len(probes) == 1
        else:
            with pytest.raises(InsufficientPermissionsError):
                await asyncio.wait_for(task, 2)
            assert browser.outbox.empty()
            assert len(probes) == 1
    finally:
        if not task.done():
            await browser.inbox.put({"type": "websocket.disconnect"})
            await task


@pytest.mark.asyncio
async def test_requested_saved_note_readback_preserves_literal_repeated_value():
    """A saved quotation keeps even a trailing count that ordinary summaries omit."""
    provider = Provider()
    relay = native_voice.VoiceRelay(
        Browser(),
        provider,
        native_voice.VoiceScope("caller", "workspace", "", "thread"),
        check=AsyncMock(),
    )
    note = "Trial six. Result: six."
    relay._read_chat_content = AsyncMock(return_value=note)
    await relay._read_live_tool(
        "call", native_voice.CHECK_CHAT, "latest_note", relay.generation
    )
    assert (await event(provider.outbox))["item"]["call_id"] == "call"
    speech = (await event(provider.outbox))["response"]
    assert json.loads(speech["instructions"].split("\nAnswer data: ")[1]) == note
    assert speech["tool_choice"] == "none"


@pytest.mark.asyncio
async def test_numbered_voice_list_revocation_before_audio(monkeypatch):
    """A resource-level change after synthesis begins fences first playback."""
    from app.services.native_voice_navigation import VoiceDestination, VoicePage

    browser, provider = Browser(), Provider()
    relay = native_voice.VoiceRelay(
        browser,
        provider,
        native_voice.VoiceScope("caller", "workspace", "", "thread"),
        check=AsyncMock(return_value=SimpleNamespace(id="thread")),
    )
    spec = await native_voice_capabilities.discover_voice_reads("apps")
    app_spec = next(s for s in spec.values() if s.name == "integral_list_apps")
    page = VoicePage(
        app_spec,
        (VoiceDestination("app", "private-id", "Private app"),),
        ("Private app",),
        collection="apps",
    )
    monkeypatch.setattr(
        native_voice_capabilities, "invoke_voice_read", AsyncMock(return_value=page)
    )
    monkeypatch.setattr(native_voice, "refresh_page", AsyncMock(return_value=None))
    task = asyncio.create_task(relay.run())
    try:
        await event(provider.outbox)
        await provider.inbox.put({"type": "session.updated"})
        await event(browser.outbox)
        selection, offer = await _negotiate(
            provider, "apps", "visible apps", "Visible apps here"
        )
        await _choose(provider, "apps", selection, offer)
        speech = (await event(provider.outbox))["response"]
        assert json.loads(speech["instructions"].split("\nAnswer data: ")[1]) == {
            "collection": "apps",
            "names": ["Private app"],
        }
        assert "private-id" not in speech["instructions"]
        await provider.inbox.put(
            {
                "type": "response.created",
                "response": {"id": "spoken", "metadata": speech["metadata"]},
            }
        )
        await provider.inbox.put(
            {
                "type": "response.output_audio.delta",
                "response_id": "spoken",
                "delta": "AAE=",
            }
        )
        await asyncio.sleep(0.05)
        assert browser.outbox.empty()
        native_voice.refresh_page.assert_awaited_once()
        assert relay.response_generation is None
    finally:
        await browser.inbox.put({"type": "websocket.disconnect"})
        await task


@pytest.mark.asyncio
@pytest.mark.parametrize("profile", ["", "published-profile"])
async def test_chat_write_tool_accepts_directory_only_in_workspace_voice(profile):
    """A private chat write cannot grant a profile an extra workspace directory."""
    provider = Provider()
    scope = native_voice.VoiceScope("caller", "workspace", profile, "thread")
    relay = native_voice.VoiceRelay(Browser(), provider, scope, check=AsyncMock())
    config = native_voice.session_update("", uses_published_profile=bool(profile))[
        "session"
    ]
    [tool] = [t for t in config["tools"] if t["name"] == native_voice.WRITE_CHAT_NOTE]
    assert ("directory" in tool["parameters"]["properties"]) is (not profile)
    relay.ready = True
    relay.response_generation = "turn"
    relay._post_directory = AsyncMock(return_value="I posted the list in this chat.")
    relay._speak_result = AsyncMock()
    relay._start_handoff = (
        AsyncMock()
    )  # isolate operation from external audio in this unit test
    frame = request(
        "posting",
        "turn",
        "",
        name=native_voice.WRITE_CHAT_NOTE,
        args={"directory": "last_list"},
    )
    await relay._completed_response(frame)
    if profile:
        relay._post_directory.assert_not_awaited()
        assert provider.outbox.empty()
    else:
        await relay.live_read
        relay._post_directory.assert_awaited_once_with("last_list", relay.generation)
        assert (await event(provider.outbox))["item"][
            "output"
        ] == "I posted the list in this chat."
        relay._speak_result.assert_awaited_once_with(
            relay.generation, result="I posted the list in this chat.", read_back=True
        )


@pytest.mark.asyncio
async def test_audio_only_call_uses_spoken_focus_and_explains_unavailable_actions(
    monkeypatch,
):
    """Run a provider conversation without a browser route, note sink or human confirmation."""
    from types import SimpleNamespace

    from app.services import native_voice_navigation as nav

    actions = native_voice.VoiceCallActions()
    relay = native_voice.VoiceRelay(
        Browser(),
        Provider(),
        native_voice.VoiceScope("caller", "workspace", "", "thread", actions=actions),
        check=AsyncMock(),
        work=AsyncMock(return_value="Two items are urgent."),
    )
    relay.ready = True
    relay._speak_result = AsyncMock()
    relay._start_handoff = (
        AsyncMock()
    )  # isolate operation from external audio in this unit test
    session = native_voice.session_update("", actions=actions)["session"]
    offered = {tool["name"] for tool in session["tools"]}
    assert {
        native_voice.NAVIGATE,
        native_voice.DELEGATE,
        native_voice.CHECK_CHAT,
    } <= offered
    unavailable = {
        native_voice.OPEN_PAGE,
        native_voice.WRITE_CHAT_NOTE,
        native_voice.REQUEST_REVIEW,
        native_voice.REVISE_REVIEW,
        native_voice.END_CALL,
    }
    assert not unavailable & offered
    assert "This call cannot open a screen" in session["instructions"]
    assert "still pending" in relay._present_review(
        SimpleNamespace(action="move the due date to Monday")
    )

    async def turn(name, args, key):
        relay.response_generation = key
        await relay._completed_response(request(key, key, "", name=name, args=args))

    one = nav.VoiceDestination("app", "a", "Garden notes")
    two = nav.VoiceDestination("app", "b", "Library notes")
    page = nav.VoicePage(None, (one, two), (one.label, two.label))
    relay.navigation.accept_page(page)
    monkeypatch.setattr(nav, "refresh_page", AsyncMock(return_value=page))
    monkeypatch.setattr(nav, "verified_destination", AsyncMock(return_value=True))
    await turn(native_voice.NAVIGATE, {"action": "select", "ordinal": 2}, "choose")
    await relay.live_read
    assert relay.navigation.focus == two
    assert relay.scope.page_context is None
    for name, args, expected in (
        (native_voice.OPEN_PAGE, {"destination": 2}, "cannot open"),
        (native_voice.REQUEST_REVIEW, {"decision": "approve"}, "No decision"),
        (native_voice.WRITE_CHAT_NOTE, {"text": "write me"}, "no written destination"),
        (
            native_voice.DELEGATE,
            {"request": "What is urgent?", "delivery": "written_only"},
            "no written destination",
        ),
    ):
        await turn(name, args, name)
        assert expected in relay._speak_result.await_args.kwargs["result"]
        relay.work.assert_not_awaited()
    await turn(
        native_voice.DELEGATE,
        {"request": "What is urgent in the selected app?"},
        "lookup",
    )
    await relay.pending
    relay.work.assert_awaited_once()
    assert relay.accepted_focus == two
    assert relay._speak_result.await_args.kwargs["result"] == "Two items are urgent."


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("caller_text", "cue"),
    [
        (
            "Could you add ‘Check quote 007’ to my list for Friday?",
            "Sure, I’ll add Check quote 007 to your Friday list.",
        ),
        (
            "Review the progress report and draft a reply about the hardware tranche.",
            "I’ll review the progress report and draft a reply explaining the hardware tranche.",
        ),
    ],
)
async def test_pending_handoff_speaks_plan_before_starting_work(caller_text, cue):
    provider, browser = Provider(manual_intent=True), Browser()
    work = AsyncMock(return_value="Finished.")
    relay = native_voice.VoiceRelay(
        browser,
        provider,
        native_voice.VoiceScope("user", "workspace", "", "thread"),
        check=AsyncMock(),
        work=work,
    )
    relay.ready = True
    relay.response_generation = "turn"
    reader = asyncio.create_task(relay._read_provider())
    try:
        await relay._completed_response(
            request(
                "call", "turn", "", args={"request": caller_text, "intent_summary": cue}
            )
        )
        speech = (await event(provider.outbox))["response"]
        assert speech["conversation"] == "none" and speech["tool_choice"] == "none"
        assert speech["input"] == []
        assert "never a new user request" in speech["instructions"]
        assert cue in speech["instructions"]
        work.assert_not_awaited()
        await provider.inbox.put(
            {
                "type": "response.created",
                "response": {"id": "ack", "metadata": speech["metadata"]},
            }
        )
        await provider.inbox.put(
            {
                "type": "response.output_audio.delta",
                "response_id": "ack",
                "delta": "AAE=",
            }
        )
        assert await event(browser.outbox) == b"\x00\x01"
        work.assert_not_awaited()
        await provider.inbox.put(
            {"type": "response.done", "response": {"id": "ack", "status": "completed"}}
        )
        await relay.pending
        work.assert_awaited_once_with(relay.scope, "call", caller_text)
        assert (await event(provider.outbox))["item"]["output"] == "Finished."
        assert "Finished." in (await event(provider.outbox))["response"]["instructions"]
    finally:
        reader.cancel()
        if relay.pending and not relay.pending.done():
            relay.pending.cancel()
        await asyncio.gather(reader, relay.pending, return_exceptions=True)


@pytest.mark.asyncio
@pytest.mark.parametrize("already_spoken", [False, True])
async def test_fast_lookup_or_spoken_acknowledgment_does_not_repeat_ack(already_spoken):
    provider = Provider(manual_intent=True)
    work = AsyncMock(return_value="The Friday list is ready.")
    relay = native_voice.VoiceRelay(
        Browser(),
        provider,
        native_voice.VoiceScope("user", "workspace", "", "thread"),
        check=AsyncMock(),
        work=work,
    )
    relay.ready = True
    relay.response_generation = "turn"
    if already_spoken:
        relay.last_spoken_response = "turn"
    turn = request(
        "call",
        "turn",
        "",
        args={
            "request": "What is on my Friday list?",
            "intent_summary": (
                "I’ll prepare your Friday list." if already_spoken else ""
            ),
        },
    )
    if already_spoken:
        turn["response"]["output"].insert(
            0,
            {
                "type": "message",
                "content": [
                    {"type": "audio", "transcript": "I’ll prepare your Friday list."}
                ],
            },
        )
    reader = asyncio.create_task(relay._read_provider())
    try:
        await relay._completed_response(turn)
        if not already_spoken:
            cue = (await event(provider.outbox))["response"]
            assert "Friday list" in cue["instructions"]
            work.assert_not_awaited()
            await provider.inbox.put(
                {
                    "type": "response.created",
                    "response": {"id": "cue", "metadata": cue["metadata"]},
                }
            )
            await provider.inbox.put(
                {
                    "type": "response.output_audio.delta",
                    "response_id": "cue",
                    "delta": "AAE=",
                }
            )
            await provider.inbox.put(
                {
                    "type": "response.done",
                    "response": {"id": "cue", "status": "completed"},
                }
            )
        await relay.pending
    finally:
        reader.cancel()
        await asyncio.gather(reader, return_exceptions=True)
    assert relay.handoff_gate is None
    assert (await event(provider.outbox))["item"][
        "output"
    ] == "The Friday list is ready."
    assert (
        "The Friday list is ready."
        in (await event(provider.outbox))["response"]["instructions"]
    )
    assert provider.outbox.empty()


@pytest.mark.asyncio
async def test_interruption_releases_handoff_without_resubmitting():
    browser, provider = Browser(), Provider(manual_intent=True)
    finish = asyncio.Event()

    async def work(*args):
        await finish.wait()
        return "Finished."

    worker = AsyncMock(side_effect=work)
    relay = native_voice.VoiceRelay(
        browser,
        provider,
        native_voice.VoiceScope("user", "workspace", "", "thread"),
        check=AsyncMock(),
        work=worker,
    )
    relay.ready = True
    relay.response_generation = "turn"
    await relay._completed_response(
        request(
            "call",
            "turn",
            "",
            args={
                "request": "Prepare the report",
                "intent_summary": "I’ll prepare your report.",
            },
        )
    )
    cue = (await event(provider.outbox))["response"]
    reader = asyncio.create_task(relay._read_provider())
    await provider.inbox.put(
        {
            "type": "response.created",
            "response": {"id": "cue", "metadata": cue["metadata"]},
        }
    )
    await provider.inbox.put(
        {"type": "response.output_audio.delta", "response_id": "cue", "delta": "AAE="}
    )
    assert await event(browser.outbox) == b"\x00\x01"
    worker.assert_not_awaited()
    await relay._interrupt()
    assert await event(browser.outbox) == {"type": "flush"}
    finish.set()
    await relay.pending
    worker.assert_awaited_once_with(relay.scope, "call", "Prepare the report")
    assert (await event(provider.outbox))["item"]["call_id"] == "call"
    assert provider.outbox.empty()
    reader.cancel()
    await asyncio.gather(reader, return_exceptions=True)


@pytest.mark.asyncio
async def test_workspace_identity_lookup_identity_sequence_has_one_answer_per_turn():
    browser, provider = Browser(), Provider()
    work = AsyncMock(return_value="Calendar, Tasks, and Documents.")
    relay = native_voice.VoiceRelay(
        browser,
        provider,
        native_voice.VoiceScope("caller", "workspace", "", "thread"),
        check=AsyncMock(),
        work=work,
    )
    relay.workspace_label = "Administrator"
    relay.ready = True
    reader = asyncio.create_task(relay._read_provider())
    try:
        for index, name, arguments, expected in (
            (1, native_voice.CALL_CONTEXT, {"detail": "workspace"}, "Administrator"),
            (
                2,
                native_voice.DELEGATE,
                {"request": "List my apps", "intent_summary": ""},
                "Calendar, Tasks, and Documents.",
            ),
            (3, native_voice.CALL_CONTEXT, {"detail": "workspace"}, "Administrator"),
        ):
            if index > 1:
                await provider.inbox.put({"type": "input_audio_buffer.speech_started"})
                assert await event(browser.outbox) == {"type": "flush"}
                await provider.inbox.put({"type": "input_audio_buffer.speech_stopped"})
            await _tool(provider, f"turn-{index}", name, arguments)
            await event(provider.outbox)
            answer = (await event(provider.outbox))["response"]
            assert expected in answer["instructions"]
            await provider.inbox.put(
                {
                    "type": "response.created",
                    "response": {
                        "id": f"answer-{index}",
                        "metadata": answer["metadata"],
                    },
                }
            )
            await provider.inbox.put(
                {
                    "type": "response.output_audio.delta",
                    "response_id": f"answer-{index}",
                    "delta": "AAE=",
                }
            )
            assert await event(browser.outbox) == b"\x00\x01"
            await provider.inbox.put(
                {
                    "type": "response.done",
                    "response": {"id": f"answer-{index}", "status": "completed"},
                }
            )
            await provider.inbox.put(
                {"type": "response.created", "response": {"id": f"extra-{index}"}}
            )
            await provider.inbox.put(
                {
                    "type": "response.output_audio.delta",
                    "response_id": f"extra-{index}",
                    "delta": "AAE=",
                }
            )
            with pytest.raises(TimeoutError):
                await asyncio.wait_for(browser.outbox.get(), 0.02)
            assert not reader.done()
        work.assert_awaited_once_with(relay.scope, "turn-2", "List my apps")
        assert relay.protocol_counts["suppressed_post_delegate_response"] == 3
        assert provider.outbox.empty()
    finally:
        reader.cancel()
        await asyncio.gather(reader, return_exceptions=True)


@pytest.mark.asyncio
async def test_generic_prelude_is_not_a_task_specific_recap():
    """Provider speaks filler before the tool; work still waits for the actual recap."""
    browser, provider = Browser(), Provider(manual_intent=True)
    work = AsyncMock(return_value="The plan is ready for review.")
    relay = native_voice.VoiceRelay(
        browser,
        provider,
        native_voice.VoiceScope("caller", "workspace", "", "thread"),
        check=AsyncMock(),
        work=work,
    )
    relay.ready = True
    relay.response_generation = "build"
    relay.last_spoken_response = "build"
    reader = asyncio.create_task(relay._read_provider())
    try:
        turn = request(
            "build-id",
            "build",
            "",
            args={
                "request": "Go ahead and build the app plan in our chat.",
                "intent_summary": "I’ll build the app from the plan we agreed on.",
            },
        )
        turn["response"]["output"].insert(
            0,
            {
                "type": "message",
                "content": [{"type": "audio", "transcript": "Okay, sure."}],
            },
        )
        await relay._completed_response(turn)
        cue = (await event(provider.outbox))["response"]
        assert "build the app from the plan" in cue["instructions"]
        work.assert_not_awaited()
        await provider.inbox.put(
            {
                "type": "response.created",
                "response": {
                    "id": "recap",
                    "metadata": cue["metadata"],
                },
            }
        )
        await provider.inbox.put(
            {
                "type": "response.output_audio.delta",
                "response_id": "recap",
                "delta": "AAE=",
            }
        )
        assert await event(browser.outbox) == b"\x00\x01"
        await provider.inbox.put(
            {
                "type": "response.done",
                "response": {"id": "recap", "status": "completed"},
            }
        )
        await relay.pending
        work.assert_awaited_once_with(
            relay.scope, "build-id", "Go ahead and build the app plan in our chat."
        )
        assert relay.protocol_counts["handoff_requested"] == 1
    finally:
        reader.cancel()
        await asyncio.gather(reader, return_exceptions=True)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "tool_name", [native_voice.DELEGATE_ACTION, native_voice.DELEGATE]
)
async def test_substantive_task_never_silently_skips_recap_when_model_omits_it(
    tool_name,
):
    browser, provider = Browser(), Provider(manual_intent=True)
    work = AsyncMock(return_value="The plan is ready.")
    relay = native_voice.VoiceRelay(
        browser,
        provider,
        native_voice.VoiceScope("caller", "workspace", "", "thread"),
        check=AsyncMock(),
        work=work,
    )
    relay.ready = True
    relay.response_generation = "plan"
    text = "Plan a new Integral app to track garden watering."
    reader = asyncio.create_task(relay._read_provider())
    try:
        await relay._completed_response(
            request(
                "plan",
                "plan",
                "",
                name=tool_name,
                args={"request": text, "intent_summary": ""},
            )
        )
        summary = (await event(provider.outbox))["response"]
        assert summary["tool_choice"] == "none"
        assert "Paraphrase the actual goal" in summary["instructions"]
        assert text in summary["instructions"]
        work.assert_not_awaited()
        await provider.inbox.put(
            {
                "type": "response.created",
                "response": {"id": "recap", "metadata": summary["metadata"]},
            }
        )
        await provider.inbox.put(
            {
                "type": "response.output_audio.delta",
                "response_id": "recap",
                "delta": "AAE=",
            }
        )
        assert await event(browser.outbox) == b"\x00\x01"
        await provider.inbox.put(
            {
                "type": "response.done",
                "response": {"id": "recap", "status": "completed"},
            }
        )
        await relay.pending
        work.assert_awaited_once_with(relay.scope, "plan", text)
        assert relay.protocol_counts["handoff_synthesized_from_request"] == 1
    finally:
        reader.cancel()
        await asyncio.gather(reader, return_exceptions=True)
