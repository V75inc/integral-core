"""Routine write-scope reconciliation must fail CLOSED (#32).

``_snapshot_pending_tokens`` used to swallow every exception and return an
empty set. ``_reconcile_write_scope`` then treated an empty skip-set as
"nothing was pending before the turn" and considered EVERY pending card in
the session the routine's to auto-bless — including cards the USER staged
and has not reviewed.

Two independent guards now cover that:

1. The snapshot returns ``None`` on failure ("unknown", not "empty") and
   ``_execute_run`` skips reconciliation entirely.
2. ``_reconcile_write_scope`` additionally requires ``sc.created_at`` to be
   at/after the turn start, so a card the routine did not mint is never
   auto-blessed even if the skip-set is wrong.
"""

from __future__ import annotations

import pytest

from tests.test_routine_tasks import _bootstrap_user_workspace_track_thread


async def _make_routine_with_allowed_entry(email: str):
    """Bootstrap a routine whose write_scope allowlists one real Entry.

    Returns ``(routine, thread, allowed_entry_id)``.
    """
    from app.agentive.services.routine_tasks import create_routine_task
    from app.agentive.tooling.invoke import invoke_route_in_process
    from app.api.entries import create_entry

    user_id, workspace_id, track_id, thread = (
        await _bootstrap_user_workspace_track_thread(email)
    )
    created = await invoke_route_in_process(
        create_entry,
        principal_id=user_id,
        scope=workspace_id,
        track_id=track_id,
        title="Allowlisted entry",
    )
    allowed_id = created["entry"]["id"]
    routine = await create_routine_task(
        user_id=user_id,
        workspace_id=workspace_id,
        thread_id=thread.id,
        instruction="Keep the allowlisted entry updated.",
        cron="0 8 * * *",
        write_scope=[{"resource_type": "entry", "resource_id": allowed_id}],
    )
    return routine, thread, allowed_id


@pytest.mark.asyncio
async def test_snapshot_returns_none_when_it_fails(monkeypatch):
    """A failed pre-turn snapshot is 'unknown' (None), never 'empty' (set())."""
    import app.agentive.staging as staging_mod
    from app.services.routine_task_scheduler import _snapshot_pending_tokens

    routine, _thread, _allowed_id = await _make_routine_with_allowed_entry(
        "routine-snap-none@example.com"
    )

    async def boom(*_args: object, **_kwargs: object) -> None:
        raise RuntimeError("staging store unavailable")

    monkeypatch.setattr(staging_mod, "list_pending_tokens", boom)

    snapshot = await _snapshot_pending_tokens(routine)
    assert snapshot is None, (
        "snapshot failure returned a set — the caller cannot distinguish "
        "'nothing was pending' from 'we could not look', and blesses the "
        "user's unreviewed cards"
    )


@pytest.mark.asyncio
async def test_run_blesses_nothing_when_the_snapshot_raises(monkeypatch):
    """With the snapshot unavailable, a successful run skips reconciliation."""
    import app.agentive.staging as staging_mod
    import app.services.routine_task_scheduler as sched
    from app.agentive.staging import create_staged_change, get_token

    routine, thread, allowed_id = await _make_routine_with_allowed_entry(
        "routine-snap-raise@example.com"
    )

    # A card the USER staged, targeting the allowlisted entry — exactly the
    # card an empty skip-set would have blessed.
    user_card = await create_staged_change(
        user_id=routine.user_id,
        session_id=thread.provider_session_id,
        kind="update_entry",
        summary="user staged this",
        diff_human="user staged this",
        diff_machine={},
        payload={"entry_id": allowed_id, "title": "User edit"},
    )

    real_list_pending = staging_mod.list_pending_tokens
    calls = {"n": 0}

    async def boom_first(*args: object, **kwargs: object) -> object:
        calls["n"] += 1
        if calls["n"] == 1:  # the pre-turn snapshot
            raise RuntimeError("staging store unavailable")
        return await real_list_pending(*args, **kwargs)

    monkeypatch.setattr(staging_mod, "list_pending_tokens", boom_first)

    async def fake_turn(_task):
        return True, None

    monkeypatch.setattr(sched, "_run_agent_turn", fake_turn)

    await sched._execute_run(routine)

    after = await get_token(user_card.token)
    assert after.state == "pending", (
        "a user-staged card was auto-blessed after the pre-turn snapshot "
        f"failed (state={after.state})"
    )


@pytest.mark.asyncio
async def test_card_predating_the_turn_is_never_blessed():
    """The created_at gate holds even when the skip-set is empty/wrong."""
    from app.agentive.staging import create_staged_change, get_token
    from app.services.routine_task_scheduler import _reconcile_write_scope
    from app.utils.time import utc_now

    routine, thread, allowed_id = await _make_routine_with_allowed_entry(
        "routine-predates-turn@example.com"
    )

    stale_card = await create_staged_change(
        user_id=routine.user_id,
        session_id=thread.provider_session_id,
        kind="update_entry",
        summary="staged before the turn",
        diff_human="staged before the turn",
        diff_machine={},
        payload={"entry_id": allowed_id, "title": "User edit"},
    )

    # Turn starts AFTER the card was minted, and the skip-set is deliberately
    # empty — the provenance-by-inference path that used to bless it.
    turn_started_at = utc_now()
    await _reconcile_write_scope(
        routine,
        preexisting_tokens=set(),
        turn_started_at=turn_started_at,
    )

    after = await get_token(stale_card.token)
    assert after.state == "pending", (
        "a card minted before the turn start was auto-blessed " f"(state={after.state})"
    )


@pytest.mark.asyncio
async def test_card_minted_during_the_turn_still_auto_applies():
    """The new gate must not break the feature it guards."""
    from app.agentive.staging import create_staged_change, get_token
    from app.services.routine_task_scheduler import _reconcile_write_scope
    from app.utils.time import utc_now

    routine, thread, allowed_id = await _make_routine_with_allowed_entry(
        "routine-during-turn@example.com"
    )

    turn_started_at = utc_now()
    fresh_card = await create_staged_change(
        user_id=routine.user_id,
        session_id=thread.provider_session_id,
        kind="update_entry",
        summary="minted by the routine",
        diff_human="minted by the routine",
        diff_machine={},
        payload={"entry_id": allowed_id, "title": "Routine edit"},
    )

    await _reconcile_write_scope(
        routine,
        preexisting_tokens=set(),
        turn_started_at=turn_started_at,
    )

    after = await get_token(fresh_card.token)
    assert after.state == "consumed", (
        "a card minted during the turn and inside write_scope was not "
        f"auto-applied (state={after.state})"
    )


def test_minted_during_turn_is_fail_closed_on_bad_timestamps():
    """An absent / non-datetime created_at is treated as NOT ours to bless."""
    from app.services.routine_task_scheduler import _minted_during_turn
    from app.utils.time import utc_now

    now = utc_now()
    assert _minted_during_turn(None, now) is False
    assert _minted_during_turn("2026-01-01T00:00:00+00:00", now) is False
    assert _minted_during_turn(now, now) is True
