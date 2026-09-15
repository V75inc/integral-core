"""Prompt Sheet integrity: the sequester gate and truthful approvals.

Two defects this pins:

1. The sequester gate sat *below* the workspace-tool fallback in
   ``dispatch_tool``, which returns early — so while a sheet was open, mounted
   ``mcp__*`` tools and trusted bundle tools still executed. The gate only ever
   covered central manifest tools.
2. ``mark_write_item`` wrote ``approved`` on the caller's say-so. The sheet
   drained and the resume line read "Approved — <summary>" while the
   StagedChange sat ``pending``, telling the model a write had landed when it
   had not.
"""

from __future__ import annotations

import inspect

import pytest

pytestmark = pytest.mark.smoke


def test_sequester_gate_precedes_every_dispatch_path():
    """The gate must run before the bundle / mcp__* fallback can return."""
    from app.agentive.tooling import dispatch

    src = inspect.getsource(dispatch.dispatch_tool)
    gate = src.index("session_queue_is_open")
    fallback = src.index("get_workspace_tools")
    registry = src.index("_registry().get(name)")
    assert gate < fallback, "bundle/mcp fallback returns before the sequester gate"
    assert gate < registry, "gate must precede tool resolution entirely"


@pytest.mark.asyncio
async def test_mark_write_refuses_to_forge_an_approval(monkeypatch):
    """`approved` requires the StagedChange to actually be blessed."""
    from app.models.nodes import ChatThread
    from app.services import prompt_queue as pq

    thread = await ChatThread.create(user_id="u-forge", provider_id="jvagent")
    thread.prompt_queue = {
        "status": pq.QUEUE_STATUS_OPEN,
        "opened_at": None,
        "closed_at": None,
        "close_reason": None,
        "items": [
            {
                "id": "i1",
                "kind": pq.ITEM_STAGED_WRITE,
                "token": "tok-unblessed",
                "status": pq.STATUS_PENDING,
                "summary": "Create entry Acme",
            }
        ],
    }
    await thread.save()

    async def _pending_token(token):
        class _SC:
            state = "pending"

        return _SC()

    monkeypatch.setattr("app.agentive.staging.get_token", _pending_token)

    result = await pq.mark_write_item(
        user_id="u-forge",
        thread=thread,
        token="tok-unblessed",
        status=pq.STATUS_APPROVED,
    )
    assert result.get("error") == "state_mismatch", result
    queue = pq.get_queue(thread)
    assert queue["items"][0]["status"] == pq.STATUS_PENDING


@pytest.mark.asyncio
async def test_mark_write_accepts_a_genuinely_blessed_change(monkeypatch):
    """The happy path still works when the token really is blessed."""
    from app.models.nodes import ChatThread
    from app.services import prompt_queue as pq

    thread = await ChatThread.create(user_id="u-ok", provider_id="jvagent")
    thread.prompt_queue = {
        "status": pq.QUEUE_STATUS_OPEN,
        "opened_at": None,
        "closed_at": None,
        "close_reason": None,
        "items": [
            {
                "id": "i1",
                "kind": pq.ITEM_STAGED_WRITE,
                "token": "tok-blessed",
                "status": pq.STATUS_PENDING,
                "summary": "Create entry Acme",
            }
        ],
    }
    await thread.save()

    async def _blessed_token(token):
        class _SC:
            state = "consumed"

        return _SC()

    monkeypatch.setattr("app.agentive.staging.get_token", _blessed_token)

    result = await pq.mark_write_item(
        user_id="u-ok",
        thread=thread,
        token="tok-blessed",
        status=pq.STATUS_APPROVED,
    )
    assert result.get("ok") is True
    assert pq.get_queue(thread)["items"][0]["status"] == pq.STATUS_APPROVED


@pytest.mark.asyncio
async def test_mark_write_refuses_to_forge_a_rejection(monkeypatch):
    """The mirror image: "rejected" must also reflect real staging state.

    Verifying only the approved path left this open — the sheet could report
    "Rejected — <summary>" and close while the token stayed live and blessable.
    """
    from app.models.nodes import ChatThread
    from app.services import prompt_queue as pq

    thread = await ChatThread.create(user_id="u-forge-rej", provider_id="jvagent")
    thread.prompt_queue = {
        "status": pq.QUEUE_STATUS_OPEN,
        "opened_at": None,
        "closed_at": None,
        "close_reason": None,
        "items": [
            {
                "id": "i1",
                "kind": pq.ITEM_STAGED_WRITE,
                "token": "tok-live",
                "status": pq.STATUS_PENDING,
                "summary": "Delete entry Acme",
            }
        ],
    }
    await thread.save()

    async def _still_pending(token):
        class _SC:
            state = "pending"

        return _SC()

    monkeypatch.setattr("app.agentive.staging.get_token", _still_pending)

    result = await pq.mark_write_item(
        user_id="u-forge-rej",
        thread=thread,
        token="tok-live",
        status=pq.STATUS_REJECTED,
    )
    assert result.get("error") == "state_mismatch", result
    assert pq.get_queue(thread)["items"][0]["status"] == pq.STATUS_PENDING
