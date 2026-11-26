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
    await relay._completed_response(
        request(
            "identity-call", "identity", "", name=native_voice.CALL_CONTEXT, args=args
        )
    )
    check.assert_awaited_once_with(scope)
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
            native_voice.CHECK_CHAT,
            native_voice.WRITE_CHAT_NOTE,
            native_voice.REVISE_REVIEW,
            native_voice.REQUEST_REVIEW,
            native_voice.CANCEL_WORK,
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
        assert (await event(provider.outbox))["type"] == "session.update"
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
@pytest.mark.parametrize("intent", ["", "On it."])
async def test_only_a_requested_acknowledgment_is_spoken_before_work(intent):
    scope = native_voice.VoiceScope("user", "workspace", "", "thread")
    relay = native_voice.VoiceRelay(Browser(), Provider(), scope, check=AsyncMock())
    relay.conversational_review = True
    relay.ready = True
    relay.response_generation = "turn"

    async def admit(*args):
        relay.pending = asyncio.get_running_loop().create_future()

    relay._start_requested_work = admit
    relay._speak_handoff = AsyncMock()
    await relay._completed_response(
        request(
            "call", "turn", "", args={"request": "Friday", "intent_summary": intent}
        )
    )
    assert relay.pending is not None
    if intent:
        await relay.handoff_task
        relay._speak_handoff.assert_awaited_once_with(
            intent, relay.pending, relay.generation
        )
    else:
        assert relay.handoff_task is None
    relay.pending.cancel()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("caller_text", "cue"),
    [
        (
            "Could you add ‘Check quote 007’ to my list for Friday?",
            "Sure, I’ll add that to your Friday list.",
        ),
        (
            "Move my planning session to tomorrow at eight.",
            "Okay, I’ll move your planning session.",
        ),
    ],
)
async def test_pending_handoff_separates_chat_wording_from_spoken_intent(
    caller_text, cue
):
    provider = Provider()
    work = AsyncMock()
    finish = asyncio.Event()

    async def wait_for_worker(*args):
        await finish.wait()
        return "Finished."

    work.side_effect = wait_for_worker
    relay = native_voice.VoiceRelay(
        Browser(),
        provider,
        native_voice.VoiceScope("user", "workspace", "", "thread"),
        check=AsyncMock(),
        work=work,
    )
    relay.conversational_review = True
    relay.ready = True
    relay.response_generation = "turn"
    try:
        await relay._completed_response(
            request(
                "call", "turn", "", args={"request": caller_text, "intent_summary": cue}
            )
        )
        speech = (await event(provider.outbox))["response"]
        work.assert_awaited_once_with(relay.scope, "call", caller_text)
        assert speech["conversation"] == "none" and speech["tool_choice"] == "none"
        assert speech["input"] == []
        assert "already underway" in speech["instructions"]
        assert "never a new user request" in speech["instructions"]
        assert cue in speech["instructions"]
        assert caller_text not in speech["instructions"]
    finally:
        finish.set()
        await relay.pending
        await relay.handoff_task


@pytest.mark.asyncio
@pytest.mark.parametrize("already_spoken", [False, True])
async def test_fast_task_or_spoken_acknowledgment_does_not_prompt_a_second_ack(
    already_spoken,
):
    provider = Provider()
    work = AsyncMock(return_value="The Friday list is ready.")
    relay = native_voice.VoiceRelay(
        Browser(),
        provider,
        native_voice.VoiceScope("user", "workspace", "", "thread"),
        check=AsyncMock(),
        work=work,
    )
    relay.conversational_review = True
    relay.ready = True
    relay.response_generation = "turn"
    if already_spoken:
        relay.last_spoken_response = "turn"
    await relay._completed_response(
        request(
            "call",
            "turn",
            "",
            args={
                "request": "Can you prepare my Friday list?",
                "intent_summary": "Prepare Friday list.",
            },
        )
    )
    await relay.pending
    if relay.handoff_task:
        await relay.handoff_task
    assert (await event(provider.outbox))["item"][
        "output"
    ] == "The Friday list is ready."
    assert (
        "The Friday list is ready."
        in (await event(provider.outbox))["response"]["instructions"]
    )
    assert provider.outbox.empty()
