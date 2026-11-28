"""The spoken cue is a runtime admission gate, not an optional model argument."""

import asyncio
import json
from unittest.mock import AsyncMock

import pytest

from app.services import native_voice as voice
from tests.test_native_voice import Browser, Provider, event, request


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "tool,args,operation,subject",
    [
        (
            voice.DELEGATE,
            {
                "request": "Can you tell me where my car rentals app is",
                "intent_summary": "",
            },
            "_work",
            "car rentals app",
        ),
        (
            voice.CALL_CONTEXT,
            {
                "detail": "workspace",
                "request": "Can you tell me where my car rentals app is",
            },
            "_work",
            "car rentals app",
        ),
        (
            voice.CALL_CONTEXT,
            {"detail": "workspace", "request": "Where is the quarterly review track?"},
            "_work",
            "quarterly review track",
        ),
        (
            voice.NAVIGATE,
            {
                "action": "apps",
                "ordinal": 0,
                "request": "Can you tell me where my car rentals app is?",
            },
            "_work",
            "car rentals app",
        ),
        (
            voice.NAVIGATE,
            {
                "action": "tracks",
                "ordinal": 0,
                "request": "Where is the quarterly review track?",
            },
            "_work",
            "quarterly review track",
        ),
        (
            voice.DELEGATE,
            {"request": "Find today's urgent tasks"},
            "_work",
            "urgent tasks",
        ),
        (
            voice.DELEGATE_ACTION,
            {"request": "Draft a maintenance checklist", "intent_summary": ""},
            "_work",
            "maintenance checklist",
        ),
        (voice.OPEN_PAGE, {"destination": "Car Rentals"}, "_open_page", "Car Rentals"),
        (voice.NAVIGATE, {"action": "apps", "ordinal": 0}, "_navigate", "apps"),
        (voice.FIND_RECORDS, {"query": "Tulip"}, "_navigate", "Tulip"),
        (voice.CHECK_CHAT, {"view": "latest_reply"}, "_read_live_tool", "latest reply"),
        (
            voice.FIND_CAPABILITY,
            {"query": "Available apps"},
            "_read_live_tool",
            "Available apps",
        ),
        (
            voice.WRITE_CHAT_NOTE,
            {"text": "Remember the blue umbrella"},
            "_write_requested_note",
            "note",
        ),
        (
            voice.REQUEST_REVIEW,
            {"decision": "approve"},
            "_request_review_confirmation",
            "confirmation",
        ),
        (
            voice.REVISE_REVIEW,
            {"field": "Title", "value": "Blue umbrella"},
            "_revise_pending_review",
            "correction",
        ),
        (voice.CANCEL_WORK, {}, "_cancel_accepted_work", "cancellation"),
        (
            voice.CALL_CONTEXT,
            {"detail": "workspace"},
            "_read_call_context",
            "workspace",
        ),
    ],
)
async def test_every_tool_path_waits_for_the_intent_audio(
    tool, args, operation, subject
):
    browser, provider = Browser(), Provider(manual_intent=True)
    relay = voice.VoiceRelay(
        browser,
        provider,
        voice.VoiceScope("caller", "workspace", "", "thread"),
        check=AsyncMock(),
    )
    relay.ready = True
    relay.workspace_label = "Fixture workspace"
    relay.review_confirmation_supported = True
    if operation == "_work":
        # Exercise the shared admission gate before calling the resident,
        # not a mock of _work that would bypass it.
        execute = AsyncMock(return_value="Finished.")
        relay.work = execute
    else:
        execute = AsyncMock()
        setattr(relay, operation, execute)
    reader = asyncio.create_task(relay._read_provider())
    try:
        await provider.inbox.put(
            {"type": "response.created", "response": {"id": "caller"}}
        )
        await provider.inbox.put(request("tool", "caller", "", name=tool, args=args))
        cue = (await event(provider.outbox))["response"]
        assert cue["metadata"]["integral_intent_ack"] == "1"
        assert subject.casefold() in cue["instructions"].casefold()
        assert cue["tool_choice"] == "none"
        execute.assert_not_awaited()
        await provider.inbox.put(
            {
                "type": "response.created",
                "response": {"id": "intent", "metadata": cue["metadata"]},
            }
        )
        await provider.inbox.put(
            {
                "type": "response.output_audio.delta",
                "response_id": "intent",
                "delta": "AAE=",
            }
        )
        assert await event(browser.outbox) == b"\x00\x01"
        execute.assert_not_awaited()
        await provider.inbox.put(
            {
                "type": "response.done",
                "response": {"id": "intent", "status": "completed"},
            }
        )
        for _ in range(100):
            if execute.await_count:
                break
            await asyncio.sleep(0.001)
        execute.assert_awaited_once()
        assert relay.protocol_counts["handoff_audio_sent"] == 1
    finally:
        reader.cancel()
        tasks = [reader] + [task for task in (relay.pending, relay.live_read) if task]
        for task in tasks:
            if not task.done():
                task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)


@pytest.mark.asyncio
async def test_silent_or_interrupted_intent_never_starts_work():
    browser, provider = Browser(), Provider(manual_intent=True)
    work = AsyncMock()
    relay = voice.VoiceRelay(
        browser,
        provider,
        voice.VoiceScope("caller", "workspace", "", "thread"),
        check=AsyncMock(),
        work=work,
    )
    relay.ready = True
    reader = asyncio.create_task(relay._read_provider())
    try:
        await provider.inbox.put(
            {"type": "response.created", "response": {"id": "caller"}}
        )
        await provider.inbox.put(request("tool", "caller", "Find the Car Rentals app"))
        cue = (await event(provider.outbox))["response"]
        await provider.inbox.put(
            {
                "type": "response.created",
                "response": {"id": "silent", "metadata": cue["metadata"]},
            }
        )
        await provider.inbox.put(
            {
                "type": "response.done",
                "response": {"id": "silent", "status": "completed"},
            }
        )
        await relay.pending
        assert "haven't started" in (await event(provider.outbox))["item"]["output"]
        work.assert_not_awaited()
    finally:
        reader.cancel()
        await asyncio.gather(reader, return_exceptions=True)


@pytest.mark.asyncio
async def test_direct_tool_turn_suppresses_speculative_speech():
    browser, provider = Browser(), Provider(manual_intent=True)
    relay = voice.VoiceRelay(
        browser,
        provider,
        voice.VoiceScope("caller", "workspace", "", "thread"),
        check=AsyncMock(),
    )
    relay.ready = True
    reader = asyncio.create_task(relay._read_provider())
    try:
        await provider.inbox.put(
            {"type": "response.created", "response": {"id": "direct"}}
        )
        await provider.inbox.put(
            {
                "type": "response.output_item.added",
                "response_id": "direct",
                "item": {
                    "type": "function_call",
                    "name": voice.OPEN_PAGE,
                    "call_id": "open",
                },
            }
        )
        await provider.inbox.put(
            {
                "type": "response.output_audio.delta",
                "response_id": "direct",
                "delta": "AAE=",
            }
        )
        await provider.inbox.put(
            request(
                "open",
                "direct",
                "",
                name=voice.OPEN_PAGE,
                args={"destination": "Rental fleet"},
            )
        )
        cue = (await event(provider.outbox))["response"]
        assert "Rental fleet" in cue["instructions"]
        assert browser.outbox.empty()
    finally:
        reader.cancel()
        if relay.live_read:
            relay.live_read.cancel()
        await asyncio.gather(
            reader,
            *([relay.live_read] if relay.live_read else []),
            return_exceptions=True,
        )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "caller_text,cue,tool",
    [
        ("Could you pull up my tasks?", "I'll pull up my tasks.", voice.DELEGATE),
        (
            "Could you pull up tasks for today?",
            "I'll pull up my tasks for today.",
            voice.DELEGATE,
        ),
        (
            "Can you review Maria's assigned tasks?",
            "I'll review Maria's assigned tasks.",
            voice.DELEGATE,
        ),
        ("Check our shared project schedule", "", voice.DELEGATE),
        (
            'Create a task titled "My Tasks" for me',
            'I’ll create "My Tasks" for you.',
            voice.DELEGATE_ACTION,
        ),
        ("Add my update to their shared project", "", voice.DELEGATE_ACTION),
    ],
)
async def test_spoken_perspective_is_separate_from_caller_authored_request(
    caller_text, cue, tool
):
    browser, provider = Browser(), Provider(manual_intent=True)
    work = AsyncMock(return_value="Done.")
    relay = voice.VoiceRelay(
        browser,
        provider,
        voice.VoiceScope("caller", "workspace", "", "thread"),
        check=AsyncMock(),
        work=work,
    )
    relay.ready = True
    reader = asyncio.create_task(relay._read_provider())
    try:
        await provider.inbox.put(
            {"type": "response.created", "response": {"id": "caller"}}
        )
        await provider.inbox.put(
            request(
                "tool",
                "caller",
                "",
                name=tool,
                args={"request": caller_text, "intent_summary": cue},
            )
        )
        response = (await event(provider.outbox))["response"]
        instructions = response["instructions"]
        assert voice._ACKNOWLEDGMENT_PERSPECTIVE in instructions
        assert "second person" in instructions and "third-party" in instructions
        assert json.dumps(caller_text, ensure_ascii=False) in instructions
        if cue:
            assert json.dumps(cue, ensure_ascii=False) in instructions
            assert "not a verbatim script" in instructions
        work.assert_not_awaited()
        await provider.inbox.put(
            {
                "type": "response.created",
                "response": {"id": "intent", "metadata": response["metadata"]},
            }
        )
        await provider.inbox.put(
            {
                "type": "response.output_audio.delta",
                "response_id": "intent",
                "delta": "AAE=",
            }
        )
        assert await event(browser.outbox) == b"\x00\x01"
        work.assert_not_awaited()
        await provider.inbox.put(
            {
                "type": "response.done",
                "response": {"id": "intent", "status": "completed"},
            }
        )
        for _ in range(100):
            if work.await_count:
                break
            await asyncio.sleep(0.001)
        work.assert_awaited_once_with(relay.scope, "tool", caller_text)
    finally:
        reader.cancel()
        tasks = [reader] + [task for task in (relay.pending, relay.live_read) if task]
        for task in tasks:
            if not task.done():
                task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)


@pytest.mark.parametrize("title", ["My Tasks", "Maria's Tasks", "Our Tasks"])
def test_direct_navigation_preserves_title_without_claiming_ownership(title):
    assert (
        voice._direct_intent(voice.OPEN_PAGE, {"destination": title})
        == f"I’ll open {title}."
    )


def test_voice_prompt_and_tool_contract_distinguish_perspectives():
    instructions = voice.session_update("", workspace_label="Example workspace")[
        "session"
    ]["instructions"]
    assert voice._ACKNOWLEDGMENT_PERSPECTIVE in instructions
    assert "never guess their name" in instructions
    named = voice.session_update("", caller_name="Casey Example")["session"][
        "instructions"
    ]
    assert '"Casey Example"' in named and "only in direct address" in named
    assert "never as a third-person possessive" in named
    for tool in (voice.TOOL, voice._ACTION_TOOL):
        properties = tool["parameters"]["properties"]
        assert (
            voice._ACKNOWLEDGMENT_PERSPECTIVE
            in properties["intent_summary"]["description"]
        )
        assert "caller's perspective" in properties["request"]["description"]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "already_spoken,partial", [(True, False), (False, False), (True, True)]
)
async def test_open_page_only_voiced_once_before_navigation(already_spoken, partial):
    browser, provider = Browser(), Provider(manual_intent=True)
    relay = voice.VoiceRelay(
        browser,
        provider,
        voice.VoiceScope("caller", "workspace", "", "thread"),
        check=AsyncMock(),
    )
    relay.ready = True
    relay.response_generation = "turn"
    if already_spoken:
        relay.last_spoken_response = "turn"
    if partial:
        relay.suppressed_ordinary_audio = "turn"
    cue = "I'll open your specimen catalog."
    turn = request(
        "open",
        "turn",
        "",
        name=voice.OPEN_PAGE,
        args={
            "destination": "Specimen Catalog",
            "request": "Open my specimen catalog",
            "intent_summary": cue,
        },
    )
    if already_spoken:
        turn["response"]["output"].insert(
            0, {"type": "message", "content": [{"type": "audio", "transcript": cue}]}
        )
    execute = AsyncMock()
    relay._open_page = execute
    reader = asyncio.create_task(relay._read_provider())
    try:
        await relay._completed_response(turn)
        if not already_spoken or partial:
            response = (await event(provider.outbox))["response"]
            assert cue in response["instructions"]
            execute.assert_not_awaited()
            await provider.inbox.put(
                {
                    "type": "response.created",
                    "response": {"id": "cue", "metadata": response["metadata"]},
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
        await asyncio.wait_for(relay.live_read, timeout=3)
        execute.assert_awaited_once()
        assert len(provider.intent_responses) == int(not already_spoken or partial)
        assert relay.protocol_counts["handoff_already_spoken"] == int(
            already_spoken and not partial
        )
    finally:
        reader.cancel()
        await asyncio.gather(reader, return_exceptions=True)


@pytest.mark.parametrize(
    "spoken,cue,destination,expected",
    [
        (
            "Sure, let me pull up the specimen catalog for you.",
            "I'll open the specimen catalog.",
            "specimen catalog",
            True,
        ),
        (
            "I'll bring up your reading room.",
            "I'll open your reading room.",
            "reading room",
            True,
        ),
        (
            "I can't open the specimen catalog.",
            "I'll open the specimen catalog.",
            "specimen catalog",
            False,
        ),
        (
            "I'll pull up the field notes.",
            "I'll open the specimen catalog.",
            "specimen catalog",
            False,
        ),
        ("I'll open it.", "I'll open the second result.", 2, False),
    ],
)
def test_navigation_acknowledgment_reuses_completed_subject_not_unrelated_speech(
    spoken, cue, destination, expected
):
    assert voice._navigation_ack_matches(spoken, cue, destination) is expected


@pytest.mark.parametrize(
    "spoken,cue,goal,expected",
    [
        (
            "Got it. I'll update that dashboard's name in Orchid Research.",
            "I'll rename the dashboard in Orchid Research to Field Notes.",
            "Rename the dashboard in Orchid Research to Field Notes.",
            True,
        ),
        (
            "Sure, I'll check your reading room inventory.",
            "I'll look up the reading room inventory.",
            "Can you find the reading room inventory?",
            True,
        ),
        (
            "I'll check that for you.",
            "I'll check the specimen catalog.",
            "Check the specimen catalog.",
            False,
        ),
        (
            "I can't check your reading room inventory.",
            "I'll look up the reading room inventory.",
            "Can you find the reading room inventory?",
            False,
        ),
        (
            "I'll review the laboratory sample counts.",
            "I'll look up the reading room inventory.",
            "Can you find the reading room inventory?",
            False,
        ),
    ],
)
def test_delegated_ack_deduplicates_only_grounded_completed_intent(
    spoken, cue, goal, expected
):
    assert voice._delegated_ack_matches(spoken, cue, goal) is expected


@pytest.mark.asyncio
async def test_redirect_reuses_only_complete_delivered_audio_item():
    """Do not repeat a cue just because a later speculative item was withheld."""
    browser, provider = Browser(), Provider(manual_intent=True)
    work = AsyncMock(return_value="Found it.")
    relay = voice.VoiceRelay(
        browser,
        provider,
        voice.VoiceScope("caller", "workspace", "", "thread"),
        check=AsyncMock(),
        work=work,
    )
    relay.ready = True
    relay.response_generation = "turn"
    relay.last_spoken_response = "turn"
    relay.suppressed_ordinary_audio = "turn"
    relay.suppressed_ordinary_items.add("unheard")
    relay.playback_items[("heard", 0)] = voice._PlaybackItem(
        1, "heard", 0, 24000, "turn"
    )
    turn = request(
        "tool",
        "turn",
        "",
        name=voice.NAVIGATE,
        args={
            "action": "apps",
            "ordinal": 0,
            "request": "Can you tell me where my specimen catalog app is?",
        },
    )
    turn["response"]["output"].insert(
        0,
        {
            "id": "heard",
            "type": "message",
            "content": [
                {
                    "type": "audio",
                    "transcript": "I'll look for your specimen catalog app.",
                }
            ],
        },
    )
    turn["response"]["output"].append(
        {
            "id": "unheard",
            "type": "message",
            "content": [{"type": "audio", "transcript": "I can't run that."}],
        }
    )
    await relay._completed_response(turn)
    await asyncio.wait_for(relay.pending, timeout=3)
    work.assert_awaited_once()
    assert not provider.intent_responses
    assert relay.protocol_counts["handoff_already_spoken"] == 1


@pytest.mark.asyncio
async def test_directory_uses_its_already_delivered_natural_acknowledgment():
    browser, provider = Browser(), Provider(manual_intent=True)
    relay = voice.VoiceRelay(
        browser,
        provider,
        voice.VoiceScope("caller", "workspace", "", "thread"),
        check=AsyncMock(),
    )
    relay.ready = True
    relay.response_generation = "turn"
    relay.last_spoken_response = "turn"
    relay._navigate = AsyncMock()
    turn = request(
        "tool",
        "turn",
        "",
        name=voice.NAVIGATE,
        args={"action": "apps", "ordinal": 0, "request": "Which apps can I use here?"},
    )
    turn["response"]["output"].insert(
        0,
        {
            "type": "message",
            "content": [
                {
                    "type": "audio",
                    "transcript": "Okay, let me take a look at the available apps.",
                }
            ],
        },
    )
    await relay._completed_response(turn)
    await asyncio.wait_for(relay.live_read, timeout=3)
    relay._navigate.assert_awaited_once()
    assert not provider.intent_responses
    assert relay.protocol_counts["handoff_already_spoken"] == 1


@pytest.mark.asyncio
async def test_speculative_ordinary_prefix_never_reaches_browser_when_tool_follows(
    monkeypatch,
):
    monkeypatch.setattr(voice, "ORDINARY_AUDIO_PREROLL_SECONDS", 0.1)
    browser, provider = Browser(), Provider(manual_intent=True)
    relay = voice.VoiceRelay(
        browser,
        provider,
        voice.VoiceScope("caller", "workspace", "", "thread"),
        check=AsyncMock(),
    )
    relay.ready = True
    relay._open_page = AsyncMock()
    reader = asyncio.create_task(relay._read_provider())
    try:
        await provider.inbox.put(
            {"type": "response.created", "response": {"id": "turn"}}
        )
        await provider.inbox.put(
            {
                "type": "response.output_audio.delta",
                "response_id": "turn",
                "item_id": "prefix",
                "content_index": 0,
                "delta": "AAE=",
            }
        )
        await asyncio.sleep(0.01)
        assert browser.outbox.empty()
        await provider.inbox.put(
            {
                "type": "response.output_item.added",
                "response_id": "turn",
                "item": {
                    "type": "function_call",
                    "name": voice.OPEN_PAGE,
                    "call_id": "open",
                },
            }
        )
        await provider.inbox.put(
            request(
                "open",
                "turn",
                "",
                name=voice.OPEN_PAGE,
                args={"destination": "Specimen room"},
            )
        )
        cue = (await event(provider.outbox))["response"]
        assert "Specimen room" in cue["instructions"]
        assert browser.outbox.empty()
        relay._open_page.assert_not_awaited()
        await asyncio.sleep(0.12)
        assert browser.outbox.empty()
    finally:
        reader.cancel()
        if relay.live_read:
            relay.live_read.cancel()
        await asyncio.gather(
            reader,
            *([relay.live_read] if relay.live_read else []),
            return_exceptions=True,
        )


@pytest.mark.asyncio
async def test_ordinary_preroll_releases_pure_conversation_and_drops_on_barge_in(
    monkeypatch,
):
    monkeypatch.setattr(voice, "ORDINARY_AUDIO_PREROLL_SECONDS", 0.03)
    browser, provider = Browser(), Provider(manual_intent=True)
    relay = voice.VoiceRelay(
        browser,
        provider,
        voice.VoiceScope("caller", "workspace", "", "thread"),
        check=AsyncMock(),
    )
    relay.ready = True
    reader = asyncio.create_task(relay._read_provider())
    try:
        await provider.inbox.put(
            {"type": "response.created", "response": {"id": "first"}}
        )
        await provider.inbox.put(
            {
                "type": "response.output_audio.delta",
                "response_id": "first",
                "delta": "AAE=",
            }
        )
        assert await event(browser.outbox) == b"\x00\x01"
        await provider.inbox.put(
            {
                "type": "response.done",
                "response": {"id": "first", "status": "completed", "output": []},
            }
        )
        await provider.inbox.put(
            {"type": "response.created", "response": {"id": "second"}}
        )
        await provider.inbox.put(
            {
                "type": "response.output_audio.delta",
                "response_id": "second",
                "delta": "AAE=",
            }
        )
        await provider.inbox.put({"type": "input_audio_buffer.speech_started"})
        assert await event(browser.outbox) == {"type": "flush"}
        await asyncio.sleep(0.06)
        assert browser.outbox.empty()
    finally:
        reader.cancel()
        relay._drop_ordinary_preroll()
        await asyncio.gather(reader, return_exceptions=True)


@pytest.mark.asyncio
async def test_verified_review_outcome_supersedes_stale_provider_context():
    browser, provider = Browser(), Provider(manual_intent=True)
    relay = voice.VoiceRelay(
        browser,
        provider,
        voice.VoiceScope("caller", "workspace", "", "thread"),
        check=AsyncMock(),
    )
    await relay._retain_review_outcome("rejected")
    update = await event(provider.outbox)
    assert update["type"] == "conversation.item.create"
    assert update["item"]["role"] == "system"
    text = update["item"]["content"][0]["text"]
    assert "independently rejected" in text
    assert "delegate a new request" in text
    assert "own authorization" in text
    assert browser.outbox.empty()
    await relay._retain_review_outcome("unverified")
    assert provider.outbox.empty()


def test_realtime_opening_uses_authoritative_caller_name_without_repetition():
    known = voice._opening_response("Integral AI", "new", "morning", "Eldon Marks")[
        "response"
    ]["instructions"]
    unnamed = voice._opening_response("Integral AI", "new", "morning", "")["response"][
        "instructions"
    ]
    assert '"Eldon Marks"' in known
    assert "Address the caller by their known name in this greeting." in known
    assert "never repeat it in every reply" in known
    assert "known name in this greeting" not in unnamed
    assert "never guess their name" in unnamed
