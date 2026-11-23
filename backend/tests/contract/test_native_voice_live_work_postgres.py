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
