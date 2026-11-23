"""Provider protocol checks for explicit live voice tools."""

import asyncio
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.services import (
    chat_turn_submissions,
    native_voice,
    native_voice_followups,
    permissions,
    workspace_permissions,
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
    apps = AsyncMock(
        return_value=[
            SimpleNamespace(name="Visible Projects", workspace_id=scope.workspace),
            SimpleNamespace(name="Outside Finance", workspace_id="other"),
            SimpleNamespace(
                name="Archived Audit",
                workspace_id=scope.workspace,
                lifecycle_state="archived",
            ),
        ]
    )
    volumes = AsyncMock(
        return_value=[
            SimpleNamespace(name="Operations", id=scope.workspace),
            SimpleNamespace(name="Allowed Workspace", id="other-allowed"),
        ]
    )
    monkeypatch.setattr(permissions, "get_user_accessible_apps_for_navigation", apps)
    monkeypatch.setattr(workspace_permissions, "list_accessible_workspaces", volumes)
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
            native_voice.LIST_CONTEXT,
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

        await _tool(provider, "apps", native_voice.LIST_CONTEXT, {"view": "apps_here"})
        await event(provider.outbox)
        speech = (await event(provider.outbox))["response"]["instructions"]
        assert "Visible Projects" in speech
        assert "Outside Finance" not in speech and "Archived Audit" not in speech
        apps.assert_awaited_once_with(scope.principal, workspace_id=scope.workspace)

        await _tool(
            provider, "workspaces", native_voice.LIST_CONTEXT, {"view": "workspaces"}
        )
        await event(provider.outbox)
        speech = (await event(provider.outbox))["response"]["instructions"]
        assert "Operations" in speech and "Allowed Workspace" in speech
        volumes.assert_awaited_once_with(scope.principal)

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
    monkeypatch.setattr(workspace_permissions, "list_accessible_workspaces", lookup)
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
            provider, "workspaces", native_voice.LIST_CONTEXT, {"view": "workspaces"}
        )
        await event(provider.outbox)
        answer = (await event(provider.outbox))["response"]["instructions"]
        assert "selected agent profile cannot list" in answer
        lookup.assert_not_awaited()
    finally:
        await browser.inbox.put({"type": "websocket.disconnect"})
        await task
