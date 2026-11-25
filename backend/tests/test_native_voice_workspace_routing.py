"""Synthetic chat routing parity for the authenticated Web Voice adapter."""

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.api.errors import InsufficientPermissionsError
from app.services.chat_providers.base import ChatTurnContext
from app.services.native_voice import VoiceScope

pytestmark = [pytest.mark.asyncio]
UTTERANCE = "Can you build me a dashboard for the CRM?"


@pytest.mark.smoke
@pytest.mark.parametrize(
    ("profile", "role", "response", "mode_hint"),
    [
        ("", "owner", "Your request needs approval", "workspace assistant"),
        (
            "restricted-fixture-profile",
            "member",
            "This profile cannot prepare that change.",
            "selected profile",
        ),
    ],
)
async def test_realtime_routes_workspace_request_to_existing_chat_without_authority_details(
    profile, role, response, mode_hint, monkeypatch
):
    import asyncio

    from app.agentive.work_models import WorkItem
    from app.models.nodes import ChatMessage, ChatThread
    from app.services import chat_turn_submissions, native_voice
    from tests.test_native_voice import Browser, Provider, event, request

    scope = VoiceScope("fixture-person", "fixture-workspace", profile, "fixture-thread")
    graph = SimpleNamespace(find_edges_between=AsyncMock(return_value=[object()]))
    thread = SimpleNamespace(
        id=scope.thread,
        archived=False,
        provider_id="integral_native",
        provider_session_id="",
        user_id=scope.principal,
        workspace_id=scope.workspace,
        agent_profile_key=profile,
        get_context=AsyncMock(return_value=graph),
    )
    monkeypatch.setattr(
        native_voice, "can_access_workspace", AsyncMock(return_value=role)
    )
    authorize_profile = AsyncMock()
    monkeypatch.setattr(native_voice, "require_native_chat_profile", authorize_profile)
    monkeypatch.setattr(ChatThread, "get", AsyncMock(return_value=thread))
    submit = AsyncMock(
        return_value=SimpleNamespace(work_item_id="worker", message_id="accepted")
    )
    monkeypatch.setattr(chat_turn_submissions, "submit_chat_turn", submit)
    monkeypatch.setattr(
        WorkItem,
        "get",
        AsyncMock(
            return_value=SimpleNamespace(
                kind="chat_turn",
                input_payload={},
                principal_id=scope.principal,
                workspace_id=scope.workspace,
                thread_id=scope.thread,
                status="succeeded" if profile else "waiting_for_human",
            )
        ),
    )
    monkeypatch.setattr(
        ChatMessage,
        "get",
        AsyncMock(
            return_value=SimpleNamespace(
                thread_id=scope.thread,
                id="result-message",
                role="assistant",
                parent_id="accepted",
                parts=[{"type": "text", "text": response}],
            )
        ),
    )
    browser, provider = Browser(), Provider()
    relay = native_voice.VoiceRelay(browser, provider, scope)
    call = asyncio.create_task(relay.run("Speak plainly."))
    try:
        config = (await event(provider.outbox))["session"]
        instructions = config["instructions"]
        assert mode_hint in instructions
        assert "including requests phrased as 'can you'" in instructions
        assert "Do not refuse a workspace request merely because" in instructions
        assert "do not accept approval by voice" in instructions
        assert ("creating a dashboard for review" in instructions) is (not profile)
        assert (native_voice.FIND_CAPABILITY in instructions) is (not profile)
        assert all(
            not private or private not in instructions
            for private in (scope.principal, scope.workspace, scope.thread, profile)
        )
        assert [tool["name"] for tool in config["tools"]] == [
            native_voice.DELEGATE,
            native_voice.CHECK_CHAT,
            *(
                [
                    native_voice.FIND_CAPABILITY,
                ]
                if not profile
                else []
            ),
            native_voice.WRITE_CHAT_NOTE,
            *(
                []
                if profile
                else [native_voice.REVISE_REVIEW, native_voice.REQUEST_REVIEW]
            ),
            native_voice.CANCEL_WORK,
            *(
                [
                    native_voice.FIND_RECORDS,
                    native_voice.NAVIGATE,
                    native_voice.QUEUE_FOLLOW_UP,
                    native_voice.STEER_ACTIVE,
                ]
                if not profile
                else []
            ),
        ]
        await provider.inbox.put({"type": "session.updated"})
        assert (await event(browser.outbox))["thread_id"] == scope.thread
        await provider.inbox.put({"type": "response.created", "response": {"id": "r1"}})
        for _ in range(2):
            await provider.inbox.put(request("fixture-call", "r1", UTTERANCE))
        output = (await event(provider.outbox))["item"]["output"]
        assert response in output
        followup = (await event(provider.outbox))["response"]
        assert "not yet applied" in followup["instructions"]
        assert "reports a denial" in followup["instructions"]
        assert followup["tool_choice"] == "none"
    finally:
        await browser.inbox.put({"type": "websocket.disconnect"})
        await call
    submit.assert_awaited_once()
    command = submit.await_args.args[0]
    assert (command.principal_id, command.workspace_id, command.thread_id) == (
        scope.principal,
        scope.workspace,
        scope.thread,
    )
    assert command.parts == [{"type": "text", "text": UTTERANCE}]
    assert command.execution_context.extra_data == {"voice_delegate": True}
    assert command.execution_context.no_workspace_writes is False
    if profile:
        authorize_profile.assert_awaited_with(scope.workspace, scope.principal, profile)
    else:
        authorize_profile.assert_not_awaited()


@pytest.mark.parametrize("deny_workspace", [True, False])
async def test_does_not_delegate_without_current_workspace_or_profile_authority(
    deny_workspace, monkeypatch
):
    from app.services import chat_turn_submissions, native_voice

    monkeypatch.setattr(
        native_voice,
        "can_access_workspace",
        AsyncMock(return_value="guest" if deny_workspace else "member"),
    )
    require_profile = AsyncMock(
        side_effect=InsufficientPermissionsError("Profile unavailable")
    )
    monkeypatch.setattr(native_voice, "require_native_chat_profile", require_profile)
    submit = AsyncMock()
    monkeypatch.setattr(chat_turn_submissions, "submit_chat_turn", submit)
    with pytest.raises(InsufficientPermissionsError):
        await native_voice.delegate(
            VoiceScope(
                "fixture-person",
                "fixture-workspace",
                "restricted-profile",
                "fixture-thread",
            ),
            "fixture-call",
            UTTERANCE,
        )
    submit.assert_not_awaited()
    assert require_profile.await_count == (0 if deny_workspace else 1)


@pytest.mark.smoke
async def test_voice_and_typed_use_same_heavy_dashboard_broker(monkeypatch):
    """Reach actual broker construction without model I/O, tool effects or approval."""
    from app.agentive.services import agent_skills, execution_runs, profile_management
    from app.services import (
        chat_page_context,
        chat_turn_attachments,
        credential_crypto,
        user_timezone,
    )
    from app.services.chat_providers import pydantic_ai_provider as native

    class ObservedBroker(Exception):
        pass

    provider = native.PydanticAIProvider()
    thread = SimpleNamespace(
        id="thread", workspace_id="workspace", active_harness_session_id=None
    )
    monkeypatch.setattr(
        provider, "_validate_chat_scope", AsyncMock(return_value=(thread, "member"))
    )
    light = AsyncMock(return_value=None)
    monkeypatch.setattr(provider, "_try_light_turn", light)
    heavy = AsyncMock(return_value=object())
    monkeypatch.setattr(provider, "_build_turn_model", heavy)
    monkeypatch.setattr(credential_crypto, "encryption_available", lambda: True)
    monkeypatch.setattr(
        profile_management, "workspace_assistant_persona", AsyncMock(return_value="")
    )
    monkeypatch.setattr(
        "app.agentive.harness.connector_tools.build_connector_tool_catalogue",
        AsyncMock(return_value=[]),
    )
    # Supply an empty overlay; the real workspace/global dashboard catalogue is
    # still assembled and receives the broker's normal declaration gate.
    monkeypatch.setattr(
        "app.agentive.workspace_agent_profile.compose_workspace_agent_profile",
        AsyncMock(
            return_value=SimpleNamespace(overlay_skill_docs=(), profile_version="test")
        ),
    )
    monkeypatch.setattr(agent_skills, "list_core_skills", lambda: [])
    monkeypatch.setattr(
        execution_runs.AgentRun,
        "find_one",
        AsyncMock(
            return_value=SimpleNamespace(
                run_id="run",
                thread_id="thread",
                user_id="person",
                workspace_id="workspace",
                provider_id=provider.id,
                status="running",
                capability_snapshot={},
                capability_version="test",
            )
        ),
    )
    monkeypatch.setattr(native, "ensure_harness_session", AsyncMock())
    monkeypatch.setattr(native, "_retained_file_context", AsyncMock(return_value=""))
    monkeypatch.setattr(
        chat_turn_attachments,
        "current_chat_attachment_content",
        AsyncMock(return_value=[]),
    )
    monkeypatch.setattr(native, "user_identity_context", AsyncMock(return_value=""))
    monkeypatch.setattr(native, "workspace_orientation", AsyncMock(return_value=""))
    monkeypatch.setattr(
        chat_page_context, "page_context_target_hint", AsyncMock(return_value="")
    )
    monkeypatch.setattr(user_timezone, "user_time_context", AsyncMock(return_value=""))
    real_broker = native.build_brokered_tools
    brokered = []

    def record_tools(**kwargs):
        names = {tool.name for tool in real_broker(**kwargs)}
        brokered.append((kwargs, names))
        raise ObservedBroker

    monkeypatch.setattr(native, "build_brokered_tools", record_tools)
    for voice in (False, True):
        turn = ChatTurnContext(
            user_id="person",
            user_email="",
            session_id=None,
            text=UTTERANCE,
            thread_id="thread",
            workspace_id="workspace",
            extra_data={"run_id": "run", **({"voice_delegate": True} if voice else {})},
        )
        with pytest.raises(ObservedBroker):
            await provider._prepare_current(turn)
    light.assert_awaited_once()  # typed may try light; realtime goes straight to heavy
    assert heavy.await_count == 2
    for call in heavy.await_args_list:
        assert call.kwargs.get("model_role", "heavy") == "heavy"
    for broker, names in brokered:
        assert broker["conversation_id"] == "thread"
        assert broker["scope"].principal_id == "person"
        assert broker["scope"].workspace_id == "workspace"
        assert broker["no_workspace_writes"] is False
        assert "integral_create_dashboard" in names
        assert "integral_resolve_pending_write" not in names
    assert brokered[0][1] == brokered[1][1]


async def test_live_navigation_delivery_is_exact_and_not_replayed(monkeypatch):
    """Only a successful current-call result can move the bound browser."""
    from app.schemas.agentive.chat_events import ChatEventReplayPage
    from app.services import agent_navigation, chat_turn_events, native_voice
    from tests.test_native_voice import Browser, Provider

    scope = VoiceScope("person", "workspace", "", "thread")
    scope.page_context = {"url": "/agent", "route_path": "/agent"}
    destination = {
        "type": "navigation",
        "path": "/apps/n.WorkspaceApp.fixture",
        "thread_id": "thread",
        "workspace_id": "workspace",
    }
    replay = AsyncMock(
        return_value=ChatEventReplayPage(events=[destination], work_status="succeeded")
    )
    monkeypatch.setattr(chat_turn_events, "replay_work_item_chat_events", replay)
    monkeypatch.setattr(
        agent_navigation, "verify_voice_navigation", AsyncMock(return_value=True)
    )
    browser = Browser()
    relay = native_voice.VoiceRelay(browser, Provider(), scope, check=AsyncMock())
    accepted = native_voice._AcceptedVoiceTurn(
        "work",
        "message",
        "Open app",
        browser_origin="/agent",
        voice_generation=relay.generation,
    )
    import asyncio

    delivery = asyncio.create_task(relay._deliver_navigation(accepted))
    event = await asyncio.wait_for(browser.outbox.get(), 1)
    assert event == {
        "type": "navigation",
        "navigation": destination,
        "id": event["id"],
        "origin_revision": 0,
    }
    await relay._navigation_result(
        __import__("json").dumps(
            {"type": "navigation_result", "id": "stale", "status": "rendered"}
        )
    )
    assert not delivery.done()
    scope.page_context = {"url": destination["path"], "route_path": destination["path"]}
    await relay._navigation_result(
        __import__("json").dumps(
            {"type": "navigation_result", "id": event["id"], "status": "rendered"}
        )
    )
    assert await delivery == "The requested page is open."
    await relay._deliver_navigation(accepted)
    assert browser.outbox.empty()
    scope.page_revision += 1
    from dataclasses import replace

    await relay._deliver_navigation(replace(accepted, work_item_id="other"))
    assert browser.outbox.empty()
    replay.assert_awaited_once()
