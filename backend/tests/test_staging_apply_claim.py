"""``bless_and_execute``: one executor per token, grants only after a clean write.

B3-lite — ``bless_token`` returns an already-blessed token as a no-op and the
executor runs before ``consume_token``, so two concurrent approves of the same
card (two tabs, a retry, a routine reconcile racing a manual click) both ran
the write. An in-flight claim now makes the second caller fail with
``already_executing``.

C2 — session autonomy was granted at bless time, before the executor ran, so
a refused write left a standing grant that auto-blessed the next same-kind
card. The grant now follows a clean execute.

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
    sc = await _mint()
    with pytest.raises(StagingError) as exc:
        await staging.claim_execution(user_id="u1", token=sc.token)
    assert exc.value.code == "not_blessed"


# --- C2: session autonomy follows a clean execute ---------------------------


@pytest.mark.smoke
@pytest.mark.asyncio
async def test_a_refused_write_grants_no_session_autonomy(monkeypatch):
    sc = await _mint()
    _fake_executor(monkeypatch, result={"error": True, "message": "refused"})

    await staging_apply.bless_and_execute(
        user_id="u1", token=sc.token, autonomy="session"
    )

    assert staging.has_autonomy("u1", "s1", "create_entry") is False


@pytest.mark.smoke
@pytest.mark.asyncio
async def test_a_clean_write_grants_session_autonomy(monkeypatch):
    sc = await _mint()
    _fake_executor(monkeypatch)

    await staging_apply.bless_and_execute(
        user_id="u1", token=sc.token, autonomy="session"
    )

    assert staging.has_autonomy("u1", "s1", "create_entry") is True


@pytest.mark.smoke
@pytest.mark.asyncio
async def test_blocked_kinds_never_gain_session_autonomy(monkeypatch):
    sc = await _mint(kind="delete_entry")
    _fake_executor(monkeypatch)

    await staging_apply.bless_and_execute(
        user_id="u1", token=sc.token, autonomy="session"
    )

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
    from app.agentive.api.staging import text_approve_endpoint

    sc = await _mint()
    _fake_executor(monkeypatch, result={"error": True, "message": "refused"})

    out = await text_approve_endpoint(_request(), text="yes")

    (result,) = out["results"]
    assert result["state"] == "blessed"
    live = await get_token(sc.token)
    assert live.last_error["message"] == "refused"
