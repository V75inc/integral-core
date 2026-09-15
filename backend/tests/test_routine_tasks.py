"""Tests for Routine Tasks — user-issued recurring chat instructions.

Covers: cron/timezone math (routine_tasks.compute_next_run_at), the full
create-via-chat dispatch path (integral_schedule_task propose -> bless ->
RoutineTask persisted), CRUD service functions, the scheduler loop's
permission re-check (fail-closed on revoked access), write-scope
reconciliation (auto-apply allowlisted target, leave non-listed pending,
never auto-apply delete-class kinds), and auto-pause after repeated
failures.
"""

import pytest

from app.agentive.tooling.dispatch import dispatch_tool

# ---------------------------------------------------------------------------
# Unit — cron/timezone math
# ---------------------------------------------------------------------------


def test_compute_next_run_at_respects_per_task_timezone():
    """Two routines in different timezones fire at their own local 9am —
    the exact case jvspatial's global-timezone scheduler couldn't handle."""
    from app.agentive.services.routine_tasks import compute_next_run_at

    ny = compute_next_run_at(
        "0 9 * * *", "America/New_York", base_iso="2026-01-15T00:00:00+00:00"
    )
    tokyo = compute_next_run_at(
        "0 9 * * *", "Asia/Tokyo", base_iso="2026-01-15T00:00:00+00:00"
    )
    assert ny == "2026-01-15T14:00:00+00:00"  # EST = UTC-5
    assert tokyo == "2026-01-16T00:00:00+00:00"  # JST = UTC+9, already past on the 15th


def test_compute_next_run_at_crosses_dst():
    """Same cron/timezone, evaluated either side of a US DST transition,
    yields the correct UTC offset for each."""
    from app.agentive.services.routine_tasks import compute_next_run_at

    winter = compute_next_run_at(
        "0 9 * * *", "America/New_York", base_iso="2026-01-15T00:00:00+00:00"
    )
    summer = compute_next_run_at(
        "0 9 * * *", "America/New_York", base_iso="2026-06-15T00:00:00+00:00"
    )
    assert winter == "2026-01-15T14:00:00+00:00"  # EST, UTC-5
    assert summer == "2026-06-15T13:00:00+00:00"  # EDT, UTC-4


def test_compute_next_run_at_rejects_bad_cron():
    """An unparseable cron expression raises BadRequestError, not a raw exception."""
    from app.agentive.services.routine_tasks import compute_next_run_at
    from app.api.errors import BadRequestError

    with pytest.raises(BadRequestError):
        compute_next_run_at("not a cron", "UTC")


def test_compute_next_run_at_rejects_bad_timezone():
    """An unknown IANA timezone raises BadRequestError, not a raw exception."""
    from app.agentive.services.routine_tasks import compute_next_run_at
    from app.api.errors import BadRequestError

    with pytest.raises(BadRequestError):
        compute_next_run_at("0 9 * * *", "Not/AZone")


# ---------------------------------------------------------------------------
# Shared bootstrap — no HTTP signup, mirrors test_tooling_dispatch_propose.py
# ---------------------------------------------------------------------------


async def _bootstrap_user_workspace_track_thread(email: str):
    """Create an AuthUser + User + personal workspace + track + ChatThread.

    Returns (auth_user_id, workspace_id, track_id, thread).
    """
    from jvspatial.api.auth.models import UserCreate

    from app.agentive.tooling.invoke import invoke_route_in_process
    from app.api.auth import _get_auth_service
    from app.api.tracks import create_track
    from app.models.nodes import User
    from app.services.app_graph import catalog_user
    from app.services.chat_threads import create_thread, update_provider_session
    from app.services.personal_workspace import ensure_personal_workspace

    auth_service = _get_auth_service()
    user_response = await auth_service.register_user(
        UserCreate(email=email, password="testpassword123")
    )
    auth_user_id = user_response.id
    user_node = await User.create(user_id=auth_user_id, display_name="Routine Test")
    await catalog_user(user_node)
    ws = await ensure_personal_workspace(user_node)
    workspace_id = ws.id if ws else None

    created = await invoke_route_in_process(
        create_track,
        principal_id=auth_user_id,
        scope=workspace_id,
        title="Routine Test Track",
        visibility="private",
        workspace_id=workspace_id,
    )
    assert not (isinstance(created, dict) and created.get("error")), created
    track_id = created["track"]["id"]

    thread = await create_thread(
        user_id=auth_user_id,
        provider_id="jvagent",
        title="Routine test thread",
        workspace_id=workspace_id,
    )
    await update_provider_session(thread, f"sess-{auth_user_id}")

    return auth_user_id, workspace_id, track_id, thread


@pytest.fixture(autouse=True)
def _reset_staging_store():
    from app.agentive.staging import _reset_for_tests

    _reset_for_tests()
    yield
    _reset_for_tests()


# ---------------------------------------------------------------------------
# Integration — full create-via-chat dispatch path
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_schedule_task_stages_then_bless_creates_routine():
    """integral_schedule_task stages (no RoutineTask yet); bless creates one
    with the correct cron/timezone/next_run_at and thread binding."""
    user_id, workspace_id, _track_id, thread = (
        await _bootstrap_user_workspace_track_thread("routine-create@example.com")
    )

    from app.agentive.nodes import RoutineTask

    result = await dispatch_tool(
        "integral_schedule_task",
        {
            "instruction": "Prepare a morning briefing.",
            "cron": "0 9 * * *",
            "timezone": "America/New_York",
        },
        principal_id=user_id,
        scope=workspace_id,
        session_id=thread.provider_session_id,
    )
    assert not result.is_error, result
    token = result.data["token"]

    # Staged, not applied yet.
    assert await RoutineTask.find({"user_id": user_id}) == []

    from app.agentive.services.staging_apply import bless_and_execute

    bless_result = await bless_and_execute(user_id=user_id, token=token)
    assert not bless_result["execute_result"].get("error"), bless_result

    routines = await RoutineTask.find({"user_id": user_id})
    assert len(routines) == 1
    routine = routines[0]
    assert routine.cron == "0 9 * * *"
    assert routine.timezone == "America/New_York"
    assert routine.thread_id == thread.id
    assert routine.status == "active"
    assert routine.next_run_at is not None


@pytest.mark.asyncio
async def test_schedule_task_requires_active_session():
    """No session_id (e.g. an external MCP call) fails closed — no thread to bind to."""
    user_id, workspace_id, _track_id, _thread = (
        await _bootstrap_user_workspace_track_thread("routine-nosession@example.com")
    )

    result = await dispatch_tool(
        "integral_schedule_task",
        {"instruction": "Do a thing.", "cron": "0 9 * * *"},
        principal_id=user_id,
        scope=workspace_id,
        session_id=None,
    )
    assert result.is_error


# ---------------------------------------------------------------------------
# Integration — CRUD service functions
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_list_update_cancel_routine():
    """Create -> list -> pause -> cancel round-trips; cancelled routines drop off the list."""
    from app.agentive.services.routine_tasks import (
        cancel_routine_task,
        create_routine_task,
        list_routine_tasks,
        update_routine_task,
    )

    user_id, workspace_id, _track_id, thread = (
        await _bootstrap_user_workspace_track_thread("routine-crud@example.com")
    )

    routine = await create_routine_task(
        user_id=user_id,
        workspace_id=workspace_id,
        thread_id=thread.id,
        instruction="Daily digest.",
        cron="0 8 * * *",
        timezone="UTC",
    )

    listed = await list_routine_tasks(user_id)
    assert listed["total"] == 1
    assert listed["routines"][0]["id"] == routine.id

    paused = await update_routine_task(
        user_id=user_id, routine_id=routine.id, status="paused"
    )
    assert paused.status == "paused"

    cancelled = await cancel_routine_task(user_id=user_id, routine_id=routine.id)
    assert cancelled.status == "cancelled"

    # Cancelled routines drop out of the listing.
    listed_after = await list_routine_tasks(user_id)
    assert listed_after["total"] == 0


@pytest.mark.asyncio
async def test_abort_routine_run_origin_gated():
    """abort_routine_run cancels only origin=routine_task turns."""
    from app.agentive.services.routine_tasks import (
        abort_routine_run,
        create_routine_task,
        serialize_routine,
    )
    from app.services import chat_turn_registry as registry

    await registry.reset_registry_for_tests()
    user_id, workspace_id, _track_id, thread = (
        await _bootstrap_user_workspace_track_thread("routine-abort@example.com")
    )
    routine = await create_routine_task(
        user_id=user_id,
        workspace_id=workspace_id,
        thread_id=thread.id,
        instruction="Abort probe.",
        cron="0 8 * * *",
    )

    # Human turn on the same thread — must not be cancelled.
    human = await registry.acquire_turn(thread_id=thread.id, user_id=user_id, origin="")
    assert await abort_routine_run(routine) is False
    assert not human.cancel_event.is_set()
    assert serialize_routine(routine)["running"] is False
    await registry.release_turn(thread.id)

    # Scheduled turn — cancelled and reported as running beforehand.
    sched = await registry.acquire_turn(
        thread_id=thread.id, user_id=user_id, origin="routine_task"
    )
    assert serialize_routine(routine)["running"] is True
    assert await abort_routine_run(routine) is True
    assert sched.cancel_event.is_set()
    await registry.release_turn(thread.id)


@pytest.mark.asyncio
async def test_delete_routine_task_removes_node():
    """Hard delete drops the graph node after an optional abort."""
    from app.agentive.nodes import RoutineTask
    from app.agentive.services.routine_tasks import (
        create_routine_task,
        delete_routine_task,
    )

    user_id, workspace_id, _track_id, thread = (
        await _bootstrap_user_workspace_track_thread("routine-delete@example.com")
    )
    routine = await create_routine_task(
        user_id=user_id,
        workspace_id=workspace_id,
        thread_id=thread.id,
        instruction="Delete me.",
        cron="0 8 * * *",
    )
    rid = routine.id
    assert await delete_routine_task(user_id=user_id, routine_id=rid) == rid
    assert await RoutineTask.get(rid) is None


@pytest.mark.asyncio
async def test_execute_run_discards_result_after_mid_run_cancel(monkeypatch):
    """Stopping mid-run must not count as a failure / auto-pause."""
    from app.agentive.services.routine_tasks import (
        cancel_routine_task,
        create_routine_task,
    )
    from app.services import routine_task_scheduler as sched

    user_id, workspace_id, _track_id, thread = (
        await _bootstrap_user_workspace_track_thread("routine-midrun@example.com")
    )
    routine = await create_routine_task(
        user_id=user_id,
        workspace_id=workspace_id,
        thread_id=thread.id,
        instruction="Will be stopped mid-run.",
        cron="0 8 * * *",
    )

    async def _turn_then_cancel(task):
        await cancel_routine_task(user_id=user_id, routine_id=task.id)
        return False, "stream aborted"

    monkeypatch.setattr(sched, "_run_agent_turn", _turn_then_cancel)
    monkeypatch.setattr(sched, "_permission_gate", lambda _t: _async_none())
    monkeypatch.setattr(
        sched, "_snapshot_pending_tokens", lambda _t: _async_empty_set()
    )

    await sched._execute_run(routine)

    from app.agentive.nodes import RoutineTask

    final = await RoutineTask.get(routine.id)
    assert final is not None
    assert final.status == "cancelled"
    assert final.consecutive_failures == 0


async def _async_none():
    return None


async def _async_empty_set():
    return set()


@pytest.mark.asyncio
async def test_update_routine_rejects_foreign_owner():
    """A user cannot update another user's routine — ResourceNotFoundError, not leaked state."""
    from app.agentive.services.routine_tasks import (
        create_routine_task,
        update_routine_task,
    )
    from app.api.errors import ResourceNotFoundError

    owner_id, workspace_id, _track_id, thread = (
        await _bootstrap_user_workspace_track_thread("routine-owner@example.com")
    )
    other_id, _ws2, _t2, _thread2 = await _bootstrap_user_workspace_track_thread(
        "routine-other@example.com"
    )

    routine = await create_routine_task(
        user_id=owner_id,
        workspace_id=workspace_id,
        thread_id=thread.id,
        instruction="Owner-only routine.",
        cron="0 8 * * *",
    )

    with pytest.raises(ResourceNotFoundError):
        await update_routine_task(
            user_id=other_id, routine_id=routine.id, status="paused"
        )


# ---------------------------------------------------------------------------
# Scheduler loop — permission gate, write-scope reconciliation, auto-pause
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_permission_gate_blocks_revoked_workspace_access():
    """A live re-check catches lost workspace access — never trusts creation-time state."""
    from app.agentive.services.routine_tasks import create_routine_task
    from app.services.routine_task_scheduler import _permission_gate

    user_id, workspace_id, _track_id, thread = (
        await _bootstrap_user_workspace_track_thread("routine-perm@example.com")
    )
    routine = await create_routine_task(
        user_id=user_id,
        workspace_id=workspace_id,
        thread_id=thread.id,
        instruction="Read the workspace.",
        cron="0 8 * * *",
    )

    # Access currently valid (personal workspace owner).
    assert await _permission_gate(routine) is None

    # Point at a workspace the user has no access to.
    routine.workspace_id = "ws_does_not_exist"
    reason = await _permission_gate(routine)
    assert reason is not None
    assert "workspace" in reason


@pytest.mark.asyncio
async def test_permission_gate_blocks_when_thread_archived():
    """An archived bound ChatThread stops the run — nowhere left to post the reply."""
    from app.agentive.services.routine_tasks import create_routine_task
    from app.services.chat_threads import archive_thread
    from app.services.routine_task_scheduler import _permission_gate

    user_id, workspace_id, _track_id, thread = (
        await _bootstrap_user_workspace_track_thread("routine-thread@example.com")
    )
    routine = await create_routine_task(
        user_id=user_id,
        workspace_id=workspace_id,
        thread_id=thread.id,
        instruction="Post here.",
        cron="0 8 * * *",
    )

    await archive_thread(thread)
    reason = await _permission_gate(routine)
    assert reason is not None
    assert "thread" in reason


@pytest.mark.asyncio
async def test_write_scope_reconciliation_auto_applies_allowlisted_only():
    """A pending token targeting an allowlisted entry auto-applies; one
    targeting a different entry is left pending; a delete-class kind never
    auto-applies even when its target is allowlisted."""
    from app.agentive.services.routine_tasks import create_routine_task
    from app.agentive.staging import create_staged_change, get_token
    from app.services.routine_task_scheduler import _reconcile_write_scope

    user_id, workspace_id, track_id, thread = (
        await _bootstrap_user_workspace_track_thread("routine-reconcile@example.com")
    )

    from app.agentive.tooling.invoke import invoke_route_in_process
    from app.api.entries import create_entry

    allowed_entry = await invoke_route_in_process(
        create_entry,
        principal_id=user_id,
        scope=workspace_id,
        track_id=track_id,
        title="Allowed entry",
    )
    other_entry = await invoke_route_in_process(
        create_entry,
        principal_id=user_id,
        scope=workspace_id,
        track_id=track_id,
        title="Other entry",
    )
    allowed_id = allowed_entry["entry"]["id"]
    other_id = other_entry["entry"]["id"]

    routine = await create_routine_task(
        user_id=user_id,
        workspace_id=workspace_id,
        thread_id=thread.id,
        instruction="Keep the allowed entry updated.",
        cron="0 8 * * *",
        write_scope=[{"resource_type": "entry", "resource_id": allowed_id}],
    )

    session_id = thread.provider_session_id

    allowed_sc = await create_staged_change(
        user_id=user_id,
        session_id=session_id,
        kind="update_entry",
        summary="update allowed",
        diff_human="update allowed",
        diff_machine={},
        payload={"entry_id": allowed_id, "title": "Updated"},
    )
    other_sc = await create_staged_change(
        user_id=user_id,
        session_id=session_id,
        kind="update_entry",
        summary="update other",
        diff_human="update other",
        diff_machine={},
        payload={"entry_id": other_id, "title": "Updated"},
    )
    delete_sc = await create_staged_change(
        user_id=user_id,
        session_id=session_id,
        kind="delete_entry",
        summary="delete allowed",
        diff_human="delete allowed",
        diff_machine={},
        payload={"entry_id": allowed_id},
    )

    await _reconcile_write_scope(routine)

    allowed_after = await get_token(allowed_sc.token)
    other_after = await get_token(other_sc.token)
    delete_after = await get_token(delete_sc.token)

    assert allowed_after.state == "consumed"  # auto-applied
    assert other_after.state == "pending"  # outside write_scope — untouched
    assert delete_after.state == "pending"  # delete-class — never auto-applied


@pytest.mark.asyncio
async def test_auto_pause_after_consecutive_failures(monkeypatch):
    """N consecutive failed runs auto-pause the routine and record last_run_status."""
    from app.agentive.nodes import RoutineTask
    from app.agentive.services.routine_tasks import create_routine_task
    from app.services import routine_task_scheduler as sched

    user_id, workspace_id, _track_id, thread = (
        await _bootstrap_user_workspace_track_thread("routine-fail@example.com")
    )
    routine = await create_routine_task(
        user_id=user_id,
        workspace_id=workspace_id,
        thread_id=thread.id,
        instruction="Always fails.",
        cron="0 8 * * *",
    )

    async def _always_fail(_task):
        return False, "simulated failure"

    monkeypatch.setattr(sched, "_run_agent_turn", _always_fail)

    for _ in range(sched._MAX_CONSECUTIVE_FAILURES):
        await sched._run_one(routine.id)

    final = await RoutineTask.get(routine.id)
    assert final.status == "paused"
    assert final.last_run_status == "error"
    assert final.consecutive_failures >= sched._MAX_CONSECUTIVE_FAILURES


@pytest.mark.asyncio
async def test_run_scheduler_pass_advances_next_run_at_and_dispatches(monkeypatch):
    """A forced-due routine gets next_run_at advanced BEFORE dispatch and is fired exactly once."""
    import asyncio

    from app.agentive.nodes import RoutineTask
    from app.agentive.services.routine_tasks import create_routine_task
    from app.services import routine_task_scheduler as sched
    from app.utils.time import utc_now_iso

    user_id, workspace_id, _track_id, thread = (
        await _bootstrap_user_workspace_track_thread("routine-tick@example.com")
    )
    routine = await create_routine_task(
        user_id=user_id,
        workspace_id=workspace_id,
        thread_id=thread.id,
        instruction="Tick test.",
        cron="0 8 * * *",
    )
    # Force it due right now.
    routine.next_run_at = utc_now_iso()
    await routine.save()
    original_next_run_at = routine.next_run_at

    dispatched_ids = []

    async def _fake_run_one(task_id):
        dispatched_ids.append(task_id)

    monkeypatch.setattr(sched, "_run_one", _fake_run_one)

    n = await sched.run_scheduler_pass()

    assert n == 1
    updated = await RoutineTask.get(routine.id)
    assert updated.next_run_at != original_next_run_at  # advanced before dispatch

    # Let the fire-and-forget task run.
    await asyncio.sleep(0.05)
    assert routine.id in dispatched_ids


# ---------------------------------------------------------------------------
# Run-count dimension (max_runs / run_count)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_create_routine_task_rejects_bad_max_runs():
    """max_runs=0 (and other non-positive values) raise BadRequestError."""
    from app.agentive.services.routine_tasks import create_routine_task
    from app.api.errors import BadRequestError

    user_id, workspace_id, _track_id, thread = (
        await _bootstrap_user_workspace_track_thread("routine-maxruns-bad@example.com")
    )
    with pytest.raises(BadRequestError):
        await create_routine_task(
            user_id=user_id,
            workspace_id=workspace_id,
            thread_id=thread.id,
            instruction="Bad max_runs.",
            cron="0 8 * * *",
            max_runs=0,
        )


@pytest.mark.asyncio
async def test_only_successful_runs_consume_max_runs_budget(monkeypatch):
    """A failed attempt doesn't count toward max_runs — only actual successes do."""
    from app.agentive.nodes import RoutineTask
    from app.agentive.services.routine_tasks import create_routine_task
    from app.services import routine_task_scheduler as sched

    user_id, workspace_id, _track_id, thread = (
        await _bootstrap_user_workspace_track_thread(
            "routine-maxruns-budget@example.com"
        )
    )
    routine = await create_routine_task(
        user_id=user_id,
        workspace_id=workspace_id,
        thread_id=thread.id,
        instruction="Capped routine.",
        cron="0 8 * * *",
        max_runs=2,
    )

    async def _fail(_task):
        return False, "simulated failure"

    monkeypatch.setattr(sched, "_run_agent_turn", _fail)
    await sched._run_one(routine.id)

    after_failure = await RoutineTask.get(routine.id)
    assert after_failure.run_count == 0
    assert after_failure.status == "active"

    async def _succeed(_task):
        return True, None

    monkeypatch.setattr(sched, "_run_agent_turn", _succeed)
    await sched._run_one(routine.id)
    after_one_success = await RoutineTask.get(routine.id)
    assert after_one_success.run_count == 1
    assert after_one_success.status == "active"

    await sched._run_one(routine.id)
    after_two_successes = await RoutineTask.get(routine.id)
    assert after_two_successes.run_count == 2
    assert after_two_successes.status == "completed"


@pytest.mark.asyncio
async def test_completed_routine_no_longer_dispatched(monkeypatch):
    """A routine that reached max_runs never gets picked up again, even if forced due."""
    from app.agentive.nodes import RoutineTask
    from app.agentive.services.routine_tasks import create_routine_task
    from app.services import routine_task_scheduler as sched
    from app.utils.time import utc_now_iso

    user_id, workspace_id, _track_id, thread = (
        await _bootstrap_user_workspace_track_thread(
            "routine-maxruns-nodispatch@example.com"
        )
    )
    routine = await create_routine_task(
        user_id=user_id,
        workspace_id=workspace_id,
        thread_id=thread.id,
        instruction="One-shot routine.",
        cron="0 8 * * *",
        max_runs=1,
    )

    async def _succeed(_task):
        return True, None

    monkeypatch.setattr(sched, "_run_agent_turn", _succeed)
    await sched._run_one(routine.id)
    completed = await RoutineTask.get(routine.id)
    assert completed.status == "completed"

    # Force it "due" again and run a pass — the scheduler's query is
    # status="active", so a completed routine must never be picked up.
    completed.next_run_at = utc_now_iso()
    await completed.save()
    n = await sched.run_scheduler_pass()
    assert n == 0


@pytest.mark.asyncio
async def test_resuming_completed_routine_resets_run_count():
    """Reactivating a completed routine restarts its run count from 0."""
    from app.agentive.services.routine_tasks import (
        create_routine_task,
        update_routine_task,
    )

    user_id, workspace_id, _track_id, thread = (
        await _bootstrap_user_workspace_track_thread(
            "routine-maxruns-resume@example.com"
        )
    )
    routine = await create_routine_task(
        user_id=user_id,
        workspace_id=workspace_id,
        thread_id=thread.id,
        instruction="Resumable routine.",
        cron="0 8 * * *",
        max_runs=1,
    )
    routine.status = "completed"
    routine.run_count = 1
    await routine.save()

    resumed = await update_routine_task(
        user_id=user_id, routine_id=routine.id, status="active"
    )
    assert resumed.status == "active"
    assert resumed.run_count == 0


@pytest.mark.asyncio
async def test_clear_max_runs_reverts_to_open_ended():
    """clear_max_runs=True reverts a capped routine to max_runs=None."""
    from app.agentive.services.routine_tasks import (
        create_routine_task,
        update_routine_task,
    )

    user_id, workspace_id, _track_id, thread = (
        await _bootstrap_user_workspace_track_thread(
            "routine-maxruns-clear@example.com"
        )
    )
    routine = await create_routine_task(
        user_id=user_id,
        workspace_id=workspace_id,
        thread_id=thread.id,
        instruction="Capped then uncapped.",
        cron="0 8 * * *",
        max_runs=3,
    )
    updated = await update_routine_task(
        user_id=user_id, routine_id=routine.id, clear_max_runs=True
    )
    assert updated.max_runs is None


@pytest.mark.asyncio
async def test_schedule_task_stages_max_runs_in_diff_and_payload():
    """The staged card names the run-count cap; the create executor persists it."""
    user_id, workspace_id, _track_id, thread = (
        await _bootstrap_user_workspace_track_thread(
            "routine-maxruns-stage@example.com"
        )
    )
    from app.agentive.nodes import RoutineTask

    result = await dispatch_tool(
        "integral_schedule_task",
        {
            "instruction": "Capped check-in.",
            "cron": "*/1 * * * *",
            "max_runs": 5,
        },
        principal_id=user_id,
        scope=workspace_id,
        session_id=thread.provider_session_id,
    )
    assert not result.is_error, result
    assert "5 run(s)" in result.data["diff_human"]

    from app.agentive.services.staging_apply import bless_and_execute

    bless_result = await bless_and_execute(user_id=user_id, token=result.data["token"])
    assert not bless_result["execute_result"].get("error"), bless_result

    routines = await RoutineTask.find({"user_id": user_id})
    assert len(routines) == 1
    assert routines[0].max_runs == 5


# ---------------------------------------------------------------------------
# One-shot via run_at
# ---------------------------------------------------------------------------


def test_parse_run_at_rejects_past():
    """Past absolute fire times are rejected at create/stage time."""
    from datetime import datetime, timedelta, timezone

    from app.agentive.services.routine_tasks import parse_run_at
    from app.api.errors import BadRequestError

    past = (datetime.now(timezone.utc) - timedelta(minutes=1)).isoformat()
    with pytest.raises(BadRequestError):
        parse_run_at(past)


def test_compute_next_run_at_rejects_empty_cron():
    """Empty cron cannot drive croniter — one-shots use run_at instead."""
    from app.agentive.services.routine_tasks import compute_next_run_at
    from app.api.errors import BadRequestError

    with pytest.raises(BadRequestError):
        compute_next_run_at("", "UTC")


@pytest.mark.asyncio
async def test_schedule_task_run_at_stages_then_bless_creates_one_shot():
    """run_at (no cron) → staged one-shot with max_runs=1 and absolute next_run_at."""
    from datetime import datetime, timedelta, timezone

    from app.agentive.nodes import RoutineTask

    user_id, workspace_id, _track_id, thread = (
        await _bootstrap_user_workspace_track_thread("routine-runat@example.com")
    )
    run_at = (datetime.now(timezone.utc) + timedelta(minutes=5)).isoformat()

    result = await dispatch_tool(
        "integral_schedule_task",
        {
            "instruction": "Remind the user to join standup.",
            "run_at": run_at,
        },
        principal_id=user_id,
        scope=workspace_id,
        session_id=thread.provider_session_id,
    )
    assert not result.is_error, result
    assert "One-shot at:" in result.data["diff_human"]
    assert "after 1 run(s)" in result.data["diff_human"]

    from app.agentive.services.staging_apply import bless_and_execute

    bless_result = await bless_and_execute(user_id=user_id, token=result.data["token"])
    assert not bless_result["execute_result"].get("error"), bless_result

    routines = await RoutineTask.find({"user_id": user_id})
    assert len(routines) == 1
    routine = routines[0]
    assert routine.cron == ""
    assert routine.max_runs == 1
    assert routine.status == "active"
    assert routine.next_run_at is not None
    assert routine.thread_id == thread.id


@pytest.mark.asyncio
async def test_schedule_task_rejects_cron_and_run_at_together():
    """XOR: cron and run_at cannot both be set on the same schedule call."""
    user_id, workspace_id, _track_id, thread = (
        await _bootstrap_user_workspace_track_thread("routine-xor@example.com")
    )
    from datetime import datetime, timedelta, timezone

    result = await dispatch_tool(
        "integral_schedule_task",
        {
            "instruction": "Nope.",
            "cron": "0 9 * * *",
            "run_at": (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat(),
        },
        principal_id=user_id,
        scope=workspace_id,
        session_id=thread.provider_session_id,
    )
    assert result.is_error


@pytest.mark.asyncio
async def test_run_scheduler_pass_clears_next_run_at_for_one_shot(monkeypatch):
    """Empty-cron due rows clear next_run_at (no croniter) then dispatch once."""
    import asyncio
    from datetime import datetime, timedelta, timezone

    from app.agentive.nodes import RoutineTask
    from app.agentive.services.routine_tasks import create_routine_task
    from app.services import routine_task_scheduler as sched
    from app.utils.time import utc_now_iso

    user_id, workspace_id, _track_id, thread = (
        await _bootstrap_user_workspace_track_thread("routine-oneshot-tick@example.com")
    )
    run_at = (datetime.now(timezone.utc) + timedelta(minutes=10)).isoformat()
    routine = await create_routine_task(
        user_id=user_id,
        workspace_id=workspace_id,
        thread_id=thread.id,
        instruction="One-shot tick.",
        run_at=run_at,
    )
    routine.next_run_at = utc_now_iso()
    await routine.save()

    dispatched_ids = []

    async def _fake_run_one(task_id):
        dispatched_ids.append(task_id)

    monkeypatch.setattr(sched, "_run_one", _fake_run_one)

    n = await sched.run_scheduler_pass()
    assert n == 1
    updated = await RoutineTask.get(routine.id)
    assert updated.next_run_at is None
    await asyncio.sleep(0.05)
    assert routine.id in dispatched_ids
