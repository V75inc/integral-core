"""Exercise voice separation and explicit stopped rerun with a real chat WorkItem."""

import asyncio
import json
import os

import pytest

from app.agentive.services.work_items import claim_due_candidate
from app.agentive.work_models import WorkItem
from app.services import native_voice
from app.services.chat_threads import create_thread
from tests.test_native_voice import Browser, Provider, event, request

pytestmark = [pytest.mark.contract, pytest.mark.postgres, pytest.mark.asyncio]


@pytest.mark.parametrize("dispatch", [False, True], ids=["queued", "running"])
async def test_live_voice_and_explicit_stop_rerun(
    authenticated_client, test_user, monkeypatch, dispatch
):
    """A provider function closes while Heavy runs; only queued Stop is replayable."""
    if os.getenv("INTEGRAL_TEST_DB") not in ("postgres", "postgresql"):
        pytest.skip("Detached voice WorkItems require PostgreSQL")
    monkeypatch.setenv("INTEGRAL_CREDENTIAL_ENC_KEY", "29" * 32)
    workspace = await authenticated_client.post(
        "/api/workspaces", json={"name": "Isolated voice test"}
    )
    assert workspace.status_code == 200
    ws = workspace.json()["workspace"]["id"]
    me = test_user.user_id
    thread = await create_thread(
        user_id=me, workspace_id=ws, provider_id="integral_native"
    )
    scope = native_voice.VoiceScope(me, ws, "", thread.id)
    browser, provider = Browser(), Provider()
    relay = native_voice.VoiceRelay(browser, provider, scope)
    task = asyncio.create_task(relay.run())

    async def speech():
        await provider.inbox.put({"type": "input_audio_buffer.speech_started"})
        assert await event(browser.outbox) == {"type": "flush"}

    async def tool(turn, *, operation=None):
        await provider.inbox.put({"type": "response.created", "response": {"id": turn}})
        message = request(
            turn,
            turn,
            "Describe Integral" if operation is None else "Please run it again",
        )
        if operation is not None:
            message["response"]["output"][0]["arguments"] = json.dumps(
                {"request": "Please run it again", "operation": operation}
            )
        await provider.inbox.put(message)
        result = await event(provider.outbox)
        assert result["item"]["call_id"] == turn
        return result["item"]["output"]

    async def read(item_id):
        return await WorkItem.get(f"o.WorkItem.{item_id}")

    async def stop(item_id):
        return await authenticated_client.post(
            f"/api/chat/threads/{thread.id}/work-items/{item_id}/cancel",
            headers={"X-Integral-Scope": f"ws:{ws}"},
        )

    try:
        await event(provider.outbox)
        await provider.inbox.put({"type": "session.updated"})
        assert (await event(browser.outbox))["thread_id"] == thread.id
        assert "accepted" in await tool("initial")
        first = relay.accepted
        assert first is not None
        original = await read(first.work_item_id)
        assert original.status == "queued" and not original.cancel_requested_at
        if dispatch:
            claimed = await claim_due_candidate(
                worker_id="synthetic-test-worker",
                lease_seconds=60,
                work_item_id=first.work_item_id,
            )
            assert claimed is not None and claimed.status == "running"

        await speech()
        await provider.inbox.put(
            {"type": "response.created", "response": {"id": "small-talk"}}
        )
        await provider.inbox.put(
            {
                "type": "response.output_audio.delta",
                "response_id": "small-talk",
                "delta": "AAA=",
            }
        )
        assert await event(browser.outbox) == b"\x00\x00"
        await provider.inbox.put(
            {
                "type": "response.done",
                "response": {"id": "small-talk", "status": "completed"},
            }
        )
        after = await read(first.work_item_id)
        assert after.status == ("running" if dispatch else "queued")
        assert not after.cancel_requested_at
        assert relay.accepted == first

        await speech()
        assert "still active" in await tool("while-active", operation="rerun_stopped")
        assert (await event(provider.outbox))["type"] == "response.create"
        assert relay.accepted == first
        stop_response = await stop(first.work_item_id)
        assert stop_response.status_code == 200, stop_response.text
        stopped = await read(first.work_item_id)
        assert stopped.cancel_requested_at
        await speech()
        if dispatch:
            assert stopped.status == "running"
            assert "still active or stopping" in await tool(
                "while-stopping", operation="rerun_stopped"
            )
            assert relay.accepted == first
        else:
            assert stopped.status == "cancelled"
            assert "accepted" in await tool("explicit-rerun", operation="rerun_stopped")
            rerun = relay.accepted
            assert rerun is not None and rerun.work_item_id != first.work_item_id
            assert rerun.text == first.text
            new = await read(rerun.work_item_id)
            assert new.status == "queued" and not new.cancel_requested_at
            assert (await read(first.work_item_id)).status == "cancelled"
            await provider.inbox.put(
                {"type": "response.created", "response": {"id": "replayed"}}
            )
            replay = request("explicit-rerun", "replayed", "Please run it again")
            replay["response"]["output"][0]["arguments"] = json.dumps(
                {"request": "Please run it again", "operation": "rerun_stopped"}
            )
            await provider.inbox.put(replay)
            await speech()
            assert provider.outbox.empty()
            assert relay.accepted == rerun
    finally:
        await browser.inbox.put({"type": "websocket.disconnect"})
        await asyncio.wait_for(task, 3)


async def test_durable_voice_completion_after_conversation_pause(
    authenticated_client, test_user, monkeypatch
):
    """Persist fenced native chat output, then observe it through Realtime once."""
    from app.agentive.services import work_execution, work_items
    from app.services.chat_turn_transcript import persist_work_item_assistant_result

    if os.getenv("INTEGRAL_TEST_DB") not in ("postgres", "postgresql"):
        pytest.skip("Detached voice WorkItems require PostgreSQL")
    monkeypatch.setenv("INTEGRAL_CREDENTIAL_ENC_KEY", "29" * 32)
    monkeypatch.setattr(native_voice, "RESULT_IDLE_SECONDS", 0.01)
    monkeypatch.setattr(native_voice, "RESULT_POLL_SECONDS", 0.02)
    response = await authenticated_client.post(
        "/api/workspaces", json={"name": "Isolated voice completion"}
    )
    assert response.status_code == 200
    ws = response.json()["workspace"]["id"]
    me = test_user.user_id
    thread = await create_thread(
        user_id=me, workspace_id=ws, provider_id="integral_native"
    )
    browser, provider = Browser(), Provider()
    relay = native_voice.VoiceRelay(
        browser, provider, native_voice.VoiceScope(me, ws, "", thread.id)
    )
    task = asyncio.create_task(relay.run())
    try:
        await event(provider.outbox)
        await provider.inbox.put({"type": "session.updated"})
        assert (await event(browser.outbox))["thread_id"] == thread.id
        await provider.inbox.put(
            {"type": "response.created", "response": {"id": "delegate"}}
        )
        await provider.inbox.put(request("call", "delegate", "Explain Integral"))
        assert "accepted" in (await event(provider.outbox))["item"]["output"]
        accepted = relay.accepted
        assert accepted is not None
        item = await claim_due_candidate(
            worker_id="synthetic-completion-worker",
            lease_seconds=60,
            work_item_id=accepted.work_item_id,
        )
        assert item is not None and item.status == "running"
        await provider.inbox.put({"type": "input_audio_buffer.speech_started"})
        assert await event(browser.outbox) == {"type": "flush"}
        ctx = work_execution.build_work_execution_context(
            work_item=item,
            logical_step_key=work_execution.logical_step_key_for(kind="chat_turn"),
        )
        final = "Integral organizes work in your workspace."
        msg = await persist_work_item_assistant_result(
            context=ctx,
            parts=[{"type": "text", "text": final}],
            provider_metadata={},
        )
        assert msg.thread_id == thread.id and msg.parent_id == accepted.message_id
        transitioned = await work_items.transition_leased(
            item.work_item_id,
            lease_token=item.lease_token,
            lease_fence=int(item.lease_fence or 0),
            expected_status="running",
            target="succeeded",
        )
        assert transitioned.status == "succeeded"
        with pytest.raises(TimeoutError):
            await asyncio.wait_for(provider.outbox.get(), 0.05)
        await provider.inbox.put({"type": "input_audio_buffer.speech_stopped"})
        await provider.inbox.put(
            {"type": "response.created", "response": {"id": "ordinary"}}
        )
        await provider.inbox.put(
            {
                "type": "response.done",
                "response": {"id": "ordinary", "status": "completed"},
            }
        )
        rendered = (await event(provider.outbox))["response"]
        assert final in rendered["instructions"]
        await provider.inbox.put(
            {
                "type": "response.created",
                "response": {"id": "completion", "metadata": rendered["metadata"]},
            }
        )
        await provider.inbox.put(
            {
                "type": "response.output_audio.delta",
                "response_id": "completion",
                "delta": "AAE=",
            }
        )
        assert await event(browser.outbox) == b"\x00\x01"
        await provider.inbox.put(
            {
                "type": "response.done",
                "response": {"id": "completion", "status": "completed"},
            }
        )
        with pytest.raises(TimeoutError):
            await asyncio.wait_for(provider.outbox.get(), 0.05)
    finally:
        await browser.inbox.put({"type": "websocket.disconnect"})
        await asyncio.wait_for(task, 3)


async def test_realtime_tool_uses_canonical_queue_and_voice_steering(
    authenticated_client, test_user, monkeypatch
):
    """One admitted spoken turn receives a separate queued job and a correction."""
    from app.services.chat_followups import list_chat_follow_ups
    from tests.test_native_voice_completion import _send_tool
    from tests.test_native_voice_live_tools import _tool

    if os.getenv("INTEGRAL_TEST_DB") not in ("postgres", "postgresql"):
        pytest.skip("Detached voice WorkItems require PostgreSQL")
    monkeypatch.setenv("INTEGRAL_CREDENTIAL_ENC_KEY", "29" * 32)
    response = await authenticated_client.post(
        "/api/workspaces", json={"name": "Isolated spoken follow-up"}
    )
    assert response.status_code == 200
    ws = response.json()["workspace"]["id"]
    me = test_user.user_id
    thread = await create_thread(
        user_id=me, workspace_id=ws, provider_id="integral_native"
    )
    browser, provider = Browser(), Provider()
    relay = native_voice.VoiceRelay(
        browser, provider, native_voice.VoiceScope(me, ws, "", thread.id)
    )
    task = asyncio.create_task(relay.run())
    try:
        await event(provider.outbox)
        await provider.inbox.put({"type": "session.updated"})
        assert (await event(browser.outbox))["thread_id"] == thread.id
        await _send_tool(provider, "delegate", "delegate")
        assert "accepted" in (await event(provider.outbox))["item"]["output"]
        accepted = relay.accepted
        assert accepted is not None
        for operation, tool_name, text, expected in (
            (
                "queue",
                native_voice.QUEUE_FOLLOW_UP,
                "Then summarize my projects.",
                "queued",
            ),
            (
                "steer",
                native_voice.STEER_ACTIVE,
                "For that task, use only March.",
                "has not confirmed",
            ),
        ):
            await _tool(provider, operation, tool_name, {"request": text})
            closure = await event(provider.outbox)
            assert closure["item"]["call_id"] == operation
            assert expected in closure["item"]["output"]
            assert (await event(provider.outbox))["response"]["tool_choice"] == "none"
        [queued] = await list_chat_follow_ups(
            principal_id=me, workspace_id=ws, thread_id=thread.id
        )
        assert queued.after_work_item_id == accepted.work_item_id
        active = await WorkItem.get(f"o.WorkItem.{accepted.work_item_id}")
        assert active.status == "queued" and not active.cancel_requested_at
        assert relay.accepted == accepted
    finally:
        await browser.inbox.put({"type": "websocket.disconnect"})
        await asyncio.wait_for(task, 3)


async def test_queued_voice_successor_is_checked_and_announced_once(
    authenticated_client, test_user, monkeypatch
):
    """Establish actual queue promotion and fenced successor completion."""
    from app.agentive.services import work_execution, work_items
    from app.services.chat_followups import promote_next_chat_follow_up
    from app.services.chat_turn_transcript import persist_work_item_assistant_result
    from tests.test_native_voice_completion import _send_tool
    from tests.test_native_voice_live_tools import _tool

    if os.getenv("INTEGRAL_TEST_DB") not in ("postgres", "postgresql"):
        pytest.skip("Detached voice WorkItems require PostgreSQL")
    monkeypatch.setenv("INTEGRAL_CREDENTIAL_ENC_KEY", "29" * 32)
    monkeypatch.setattr(native_voice, "RESULT_IDLE_SECONDS", 0.01)
    monkeypatch.setattr(native_voice, "RESULT_POLL_SECONDS", 0.02)
    response = await authenticated_client.post(
        "/api/workspaces", json={"name": "Isolated queued voice completion"}
    )
    assert response.status_code == 200
    ws = response.json()["workspace"]["id"]
    me = test_user.user_id
    thread = await create_thread(
        user_id=me, workspace_id=ws, provider_id="integral_native"
    )
    browser, provider = Browser(), Provider()
    relay = native_voice.VoiceRelay(
        browser, provider, native_voice.VoiceScope(me, ws, "", thread.id)
    )
    task = asyncio.create_task(relay.run())

    async def settle(item_id, text):
        item = await claim_due_candidate(
            worker_id="synthetic-successor-worker",
            lease_seconds=60,
            work_item_id=item_id,
        )
        assert item is not None and item.status == "running"
        ctx = work_execution.build_work_execution_context(
            work_item=item,
            logical_step_key=work_execution.logical_step_key_for(kind="chat_turn"),
        )
        await persist_work_item_assistant_result(
            context=ctx, parts=[{"type": "text", "text": text}], provider_metadata={}
        )
        return await work_items.transition_leased(
            item_id,
            lease_token=item.lease_token,
            lease_fence=int(item.lease_fence or 0),
            expected_status="running",
            target="succeeded",
        )

    async def finished_audio(name, response):
        await provider.inbox.put(
            {
                "type": "response.created",
                "response": {"id": name, "metadata": response["metadata"]},
            }
        )
        await provider.inbox.put(
            {
                "type": "response.output_audio.delta",
                "response_id": name,
                "delta": "AAE=",
            }
        )
        assert await event(browser.outbox) == b"\x00\x01"
        await provider.inbox.put(
            {"type": "response.done", "response": {"id": name, "status": "completed"}}
        )

    try:
        await event(provider.outbox)
        await provider.inbox.put({"type": "session.updated"})
        assert (await event(browser.outbox))["thread_id"] == thread.id
        await _send_tool(provider, "delegate", "delegate")
        assert "accepted" in (await event(provider.outbox))["item"]["output"]
        first = relay.accepted
        assert first is not None
        await _send_tool(provider, "add-queued", "add-queued", "queue")
        assert (
            "queued that as a separate"
            in (await event(provider.outbox))["item"]["output"]
        )
        await finished_audio("ack", (await event(provider.outbox))["response"])

        await _send_tool(provider, "check", "check", "check_chat")
        assert "Checked" in (await event(provider.outbox))["item"]["output"]
        check = (await event(provider.outbox))["response"]
        assert "latest follow-up is still in the queue" in check["instructions"]
        await finished_audio("check-result", check)

        earlier = "This is the earlier work result."
        assert (await settle(first.work_item_id, earlier)).status == "succeeded"
        readout = (await event(provider.outbox))["response"]
        assert earlier in readout["instructions"]
        await finished_audio("earlier-result", readout)
        promoted = await promote_next_chat_follow_up({})
        assert promoted is not None and promoted.work_item_id != first.work_item_id

        # No stale predecessor result when the latest admitted job is asserted.
        await _tool(
            provider, "check-next", native_voice.CHECK_CHAT, {"view": "latest_chat"}
        )
        await event(provider.outbox)
        check = (await event(provider.outbox))["response"]
        assert "latest follow-up is queued to start" in check["instructions"]
        assert earlier not in check["instructions"]
        await finished_audio("check-next-result", check)

        final = "The separately queued summary finished."
        assert (await settle(promoted.work_item_id, final)).status == "succeeded"
        observed = (await event(provider.outbox))["response"]
        assert final in observed["instructions"]
        assert "outcome of the separately queued follow-up" in observed["instructions"]
        await finished_audio("successor-result", observed)
        with pytest.raises(TimeoutError):
            await asyncio.wait_for(provider.outbox.get(), 0.1)
    finally:
        await browser.inbox.put({"type": "websocket.disconnect"})
        await asyncio.wait_for(task, 3)


async def test_provider_discovers_and_uses_real_scoped_catalogue_reads(
    authenticated_client, test_user, monkeypatch
):
    """Both generic provider stages reach the normal app/workspace permission gates."""
    from tests.test_native_voice_live_tools import _quick_read

    if os.getenv("INTEGRAL_TEST_DB") not in ("postgres", "postgresql"):
        pytest.skip("Voice catalogue read qualification requires PostgreSQL")
    monkeypatch.setenv("INTEGRAL_CREDENTIAL_ENC_KEY", "29" * 32)
    workspaces = []
    for name in ("Spoken Operations", "Other Branch"):
        response = await authenticated_client.post(
            "/api/workspaces", json={"name": name}
        )
        assert response.status_code == 200
        workspaces.append(response.json()["workspace"]["id"])
    ws, other = workspaces
    for scope, name in ((ws, "Visible Contracts"), (other, "Other Payroll")):
        created = await authenticated_client.post(
            "/api/apps",
            headers={"X-Integral-Scope": f"ws:{scope}"},
            json={"name": name, "visibility": "private"},
        )
        assert created.status_code == 200, created.text
    thread = await create_thread(
        user_id=test_user.user_id, workspace_id=ws, provider_id="integral_native"
    )
    browser, provider = Browser(), Provider()
    relay = native_voice.VoiceRelay(
        browser,
        provider,
        native_voice.VoiceScope(test_user.user_id, ws, "", thread.id),
    )
    task = asyncio.create_task(relay.run())
    try:
        await event(provider.outbox)
        await provider.inbox.put({"type": "session.updated"})
        assert (await event(browser.outbox))["thread_id"] == thread.id
        apps = await _quick_read(provider, "apps", "show apps", "Visible apps here")
        assert "Visible Contracts" in apps and "Other Payroll" not in apps
        workspaces = await _quick_read(
            provider, "workspaces", "current workspace", "Accessible workspaces"
        )
        assert "The current workspace is Spoken Operations." in workspaces
        assert "Other Branch" in workspaces
        assert all(
            identifier not in apps + workspaces for identifier in (ws, other, thread.id)
        )
        assert relay.accepted is None
    finally:
        await browser.inbox.put({"type": "websocket.disconnect"})
        await asyncio.wait_for(task, 3)
