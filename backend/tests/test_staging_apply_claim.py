"""``bless_and_execute``: one executor per token, one-time approval only.

B3-lite — ``bless_token`` returns an already-blessed token as a no-op and the
executor runs before ``consume_token``, so two concurrent approves of the same
card (two tabs, a retry, a routine reconcile racing a manual click) both ran
the write. An in-flight claim now makes the second caller fail with
``already_executing``.

C2 — session-wide autonomy is disabled in V1. Only the exact staged proposal
may be approved, and a legacy session-mode request must have no side effects.

B5 — ``/text-approve`` carried its own copy of the apply sequence that dropped
the card's workspace and never recorded a refusal. It now runs the shared
path.
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from app.agentive import staging
from app.agentive.services import staging_apply
from app.agentive.staging import StagingError, create_staged_change, get_token
from app.services.agent_scope import current_scope_workspace_id


@pytest.fixture(autouse=True)
def _clean():
    staging._reset_for_tests()
    yield
    staging._reset_for_tests()


async def _mint(*, kind: str = "create_entry", workspace_id: str | None = None):
    token = current_scope_workspace_id.set(workspace_id)
    try:
        return await create_staged_change(
            user_id="u1",
            session_id="s1",
            kind=kind,
            summary="Create entry “A”",
            diff_human="- create",
            diff_machine={"track_id": "n.Track.1", "title": "A"},
            payload={"track_id": "n.Track.1", "title": "A"},
        )
    finally:
        current_scope_workspace_id.reset(token)


def _fake_executor(monkeypatch, *, result=None, delay: float = 0.0):
    """Stub the kind executor; returns the call log."""
    calls: list = []

    async def fake(*, request, user_id, kind, payload, preferred_workspace_id=None):
        calls.append(
            {
                "kind": kind,
                "payload": payload,
                "preferred_workspace_id": preferred_workspace_id,
            }
        )
        if delay:
            await asyncio.sleep(delay)
        return dict(result) if result is not None else {"ok": True}

    monkeypatch.setattr(staging_apply, "_dispatch_kind", fake)
    return calls


# --- B3-lite: an in-flight claim -------------------------------------------


@pytest.mark.smoke
@pytest.mark.asyncio
async def test_two_concurrent_approves_run_the_executor_once(monkeypatch):
    """Concurrent approval attempts execute a write only once."""
    sc = await _mint()
    calls = _fake_executor(monkeypatch, delay=0.05)

    outcomes = await asyncio.gather(
        staging_apply.bless_and_execute(user_id="u1", token=sc.token),
        staging_apply.bless_and_execute(user_id="u1", token=sc.token),
        return_exceptions=True,
    )

    assert len(calls) == 1, calls
    errors = [o for o in outcomes if isinstance(o, StagingError)]
    envelopes = [o for o in outcomes if isinstance(o, dict)]
    assert len(errors) == 1 and errors[0].code == "already_executing"
    assert len(envelopes) == 1 and envelopes[0]["consumed"] is True
    assert envelopes[0]["staged_change"]["state"] == "consumed"
    assert (await get_token(sc.token)).state == "consumed"


@pytest.mark.smoke
@pytest.mark.asyncio
async def test_a_refused_execute_releases_the_claim_for_a_retry(monkeypatch):
    """A refused write releases its execution claim for a later retry."""
    sc = await _mint()
    _fake_executor(monkeypatch, result={"error": True, "message": "refused"})

    first = await staging_apply.bless_and_execute(user_id="u1", token=sc.token)
    assert first["consumed"] is False
    live = await get_token(sc.token)
    assert live.state == "blessed"
    assert live.executing is False
    assert live.last_error["message"] == "refused"

    # The retry is not refused as already_executing.
    calls = _fake_executor(monkeypatch)
    second = await staging_apply.bless_and_execute(user_id="u1", token=sc.token)
    assert second["consumed"] is True
    assert len(calls) == 1


@pytest.mark.smoke
@pytest.mark.asyncio
async def test_an_executor_exception_releases_the_claim(monkeypatch):
    """An executor error releases its claim without consuming the change."""
    sc = await _mint()

    async def boom(**_kwargs):
        raise RuntimeError("executor exploded")

    monkeypatch.setattr(staging_apply, "_dispatch_kind", boom)
    with pytest.raises(RuntimeError):
        await staging_apply.bless_and_execute(user_id="u1", token=sc.token)
    assert (await get_token(sc.token)).executing is False


@pytest.mark.smoke
@pytest.mark.asyncio
async def test_claim_execution_refuses_a_pending_token():
    """Pending changes cannot be claimed before they are approved."""
    sc = await _mint()
    with pytest.raises(StagingError) as exc:
        await staging.claim_execution(user_id="u1", token=sc.token)
    assert exc.value.code == "not_blessed"


# --- C2: reject session-wide approval before side effects -------------------


@pytest.mark.smoke
@pytest.mark.asyncio
async def test_session_autonomy_request_is_rejected_before_execution(
    monkeypatch,
):
    """A legacy session-mode request cannot decide or execute a write."""
    sc = await _mint()
    calls = _fake_executor(monkeypatch)
    with pytest.raises(StagingError, match="Session-wide auto-approval is not enabled"):
        await staging_apply.bless_and_execute(
            user_id="u1", token=sc.token, autonomy="session"
        )
    assert calls == []
    assert (await get_token(sc.token)).state == "pending"
    assert staging.has_autonomy("u1", "s1", "create_entry") is False


@pytest.mark.smoke
@pytest.mark.asyncio
async def test_session_autonomy_request_does_not_consume_change(monkeypatch):
    """A clean executor cannot be reached through a session request."""
    sc = await _mint()
    calls = _fake_executor(monkeypatch)
    with pytest.raises(StagingError):
        await staging_apply.bless_and_execute(
            user_id="u1", token=sc.token, autonomy="session"
        )
    assert calls == []
    assert (await get_token(sc.token)).state == "pending"
    assert staging.has_autonomy("u1", "s1", "create_entry") is False


@pytest.mark.smoke
@pytest.mark.asyncio
async def test_session_autonomy_is_rejected_for_every_kind(monkeypatch):
    """Every kind follows the same one-time approval boundary."""
    sc = await _mint(kind="delete_entry")
    calls = _fake_executor(monkeypatch)
    with pytest.raises(StagingError):
        await staging_apply.bless_and_execute(
            user_id="u1", token=sc.token, autonomy="session"
        )
    assert calls == []
    assert (await get_token(sc.token)).state == "pending"
    assert staging.has_autonomy("u1", "s1", "delete_entry") is False
    assert "delete_entry" not in staging._autonomy.get(("u1", "s1"), set())


# --- B5: /text-approve runs the shared apply path ---------------------------


def _request(user_id: str = "u1"):
    return SimpleNamespace(state=SimpleNamespace(user=SimpleNamespace(id=user_id)))


@pytest.mark.smoke
@pytest.mark.asyncio
async def test_text_approve_executes_in_the_workspace_the_card_was_staged_in(
    monkeypatch,
):
    """Legacy text approval executes in the captured staging workspace."""
    from app.agentive.api.staging import text_approve_endpoint

    sc = await _mint(workspace_id="n.Workspace.acme")
    calls = _fake_executor(monkeypatch)

    out = await text_approve_endpoint(_request(), text="yes")

    assert out["parsed"] == 1, out
    (result,) = out["results"]
    assert result["success"] is True
    assert result["token"] == sc.token
    assert result["state"] == "consumed"
    assert calls == [
        {
            "kind": "create_entry",
            "payload": sc.payload,
            "preferred_workspace_id": "n.Workspace.acme",
        }
    ]


@pytest.mark.smoke
@pytest.mark.asyncio
async def test_text_approve_records_a_refusal_on_the_change(monkeypatch):
    """Legacy text approval preserves a declined executor outcome."""
    from app.agentive.api.staging import text_approve_endpoint

    sc = await _mint()
    _fake_executor(monkeypatch, result={"error": True, "message": "refused"})

    out = await text_approve_endpoint(_request(), text="yes")

    (result,) = out["results"]
    assert result["state"] == "blessed"
    live = await get_token(sc.token)
    assert live.last_error["message"] == "refused"


@pytest.mark.smoke
@pytest.mark.asyncio
async def test_native_model_tool_approval_uses_only_this_workspace_pending_write(
    monkeypatch: pytest.MonkeyPatch,
):
    """The native model tool resolves and executes the scoped pending write."""
    import hashlib

    from app.agentive.harness.broker_tools import build_brokered_tools
    from app.agentive.harness.contracts import HarnessExecutionScope
    from app.services import prompt_queue

    workspace_id = "n.Workspace.chat-approval"
    sc = await _mint(workspace_id=workspace_id)
    calls = _fake_executor(monkeypatch)
    marked = []

    async def mark_write_item(**kwargs):
        marked.append(kwargs["token"])
        return {"closed": True}

    async def get_thread(_thread_id):
        return SimpleNamespace(workspace_id=workspace_id)

    monkeypatch.setattr(prompt_queue, "mark_write_item", mark_write_item)
    monkeypatch.setattr("app.models.nodes.ChatThread.get", get_thread)
    reference = hashlib.sha256(sc.token.encode()).hexdigest()[:16]
    scope = HarnessExecutionScope(
        tenant_id=workspace_id,
        workspace_id=workspace_id,
        principal_id="u1",
        thread_id="thread-1",
        session_id="s1",
        run_id="run-1",
        permission_revision="p1",
        capability_version="c1",
    )
    tools = build_brokered_tools(
        scope=scope,
        catalogue=[],
        pending_approval_tokens={reference: sc.token},
    )
    tool = next(item for item in tools if item.name == "integral_resolve_pending_write")
    outcome = await tool.function_schema.call(
        {"item_reference": reference, "decision": "approve"},
        SimpleNamespace(tool_call_id="approval-1"),
    )

    assert outcome == {"ok": True, "decision": "approve", "state": "consumed"}
    assert marked == [sc.token]
    assert len(calls) == 1


@pytest.mark.smoke
@pytest.mark.asyncio
async def test_native_model_tool_approval_cannot_cross_workspace(monkeypatch):
    """The native model tool cannot execute another workspace's pending item."""
    import hashlib

    from app.agentive.harness.broker_tools import build_brokered_tools
    from app.agentive.harness.contracts import HarnessExecutionScope

    sc = await _mint(workspace_id="n.Workspace.other")
    reference = hashlib.sha256(sc.token.encode()).hexdigest()[:16]
    scope = HarnessExecutionScope(
        tenant_id="n.Workspace.current",
        workspace_id="n.Workspace.current",
        principal_id="u1",
        thread_id="thread-1",
        session_id="s1",
        run_id="run-1",
        permission_revision="p1",
        capability_version="c1",
    )
    tools = build_brokered_tools(
        scope=scope,
        catalogue=[],
        pending_approval_tokens={reference: sc.token},
    )
    tool = next(item for item in tools if item.name == "integral_resolve_pending_write")
    outcome = await tool.function_schema.call(
        {"item_reference": reference, "decision": "approve"},
        SimpleNamespace(tool_call_id="approval-cross-workspace"),
    )

    assert outcome == {"ok": False, "error_code": "pending_item_changed"}
    assert (await get_token(sc.token)).state == "pending"
