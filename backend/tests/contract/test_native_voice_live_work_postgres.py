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
from tests.test_native_voice_completion import completed_readout

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
        rendered = (await completed_readout(provider, final))["response"]
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
        readout = (await completed_readout(provider, earlier))["response"]
        assert earlier in readout["instructions"]
        await finished_audio("earlier-result", readout)
        promoted = await promote_next_chat_follow_up({})
        assert promoted is not None and promoted.work_item_id != first.work_item_id

        # Saved chat text and the status of the latest admitted job are distinct.
        await _tool(
            provider, "check-visible", native_voice.CHECK_CHAT, {"view": "latest_reply"}
        )
        await event(provider.outbox)
        visible = (await event(provider.outbox))["response"]
        assert "Latest saved reply: " + earlier in visible["instructions"]
        assert earlier in visible["instructions"]
        await finished_audio("check-visible-result", visible)

        # Status should not fall back to that earlier result.
        await _tool(
            provider, "check-next", native_voice.CHECK_CHAT, {"view": "latest_task"}
        )
        await event(provider.outbox)
        check = (await event(provider.outbox))["response"]
        assert "latest follow-up is queued to start" in check["instructions"]
        assert earlier not in check["instructions"]
        await finished_audio("check-next-result", check)

        final = "The separately queued summary finished."
        assert (await settle(promoted.work_item_id, final)).status == "succeeded"
        observed = (await completed_readout(provider, final))["response"]
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
        assert '"current_workspace": "Spoken Operations"' in workspaces
        assert "Other Branch" in workspaces
        assert all(
            identifier not in apps + workspaces for identifier in (ws, other, thread.id)
        )
        assert relay.accepted is None
    finally:
        await browser.inbox.put({"type": "websocket.disconnect"})
        await asyncio.wait_for(task, 3)


async def test_spoken_navigation_context_and_cancellation(
    authenticated_client, test_user, monkeypatch
):
    """Exercise the actual voice relay, scoped list and durable submission."""
    from tests.test_native_voice_live_tools import _tool

    if os.getenv("INTEGRAL_TEST_DB") not in ("postgres", "postgresql"):
        pytest.skip("Voice navigation qualification requires PostgreSQL")
    monkeypatch.setenv("INTEGRAL_CREDENTIAL_ENC_KEY", "29" * 32)
    workspace = await authenticated_client.post(
        "/api/workspaces", json={"name": "Voice location test"}
    )
    assert workspace.status_code == 200
    ws = workspace.json()["workspace"]["id"]
    headers = {"X-Integral-Scope": f"ws:{ws}"}
    created = await authenticated_client.post(
        "/api/apps",
        headers=headers,
        json={"name": "Voice target", "visibility": "private"},
    )
    assert created.status_code == 200, created.text
    app_id = created.json()["app"]["id"]
    track = await authenticated_client.post(
        "/api/tracks",
        headers=headers,
        json={"title": "Voice collection", "app_id": app_id, "visibility": "private"},
    )
    assert track.status_code in (200, 201), track.text
    track_id = track.json()["track"]["id"]
    saved_view = await authenticated_client.post(
        f"/api/tracks/{track_id}/views",
        headers=headers,
        json={"name": "Voice saved view", "type": "feed"},
    )
    assert saved_view.status_code == 200, saved_view.text
    for title in ("Acme Georgetown", "Acme Linden"):
        entry = await authenticated_client.post(
            "/api/entries",
            headers=headers,
            json={"track_id": track_id, "title": title, "body": "Fixture only"},
        )
        assert entry.status_code in (200, 201), entry.text
    thread = await create_thread(
        user_id=test_user.user_id, workspace_id=ws, provider_id="integral_native"
    )
    scope = native_voice.VoiceScope(test_user.user_id, ws, "", thread.id)
    browser, provider = Browser(), Provider()
    relay = native_voice.VoiceRelay(browser, provider, scope)
    task = asyncio.create_task(relay.run())

    async def navigate(identifier, action, ordinal=0):
        await _tool(
            provider,
            identifier,
            native_voice.NAVIGATE,
            {"action": action, "ordinal": ordinal},
        )
        # The scoped read has its own two-second deadline before the relay
        # publishes its result. Give the test transport time to deliver it.
        first = await asyncio.wait_for(provider.outbox.get(), 6)
        assert first["item"]["call_id"] == identifier
        response = (await event(provider.outbox))["response"]
        assert response["conversation"] == "none"
        return response["instructions"]

    try:
        await event(provider.outbox)
        await provider.inbox.put({"type": "session.updated"})
        assert (await event(browser.outbox))["thread_id"] == thread.id
        assert '"Voice target"' in await navigate("list", "apps")
        assert "Selected app Voice target" in await navigate("select", "select", 1)
        assert relay.navigation.focus.label == "Voice target"
        assert "Voice collection" in await navigate("tracks", "tracks")
        track_ordinal = next(
            i
            for i, candidate in enumerate(relay.navigation.page.destinations, 1)
            if candidate.label == "Voice collection"
        )
        assert "Selected track Voice collection" in await navigate(
            "track", "select", track_ordinal
        )
        assert "Voice saved view" in await navigate("views", "views")
        view_ordinal = next(
            i
            for i, candidate in enumerate(relay.navigation.page.destinations, 1)
            if candidate.label == "Voice saved view"
        )
        assert "Selected view Voice saved view" in await navigate(
            "view", "select", view_ordinal
        )
        await _tool(provider, "records", native_voice.FIND_RECORDS, {"query": "Acme"})
        assert (await event(provider.outbox))["item"]["call_id"] == "records"
        response = (await event(provider.outbox))["response"]["instructions"]
        assert "Acme Georgetown" in response and "Acme Linden" in response
        ordinal = next(
            i
            for i, candidate in enumerate(relay.navigation.page.destinations, 1)
            if candidate.label == "Acme Georgetown"
        )
        assert "Selected entry Acme Georgetown" in await navigate(
            "entry", "select", ordinal
        )
        dest = relay.navigation.focus
        assert dest.id not in await navigate("where", "where")
        await _tool(
            provider, "work", native_voice.DELEGATE, {"request": "Describe this record"}
        )
        assert "accepted" in (await event(provider.outbox))["item"]["output"]
        accepted = relay.accepted
        assert accepted is not None
        item = await WorkItem.get(f"o.WorkItem.{accepted.work_item_id}")
        from app.agentive.harness.turn_input import load_turn_input_capsule

        capsule = await load_turn_input_capsule(
            principal_id=scope.principal,
            workspace_id=ws,
            thread_id=thread.id,
            work_item_id=item.work_item_id,
            capsule_id=item.input_payload["capsule_id"],
            expected_digest=item.input_payload["capsule_digest"],
        )
        assert capsule.execution_context.focused_track_id == track_id
        assert dest.id in capsule.execution_context.system_context
        assert capsule.execution_context.extra_data["voice_delegate"] is True
        await _tool(provider, "cancel", native_voice.CANCEL_WORK, {})
        assert (await event(provider.outbox))["item"]["call_id"] == "cancel"
        result = (await event(provider.outbox))["response"]["instructions"]
        assert "task is cancelled" in result
        current = await WorkItem.find_one(
            {"context.work_item_id": accepted.work_item_id}
        )
        assert current.status == "cancelled"
        assert scope.workspace == ws and scope.profile == ""
    finally:
        await browser.inbox.put({"type": "websocket.disconnect"})
        await asyncio.wait_for(task, 3)


async def test_spoken_review_of_canonical_entry_proposal(
    authenticated_client, test_user
):
    """The spoken read uses current durable staging and record revisions."""
    from app.agentive.services.proposal_origin import bind_proposal_origin
    from app.agentive.staging import create_staged_change, revoke_token
    from app.services.native_voice_review import read_entry_review

    if os.getenv("INTEGRAL_TEST_DB") not in ("postgres", "postgresql"):
        pytest.skip("Spoken review qualification requires PostgreSQL")
    response = await authenticated_client.post(
        "/api/workspaces", json={"name": "Voice review test"}
    )
    assert response.status_code == 200
    ws = response.json()["workspace"]["id"]
    headers = {"X-Integral-Scope": f"ws:{ws}"}
    track = await authenticated_client.post(
        "/api/tracks",
        headers=headers,
        json={"title": "Contracts", "visibility": "private"},
    )
    assert track.status_code in (200, 201), track.text
    track_id = track.json()["track"]["id"]
    record = await authenticated_client.post(
        "/api/entries", headers=headers, json={"track_id": track_id, "title": "Acme"}
    )
    assert record.status_code in (200, 201), record.text
    entry = record.json()["entry"]
    thread = await create_thread(
        user_id=test_user.user_id, workspace_id=ws, provider_id="integral_native"
    )
    args = dict(
        principal=test_user.user_id,
        workspace=ws,
        thread=thread.id,
        work_id="fixture-work",
        run_id="fixture-run",
    )
    payload = {
        "entry_id": entry["id"],
        "title": "Acme Georgetown",
        "expected_record_revision": entry["record_revision"],
    }
    proof = {
        "version": "work-proposal-v1",
        "capability_key": "integral_update_entry",
        "work_item_id": args["work_id"],
        "run_id": args["run_id"],
        "principal_id": args["principal"],
        "workspace_id": ws,
        "thread_id": thread.id,
    }
    with bind_proposal_origin(proof):
        staged = await create_staged_change(
            user_id=args["principal"],
            workspace_id=ws,
            session_id=thread.id,
            kind="update_entry",
            summary="Update Acme",
            diff_human="Title: Acme to Acme Georgetown",
            diff_machine=payload,
            payload=payload,
        )
    initial = await read_entry_review(**args)
    assert initial is not None and "Acme Georgetown" in initial.text
    assert staged.token not in initial.text and entry["id"] not in initial.text
    await revoke_token(user_id=args["principal"], token=staged.token)
    assert await read_entry_review(**args) is None


@pytest.mark.parametrize("edit", ["title", "date", "reject", "create"])
async def test_voice_confirmation_uses_real_staging_decision_and_saved_value(
    authenticated_client, test_user, monkeypatch, edit
):
    """A synthetic independent recognition can apply exactly one real record edit."""
    from app.agentive.services import work_approvals
    from app.agentive.services.proposal_origin import bind_proposal_origin
    from app.agentive.services.speech import service
    from app.agentive.staging import create_staged_change
    from app.schemas.agentive.work import (
        ChatTurnExecutionContext,
        ChatTurnSubmissionRequest,
    )
    from app.services.chat_turn_submissions import submit_chat_turn
    from app.services.native_voice_confirmation import VoiceConfirmation
    from app.services.native_voice_navigation import VoiceDestination

    if os.getenv("INTEGRAL_TEST_DB") not in ("postgres", "postgresql"):
        pytest.skip("Spoken confirmation qualification requires PostgreSQL")
    monkeypatch.setenv("INTEGRAL_CREDENTIAL_ENC_KEY", "29" * 32)
    response = await authenticated_client.post(
        "/api/workspaces", json={"name": "Voice confirmation test"}
    )
    assert response.status_code == 200
    ws = response.json()["workspace"]["id"]
    me = test_user.user_id
    headers = {"X-Integral-Scope": f"ws:{ws}"}
    track = await authenticated_client.post(
        "/api/tracks",
        headers=headers,
        json={"title": "Contracts", "visibility": "private"},
    )
    assert track.status_code in (200, 201), track.text
    track_id = track.json()["track"]["id"]
    record_input = {"track_id": track_id, "title": "Acme"}
    date_key, date_label = (
        ("renew_on", "Renewal on") if edit == "create" else ("due_date", "Due date")
    )
    if edit in {"date", "create"}:
        kind = await authenticated_client.post(
            "/api/entry-types",
            headers=headers,
            json={
                "track_id": track_id,
                "name": "Contract",
                "form_schema": {
                    "fields": [
                        {"key": date_key, "name": date_label, "type": "date"},
                        {"key": "notes", "name": "Notes", "type": "markdown"},
                        {"key": "count", "name": "Count", "type": "number"},
                        {"key": "expedite", "name": "Expedite", "type": "boolean"},
                    ]
                },
            },
        )
        assert kind.status_code == 200, kind.text
        record_input.update(
            type_id=kind.json()["entry_type"]["id"],
            custom_fields={date_key: "2026-11-26"},
        )
    response = await authenticated_client.post(
        "/api/entries", headers=headers, json=record_input
    )
    assert response.status_code in (200, 201), response.text
    entry = response.json()["entry"]
    thread = await create_thread(
        user_id=me, workspace_id=ws, provider_id="integral_native"
    )
    receipt = await submit_chat_turn(
        ChatTurnSubmissionRequest(
            principal_id=me,
            workspace_id=ws,
            thread_id=thread.id,
            client_request_id="voice-confirmation-fixture",
            parts=[{"type": "text", "text": "Rename Acme to Georgetown"}],
            execution_context=ChatTurnExecutionContext(
                extra_data={"voice_delegate": True}, focused_track_id=track_id
            ),
        )
    )
    claimed = await claim_due_candidate(
        worker_id="voice-confirmation-test",
        work_item_id=receipt.work_item_id,
        lease_seconds=60,
    )
    assert claimed is not None
    run_id = "voice-confirmation-run"
    payload = {
        "entry_id": entry["id"],
        "expected_record_revision": entry["record_revision"],
    }
    if edit == "date":
        from app.agentive.tooling.bindings import _schema_revision_for_update

        payload.update(
            fields={"due_date": "2026-11-27"},
            expected_schema_revision=await _schema_revision_for_update(entry),
        )
    else:
        payload["title"] = "Acme Georgetown"
    if edit == "create":
        payload = {
            "track_id": track_id,
            "title": "Water the plants",
            "body": "At noon local time",
            "entry_type": entry["type_id"],
            "fields": {date_key: "2026-11-27", "count": 0, "expedite": False},
        }
    proof = {
        "version": "work-proposal-v1",
        "capability_key": (
            "integral_create_entry" if edit == "create" else "integral_update_entry"
        ),
        "work_item_id": claimed.work_item_id,
        "run_id": run_id,
        "principal_id": me,
        "workspace_id": ws,
        "thread_id": thread.id,
    }
    with bind_proposal_origin(proof):
        staged = await create_staged_change(
            user_id=me,
            workspace_id=ws,
            session_id=thread.id,
            kind="create_entry" if edit == "create" else "update_entry",
            summary="Rename Acme",
            diff_human="Title from Acme to Acme Georgetown",
            diff_machine=payload,
            payload=payload,
        )
    if edit == "create":
        from app.services.native_voice_creation import read_creation_review

        rich_payload = {
            **payload,
            "fields": {**payload["fields"], "notes": "**Review me**"},
        }
        from dataclasses import replace

        rich = replace(staged, payload=rich_payload)
        assert await read_creation_review(rich, claimed.work_item_id, run_id) is None
    approval, parked = await work_approvals.propose_work_approval_unit(
        work_item_id=claimed.work_item_id,
        lease_token=claimed.lease_token,
        lease_fence=claimed.lease_fence,
        run_id=run_id,
        run_step_id="voice-confirmation-step",
        staging_token=staged.token,
        authority_digest="voice-fixture-authority",
    )
    assert parked.status == "waiting_for_human"
    browser, provider = Browser(), Provider()
    scope = native_voice.VoiceScope(me, ws, "", thread.id)
    from app.models.nodes import ChatThread
    from app.services.chat_threads import list_messages

    before = await ChatThread.get(thread.id)
    before_ids = [message.id for message in await list_messages(before)]
    assert "waiting for review" in await native_voice._save_chat_note(
        scope, "note-during-review", "A separate note"
    )
    after = await ChatThread.get(thread.id)
    assert before.updated_at == after.updated_at
    assert [message.id for message in await list_messages(after)] == before_ids
    relay = native_voice.VoiceRelay(browser, provider, scope)
    relay.accepted = native_voice._AcceptedVoiceTurn(
        receipt.work_item_id, receipt.message_id, "Rename Acme to Georgetown"
    )
    if edit == "create":
        relay.navigation.focus = VoiceDestination("track", track_id, "Tasks")
    else:
        relay.navigation.focus = VoiceDestination(
            "entry", entry["id"], "Acme", track_id
        )
    review = await relay._current_review()
    assert review is not None
    if edit == "date":
        assert "2026-11-26" in review.text and "2026-11-27" in review.text
        await relay._revise_pending_review(
            "correct-date", {"field_number": 1, "value": "2026-11-30"}, relay.generation
        )
        assert (await event(provider.outbox))["item"]["call_id"] == "correct-date"
        revised_speech = (await event(provider.outbox))["response"]["instructions"]
        assert "2026-11-30" in revised_speech and "not been applied" in revised_speech
        # Finish this private readout so the later approval can speak.
        relay._discard_silent_readout()
        from app.agentive.staging import get_token

        old = await get_token(staged.token)
        assert old.state == "revoked" and old.decision_source == "voice"
        review = await relay._current_review()
        assert review is not None and review.proposal_id != staged.token
        revised = await get_token(review.proposal_id)
        assert revised.supersedes_token == staged.token and revised.origin_proof is None
        assert "2026-11-26" in review.text and "2026-11-30" in review.text
        # Reopening the call does not inherit the retired WorkApproval.
        relay = native_voice.VoiceRelay(
            browser, provider, native_voice.VoiceScope(me, ws, "", thread.id)
        )
        relay.navigation.focus = VoiceDestination(
            "entry", entry["id"], "Acme", track_id
        )
        recovered = await relay._current_review()
        assert recovered is not None and recovered.proposal_id == revised.token
        assert not recovered.work_id and recovered.text == review.text
        review = recovered
    if edit == "create":
        assert await relay._review_for_receipt(relay.accepted) == review
        await relay._revise_pending_review(
            "correct-create-date",
            {"field": date_label, "value": "2026-11-30"},
            relay.generation,
        )
        assert (await event(provider.outbox))["item"][
            "call_id"
        ] == "correct-create-date"
        spoken = (await event(provider.outbox))["response"]["instructions"]
        assert "2026-11-30" in spoken
        relay._discard_silent_readout()
        review = await relay._current_review()
        assert review is not None and review.proposal_id != staged.token
    if edit == "create":
        relay = native_voice.VoiceRelay(browser, provider, scope)
        relay.navigation.focus = VoiceDestination("track", track_id, "Tasks")
        recovered = await relay._current_review()
        assert recovered and recovered.proposal_id == review.proposal_id
        review = recovered
    confirmation = VoiceConfirmation(
        review,
        "reject" if edit == "reject" else "approve",
        ("fixture", "fixture", "byok", "fixture"),
    )
    confirmation.phase = "capture"
    confirmation.audio.extend(b"\xff\x7f" * 48000)

    async def recognize(*args, **kwargs):
        return confirmation.decision + " " + confirmation.code

    monkeypatch.setattr(service, "transcribe_voice_confirmation", recognize)
    monkeypatch.setattr(native_voice, "CAPTURE_SECONDS", 0.01)
    relay.review_confirmation = confirmation
    await relay._capture_review_confirmation(confirmation)
    assert (await event(browser.outbox))["type"] == "review_confirmation_ended"
    if edit == "create":
        from app.models.nodes import Entry

        created = await Entry.find(
            {"context.track_id": track_id, "context.title": "Water the plants"}
        )
        assert len(created) == 1
        assert created[0].custom_fields[date_key] == "2026-11-30"
        assert created[0].body == "At noon local time"
        spoken = (await event(provider.outbox))["response"]["instructions"]
        assert "Water the plants" in spoken and "Current saved values" in spoken
        assert "Count: 0" in spoken and "Expedite: False" in spoken
        await relay._capture_review_confirmation(confirmation)
        assert (
            len(
                await Entry.find(
                    {"context.track_id": track_id, "context.title": "Water the plants"}
                )
            )
            == 1
        )
        return
    actual = (
        await authenticated_client.get(f"/api/entries/{entry['id']}", headers=headers)
    ).json()["entry"]
    if edit == "reject":
        assert (
            actual["title"] == "Acme"
            and actual["record_revision"] == entry["record_revision"]
        )
        spoken = (await event(provider.outbox))["response"]["instructions"]
        assert "review was rejected" in spoken
        assert await relay._current_review() is None
        from app.agentive.staging import get_token

        assert (await get_token(staged.token)).state == "revoked"
        corrected = await submit_chat_turn(
            ChatTurnSubmissionRequest(
                principal_id=me,
                workspace_id=ws,
                thread_id=thread.id,
                client_request_id="voice-corrected-fixture",
                parts=[{"type": "text", "text": "Rename Acme to Linden instead"}],
                execution_context=ChatTurnExecutionContext(
                    extra_data={"voice_delegate": True}, focused_track_id=track_id
                ),
            )
        )
        assert corrected.work_item_id != receipt.work_item_id
        return
    if edit == "date":
        assert actual["custom_fields"]["due_date"] == "2026-11-30"
    else:
        assert actual["title"] == "Acme Georgetown"
        assert relay.navigation.focus.label == "Acme Georgetown"
    spoken = (await event(provider.outbox))["response"]["instructions"]
    assert "Current saved values" in spoken
    assert ("2026-11-30" if edit == "date" else "Acme Georgetown") in spoken
    assert actual["record_revision"] == entry["record_revision"] + 1
    await relay._capture_review_confirmation(confirmation)
    repeated = (
        await authenticated_client.get(f"/api/entries/{entry['id']}", headers=headers)
    ).json()["entry"]
    assert repeated["record_revision"] == actual["record_revision"]


async def test_requested_note_is_scoped_replay_safe_and_readable(
    authenticated_client, test_user
):
    """One explicit note survives replay without starting an agent or crossing workspaces."""
    from app.api.errors import InsufficientPermissionsError, ResourceConflictError
    from app.services.chat_threads import list_messages

    if os.getenv("INTEGRAL_TEST_DB") not in ("postgres", "postgresql"):
        pytest.skip("Voice note admission requires a transactional graph")
    response = await authenticated_client.post(
        "/api/workspaces", json={"name": "Spoken notes"}
    )
    workspace = response.json()["workspace"]["id"]
    principal = test_user.user_id
    thread = await create_thread(
        user_id=principal, workspace_id=workspace, provider_id="integral_native"
    )
    scope = native_voice.VoiceScope(principal, workspace, "", thread.id)
    note = "The test phrase is violet sailboat."
    await native_voice._save_chat_note(scope, "call-1", note)
    await native_voice._save_chat_note(scope, "call-1", note)
    with pytest.raises(ResourceConflictError):
        await native_voice._save_chat_note(scope, "call-1", "Changed on replay")
    another = await authenticated_client.post(
        "/api/workspaces", json={"name": "Other note workspace"}
    )
    wrong = native_voice.VoiceScope(
        principal, another.json()["workspace"]["id"], "", thread.id
    )
    with pytest.raises(InsufficientPermissionsError):
        await native_voice._save_chat_note(wrong, "call-2", "Foreign note")
    [row] = await list_messages(thread)
    assert (row.role, row.provider_metadata, row.parts) == (
        "assistant",
        {"source": "voice_note"},
        [{"type": "text", "text": note}],
    )
    relay = native_voice.VoiceRelay(Browser(), Provider(), scope)
    assert note in await relay._read_chat_content("latest_reply")
    assert not await WorkItem.find({"kind": "chat_turn", "thread_id": thread.id})


async def test_directory_post_rechecks_source_and_commits_one_chat_message(
    authenticated_client, test_user
):
    """A heard directory can be copied without guessing names or resubmitting work."""
    from app.services.chat_threads import list_messages

    if os.getenv("INTEGRAL_TEST_DB") not in ("postgres", "postgresql"):
        pytest.skip("Directory posts need a transactional graph")
    workspace = await authenticated_client.post(
        "/api/workspaces", json={"name": "Directory notes"}
    )
    ws = workspace.json()["workspace"]["id"]
    principal = test_user.user_id
    headers = {"X-Integral-Scope": f"ws:{ws}"}

    async def add(name):
        created = await authenticated_client.post(
            "/api/apps", headers=headers, json={"name": name, "visibility": "private"}
        )
        assert created.status_code == 200

    await add("Field Notes")
    thread = await create_thread(
        user_id=principal, workspace_id=ws, provider_id="integral_native"
    )
    scope = native_voice.VoiceScope(principal, ws, "", thread.id)
    relay = native_voice.VoiceRelay(Browser(), Provider(), scope)
    page = await relay.navigation.select(
        {"action": "apps", "ordinal": 0}, principal=principal, workspace=ws
    )
    assert page.labels == ("Field Notes",)
    assert "don't have" in await relay._post_directory("last_list", 1)
    relay.last_spoken_page = page
    assert "posted" in await relay._post_directory("last_list", 2)
    assert "already saved" in await relay._post_directory("last_list", 2)
    assert "already saved" in await relay._post_directory("apps", 2)

    await add("Reading Room")
    assert "changed" in await relay._post_directory("last_list", 3)
    relay.navigation.expires = 0
    assert "don't have" in await relay._post_directory("last_list", 4)
    [posted] = await list_messages(thread)
    assert posted.parts == [{"type": "text", "text": "1. Field Notes"}]
    assert posted.provider_metadata == {"source": "voice_note"}
    assert "posted" in await relay._post_directory("apps", 5)
    assert any(
        "Reading Room" in row.parts[0]["text"] for row in await list_messages(thread)
    )
    assert not await WorkItem.find({"kind": "chat_turn", "thread_id": thread.id})
