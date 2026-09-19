"""Background loop executing due ``RoutineTask`` rows (user-issued recurring
chat instructions — Routine Tasks feature).

Fourth instance of the ``sync_loop`` / ``approval_ttl_loop`` /
``app_install_reaper`` ``asyncio.create_task`` background-loop pattern (see
``app_install_reaper.py``'s module docstring for the precedent). jvspatial
ships its own ``SchedulerService`` (``jvspatial/api/integrations/scheduler``)
but it was deliberately NOT used here:

1. Its own ``ScheduledTask`` model is explicitly documented as "not a graph
   Node — scheduler tasks are runtime constructs" (in-memory only, wiped on
   restart) — adopting it would not remove the need for this module's own
   persisted-Node + re-query-every-tick approach.
2. It executes on a background OS thread (the ``schedule`` package +
   ``ThreadPoolExecutor``), not Integral's asyncio loop — a thread-affinity
   risk for async DB drivers on a feature whose entire job is triggering
   user writes.
3. Its timezone is one global setting on the whole service, not per-task —
   doesn't fit "9am in THIS user's timezone."

This loop is fully async-native and reuses the EXISTING agent-turn
machinery via in-process ``invoke_route_in_process(agent_turn, …)`` —
no HTTP loopback / service-auth dependency. See ``_run_one`` below.
"""

from __future__ import annotations

import asyncio
import logging
import os
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Literal, Optional, Set, Tuple

from app.agentive.nodes import RoutineTask
from app.utils.time import utc_now, utc_now_iso

logger = logging.getLogger(__name__)

_DEFAULT_INTERVAL_SECONDS = 60
_MAX_CONSECUTIVE_FAILURES = 5
# Upper bound on routines executing at once across ALL users (each run holds
# an LLM stream). Per-user dispatch is additionally serialized — see
# ``run_scheduler_pass`` — so one user's N due routines never trip their own
# ``MAX_CONCURRENT_TURNS_PER_USER`` cap.
_DEFAULT_MAX_PARALLEL_RUNS = 4
# A run refused because the bound thread is busy (or the user is at their
# turn cap) is NOT a failure — retry shortly instead of waiting for the next
# cron slot, and never count it toward auto-pause.
_SKIP_RETRY_SECONDS = 120

# Fixed marker prepended to every replayed instruction so the model can tell
# a scheduled replay from a fresh live ask (integral_scheduling/SKILL.md
# documents the duplicate-routine failure mode when this signal is absent).
SCHEDULED_REPLAY_MARKER = (
    "[Scheduled routine replay — perform the task; do not create or modify routines]"
)


class _TurnBusy(Exception):
    """Raised by ``_run_agent_turn`` when ``acquire_turn`` refused admission."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


def _interval_seconds() -> int:
    env_val = os.environ.get("ROUTINE_TASK_SCHEDULER_INTERVAL_SECONDS")
    if env_val is not None:
        try:
            return int(env_val)
        except ValueError:
            pass
    return _DEFAULT_INTERVAL_SECONDS


def _max_parallel_runs() -> int:
    env_val = os.environ.get("ROUTINE_TASK_SCHEDULER_MAX_PARALLEL_RUNS")
    if env_val is not None:
        try:
            return max(int(env_val), 1)
        except ValueError:
            pass
    return _DEFAULT_MAX_PARALLEL_RUNS


def replay_prompt(task: RoutineTask) -> str:
    """The exact prompt a scheduled run hands the agent (marker + instruction)."""
    return f"{SCHEDULED_REPLAY_MARKER}\n{task.instruction}"


async def _permission_gate(task: RoutineTask) -> Optional[str]:
    """Return a failure reason string if the run must NOT proceed, else None.

    Fail-closed re-check of the CREATING user's LIVE access — never trusts a
    cached value from creation time. A revoked workspace membership or
    write-scope resource must stop the routine immediately, not on some
    future audit pass.
    """
    from app.services.workspace_permissions import can_access_workspace

    if task.workspace_id:
        workspace_role = await can_access_workspace(task.user_id, task.workspace_id)
        if workspace_role == "none":
            return f"user no longer has access to workspace {task.workspace_id}"

    if task.write_scope:
        from app.services.permissions import resolve_role

        for grant in task.write_scope:
            resource_type = grant.get("resource_type")
            resource_id = grant.get("resource_id")
            if not resource_type or not resource_id:
                # Fail closed. This gate's contract is to stop the routine on
                # anything it cannot positively verify; skipping a malformed
                # grant silently treated it as passing. ``write_scope`` is
                # model-authored via integral_create_routine and stored
                # verbatim, so a typeless entry is exactly the case that must
                # not slip through.
                return f"write_scope grant is malformed ({grant!r})"
            resource_role = await resolve_role(task.user_id, resource_type, resource_id)
            if resource_role is None:
                return (
                    f"user no longer has access to {resource_type} {resource_id} "
                    "(write_scope grant)"
                )

    from app.models.nodes import App, ChatThread

    source_app_id = str(getattr(task, "source_app_id", "") or "").strip()
    if source_app_id:
        app = await App.get(source_app_id)
        if app is None or str(getattr(app, "lifecycle_state", "") or "") != "active":
            state = getattr(app, "lifecycle_state", "missing") if app else "missing"
            return f"source app {source_app_id} is not active (state={state})"

    thread = await ChatThread.get(task.thread_id)
    if thread is None or getattr(thread, "archived", False):
        return "bound chat thread was deleted or archived"

    return None


async def _notify_auto_paused(task: RoutineTask, *, reason: str) -> None:
    try:
        from app.services.notification_router import dispatch as notify_dispatch

        await notify_dispatch(
            user_id=task.user_id,
            kind="system",
            payload={
                "summary": "A routine was paused",
                "content": (
                    f"Your routine “{task.instruction[:60]}” was "
                    f"automatically paused: {reason}. Resume it from chat "
                    "once you've resolved the issue."
                ),
            },
            actor_id="system",
            actor_kind="system",
        )
    except Exception:  # noqa: BLE001
        logger.warning(
            "routine_task_scheduler: notify_auto_paused failed for %s",
            task.id,
            exc_info=True,
        )


async def _emit_run_event(
    task: RoutineTask,
    *,
    action: Literal[
        "routine_task.run_completed",
        "routine_task.auto_paused",
        "routine_task.completed",
    ],
    details: Dict[str, Any],
) -> None:
    try:
        from app.services.change_event import emit_change_event

        await emit_change_event(
            actor_kind="agent",
            actor_id=task.user_id,
            action=action,
            resource_type="RoutineTask",
            resource_id=task.id,
            before=None,
            after={
                "id": task.id,
                "status": task.status,
                "last_run_status": task.last_run_status,
            },
            scope=f"workspace:{task.workspace_id}",
            details={"routine_task_id": task.id, **details},
        )
    except Exception:  # noqa: BLE001
        logger.warning(
            "routine_task_scheduler: emit_change_event failed for %s",
            task.id,
            exc_info=True,
        )


async def _auto_pause(task: RoutineTask, *, reason: str) -> None:
    task.status = "paused"
    task.last_run_status = "error"
    task.last_run_error = reason
    task.updated_at = utc_now_iso()
    await task.save()
    await _emit_run_event(
        task, action="routine_task.auto_paused", details={"reason": reason}
    )
    await _notify_auto_paused(task, reason=reason)
    logger.warning("routine_task_scheduler: auto-paused %s: %s", task.id, reason)


async def _run_agent_turn(task: RoutineTask) -> Tuple[bool, Optional[str]]:
    """Run one routine as an in-process agent-turn (no HTTP loopback).

    Uses ``invoke_route_in_process`` under the task owner's principal so we
    do not depend on ``INTEGRAL_SERVICE_KEY`` / JWT handoff for
    ``POST /chat/threads/{id}/agent-turn`` — that HTTP path 401'd in
    deployments where ServiceAuth middleware is missing or mis-ordered
    relative to jvspatial auth (error_code ``authentication_required``).

    Drains the SSE stream to completion; message persistence is a side
    effect inside ``generate_chat_turn_sse``.
    """
    from app.agentive.tooling.invoke import invoke_route_in_process
    from app.api.ai_chat import agent_turn
    from app.api.errors import JVSpatialAPIException, ResourceConflictError

    prompt = replay_prompt(task)
    try:
        result = await invoke_route_in_process(
            agent_turn,
            principal_id=task.user_id,
            scope=task.workspace_id or None,
            thread_id=task.thread_id,
            prompt=prompt,
            origin="routine_task",
            json_body={
                "prompt": prompt,
                "origin": "routine_task",
            },
        )
    except ResourceConflictError as exc:
        # ``acquire_turn`` admission refusals (thread already answering / user
        # at the concurrent-turn cap) are transient — not a failed run.
        reason = str((getattr(exc, "details", None) or {}).get("reason") or "")
        if reason in ("thread_busy", "user_turn_limit"):
            logger.info(
                "routine_task_scheduler: agent-turn deferred for %s (%s)",
                task.id,
                reason,
            )
            raise _TurnBusy(reason) from exc
        logger.warning(
            "routine_task_scheduler: agent-turn rejected for %s: %s",
            task.id,
            exc,
        )
        return False, f"Agent turn rejected: {getattr(exc, 'message', str(exc))}"
    except JVSpatialAPIException as exc:
        logger.warning(
            "routine_task_scheduler: agent-turn rejected for %s: %s",
            task.id,
            exc,
        )
        return False, f"Agent turn rejected: {getattr(exc, 'message', str(exc))}"
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "routine_task_scheduler: agent-turn failed for %s: %s",
            task.id,
            exc,
            exc_info=True,
        )
        return False, f"Agent turn failed: {exc}"

    if isinstance(result, dict) and result.get("error"):
        msg = result.get("message") or result.get("error_code") or "Agent turn failed"
        return False, str(msg)

    # StreamingResponse — drain so persistence side-effects complete.
    body_iterator = getattr(result, "body_iterator", None)
    if body_iterator is not None:
        try:
            async for _chunk in body_iterator:
                pass
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "routine_task_scheduler: agent-turn stream failed for %s: %s",
                task.id,
                exc,
                exc_info=True,
            )
            return False, f"Agent turn stream failed: {exc}"
    return True, None


async def _snapshot_pending_tokens(task: RoutineTask) -> Optional[Set[str]]:
    """Tokens already pending on the routine's session BEFORE its turn runs.

    Anything in this set was staged by someone else (typically the user, in
    the same chat thread) and has not been reviewed — the routine's
    pre-approved ``write_scope`` must never bless it.

    Fail-CLOSED: returns ``None`` when the snapshot could not be taken. An
    empty set means "observed, nothing was pending"; ``None`` means "unknown",
    and the caller MUST skip reconciliation entirely rather than treat every
    card in the session as the routine's to bless.
    """
    if not task.write_scope:
        return set()
    try:
        from app.agentive.staging import list_pending_tokens
        from app.models.nodes import ChatThread

        thread = await ChatThread.get(task.thread_id)
        if thread is None:
            return set()
        pending = await list_pending_tokens(task.user_id, thread.provider_session_id)
        return {sc.token for sc in pending}
    except Exception:  # noqa: BLE001
        logger.warning(
            "routine_task_scheduler: pending-token snapshot failed for %s — "
            "write-scope reconciliation will be skipped for this run",
            task.id,
            exc_info=True,
        )
        return None


def _minted_during_turn(created_at: Any, turn_started_at: datetime) -> bool:
    """True only when ``created_at`` is provably at/after the turn start.

    Fail-closed: an absent or unparseable ``created_at`` returns False, so an
    undatable card is left pending for manual review rather than blessed.
    """
    if not isinstance(created_at, datetime):
        return False
    if created_at.tzinfo is None:
        created_at = created_at.replace(tzinfo=timezone.utc)
    if turn_started_at.tzinfo is None:
        turn_started_at = turn_started_at.replace(tzinfo=timezone.utc)
    return created_at >= turn_started_at


async def _reconcile_write_scope(
    task: RoutineTask,
    *,
    preexisting_tokens: Optional[Set[str]] = None,
    turn_started_at: Optional[datetime] = None,
) -> None:
    """Auto-apply pending StagedChanges the run just minted, IF allowlisted.

    Fail-closed: a token whose payload doesn't carry a matchable
    ``entry_id``/``track_id``, or one for a ``kind`` outside the routine's
    ``write_scope``, is left pending for manual review — never silently
    dropped, never silently applied.

    ``preexisting_tokens`` (from ``_snapshot_pending_tokens`` taken before the
    turn) are skipped outright: a card the user staged in the same thread and
    has not reviewed is not the routine's to bless, even when its target is
    inside ``write_scope``.

    ``turn_started_at`` is the second, independent gate: the skip-set is
    provenance-by-inference (a pre-turn observation, not something recorded on
    the card), so a card minted before the turn but missed by the snapshot —
    or one staged by the user mid-run — would otherwise be eligible. A card
    whose ``created_at`` predates the turn is never the routine's to bless.
    """
    if not task.write_scope:
        return

    from app.agentive.staging import list_pending_tokens
    from app.agentive.staging_executors import (
        _DELETE_KINDS,  # type: ignore[attr-defined]
    )
    from app.models.nodes import ChatThread

    thread = await ChatThread.get(task.thread_id)
    if thread is None:
        return

    tokens = await list_pending_tokens(task.user_id, thread.provider_session_id)
    if not tokens:
        return

    allowed_ids = {
        g.get("resource_id") for g in task.write_scope if g.get("resource_id")
    }
    skip_tokens = preexisting_tokens or set()

    from app.agentive.services.staging_apply import bless_and_execute

    for sc in tokens:
        if sc.token in skip_tokens:
            continue  # staged before this run — not ours to bless
        if turn_started_at is not None and not _minted_during_turn(
            sc.created_at, turn_started_at
        ):
            continue  # predates the turn — the routine did not mint it
        if sc.kind in _DELETE_KINDS:
            continue  # v1 hard rule: delete-class ops never auto-apply
        target = sc.payload.get("entry_id") or sc.payload.get("track_id")
        if not target or target not in allowed_ids:
            continue
        try:
            await bless_and_execute(
                user_id=task.user_id, token=sc.token, autonomy="single"
            )
            logger.info(
                "routine_task_scheduler: auto-applied token=%s kind=%s for routine=%s",
                sc.token,
                sc.kind,
                task.id,
            )
        except Exception:  # noqa: BLE001
            logger.warning(
                "routine_task_scheduler: auto-apply failed token=%s routine=%s",
                sc.token,
                task.id,
                exc_info=True,
            )


async def _record_failed_run(task: RoutineTask, *, reason: str) -> None:
    """Persist one failed attempt; auto-pause at the consecutive-failure cap."""
    task.last_run_status = "error"
    task.last_run_error = reason
    task.consecutive_failures += 1
    # One-shots clear next_run_at on dispatch; re-arm a short retry so a
    # transient failure can still consume the max_runs budget.
    if not (task.cron or "").strip() and task.status == "active":
        retry_iso = (utc_now() + timedelta(seconds=_SKIP_RETRY_SECONDS)).isoformat()
        task.next_run_at = retry_iso
    if task.consecutive_failures >= _MAX_CONSECUTIVE_FAILURES:
        await task.save()
        await _auto_pause(
            task,
            reason=f"{task.consecutive_failures} consecutive failed runs",
        )
        return
    await task.save()
    await _emit_run_event(
        task,
        action="routine_task.run_completed",
        details={"status": "error", "reason": task.last_run_error},
    )


async def _record_skipped_run(task: RoutineTask, *, reason: str) -> None:
    """A run refused at admission (thread busy / user turn cap) is deferred,
    not failed: no failure increment, retry after a short delay — but never
    later than the cron slot already computed for it."""
    task.last_run_at = utc_now_iso()
    task.last_run_status = "skipped"
    task.last_run_error = None
    retry_iso = (utc_now() + timedelta(seconds=_SKIP_RETRY_SECONDS)).isoformat()
    if not task.next_run_at or retry_iso < task.next_run_at:
        task.next_run_at = retry_iso
    task.updated_at = utc_now_iso()
    await task.save()
    logger.info(
        "routine_task_scheduler: run skipped for %s (%s) — retry at %s",
        task.id,
        reason,
        task.next_run_at,
    )
    await _emit_run_event(
        task,
        action="routine_task.run_completed",
        details={"status": "skipped", "reason": reason},
    )


async def _run_one(task_id: str) -> None:
    """Execute one due routine. Isolated per-task so one failure never
    affects sibling tasks dispatched in the same tick.

    Nothing awaits the dispatched task (see ``run_scheduler_pass``), so an
    exception escaping here would otherwise vanish after ``next_run_at`` was
    already advanced — the run silently lost. Top-level guard records it on
    the row like any other failed attempt.
    """
    task = await RoutineTask.get(task_id)
    if task is None or task.status != "active":
        return
    try:
        await _execute_run(task)
    except Exception as exc:  # noqa: BLE001 — never lose a run silently
        logger.warning(
            "routine_task_scheduler: run crashed for %s: %s",
            task_id,
            exc,
            exc_info=True,
        )
        try:
            fresh = await RoutineTask.get(task_id) or task
            if fresh.status != "active":
                return
            fresh.last_run_at = utc_now_iso()
            await _record_failed_run(fresh, reason=f"Run crashed: {exc}")
        except Exception:  # noqa: BLE001
            logger.warning(
                "routine_task_scheduler: failed to record crash for %s",
                task_id,
                exc_info=True,
            )


async def _execute_run(task: RoutineTask) -> None:
    reason = await _permission_gate(task)
    if reason is not None:
        await _auto_pause(task, reason=reason)
        return

    # Snapshot BEFORE the turn: only tokens minted by this run may be
    # auto-applied under write_scope (C1). ``None`` = snapshot failed →
    # reconciliation is skipped entirely (fail closed).
    preexisting_tokens = await _snapshot_pending_tokens(task)
    turn_started_at = utc_now()
    # Slot identity includes run bookkeeping so a failed fire does not block
    # the next attempt that still shares ``next_run_at`` (tests / busy skip).
    # Mid-flight crash recovery still sees the same key because
    # consecutive_failures / run_count / last_run_at are unchanged until
    # bookkeeping completes.
    scheduled_for = (
        f"{task.next_run_at or utc_now_iso()}"
        f"#{int(task.consecutive_failures or 0)}"
        f":{int(task.run_count or 0)}"
        f":{task.last_run_at or ''}"
    )

    from app.agentive.services import routine_tasks as routine_svc
    from app.agentive.services import work_worker
    from app.schemas.agentive.work import WorkError

    try:
        work = await routine_svc.enqueue_routine_turn_work(
            routine_id=task.id,
            principal_id=task.user_id,
            workspace_id=task.workspace_id or "",
            scheduled_for=scheduled_for,
            thread_id=task.thread_id or "",
            app_id=getattr(task, "app_id", "") or "",
        )
        done = await work_worker.process_one_due_item(
            worker_id=f"routine-scheduler:{task.id}",
            work_item_id=work.work_item_id,
            lease_seconds=120,
        )
    except _TurnBusy as busy:
        await _record_skipped_run(task, reason=busy.reason)
        return
    except WorkError as exc:
        if exc.code == "work.lease_lost":
            await _record_skipped_run(task, reason="work_lease_lost")
            return
        success, error_reason = False, exc.message
        done = None
    else:
        if done is None:
            success, error_reason = False, "work item not claimed"
        elif done.status == "succeeded":
            success, error_reason = True, None
        elif done.status == "retry_wait":
            fail_code = (done.failure or {}).get("code") or ""
            if fail_code in {"work.turn_busy", "work.lease_lost"}:
                await _record_skipped_run(
                    task,
                    reason=(done.failure or {}).get("message") or fail_code,
                )
                return
            success = False
            error_reason = (done.failure or {}).get("message") or "retry_wait"
        else:
            success = False
            error_reason = (done.failure or {}).get("message") or done.status

    # Stop / Remove may have cancelled or deleted the row while the turn ran.
    # Do not record success/failure against a row the user already stopped —
    # that would bump consecutive_failures into an auto-pause after an abort.
    fresh = await RoutineTask.get(task.id)
    if fresh is None:
        logger.info(
            "routine_task_scheduler: routine %s deleted mid-run — discarding result",
            task.id,
        )
        return
    if fresh.status != "active":
        logger.info(
            "routine_task_scheduler: routine %s status=%s mid-run — discarding result",
            task.id,
            fresh.status,
        )
        return
    task = fresh
    task.last_run_at = utc_now_iso()

    if success:
        task.last_run_status = "success"
        task.last_run_error = None
        task.consecutive_failures = 0
        # Run-count dimension: only a SUCCESSFUL run consumes the budget —
        # a failed attempt didn't do the work the user asked for N of.
        just_completed = False
        if task.max_runs is not None:
            task.run_count += 1
            if task.run_count >= task.max_runs:
                task.status = "completed"
                just_completed = True
        await task.save()
        logger.info(
            "routine_task_scheduler: run succeeded for %s (run_count=%s/%s)",
            task.id,
            task.run_count,
            task.max_runs if task.max_runs is not None else "∞",
        )
        if preexisting_tokens is None:
            # Fail closed — without the pre-turn snapshot we cannot tell the
            # routine's own cards from the user's, and blessing the user's
            # unreviewed cards is worse than leaving ours pending.
            logger.warning(
                "routine_task_scheduler: skipping write-scope reconciliation for "
                "%s — pre-turn pending-token snapshot unavailable",
                task.id,
            )
        else:
            try:
                await _reconcile_write_scope(
                    task,
                    preexisting_tokens=preexisting_tokens,
                    turn_started_at=turn_started_at,
                )
            except Exception:  # noqa: BLE001
                logger.warning(
                    "routine_task_scheduler: write-scope reconciliation failed for %s",
                    task.id,
                    exc_info=True,
                )
        await _emit_run_event(
            task, action="routine_task.run_completed", details={"status": "success"}
        )
        if just_completed:
            await _emit_run_event(
                task,
                action="routine_task.completed",
                details={"run_count": task.run_count, "max_runs": task.max_runs},
            )
            logger.info(
                "routine_task_scheduler: %s reached max_runs=%s — marked completed",
                task.id,
                task.max_runs,
            )
    else:
        await _record_failed_run(task, reason=error_reason or "Agent turn failed")


# ---------------------------------------------------------------------------
# Dispatch bookkeeping — in-flight set + global slot bound + per-user serial
# ---------------------------------------------------------------------------

_in_flight: Set[asyncio.Task] = set()
_run_slots: Optional[asyncio.Semaphore] = None
_run_slots_loop: Optional[asyncio.AbstractEventLoop] = None


def _get_run_slots() -> asyncio.Semaphore:
    """Lazily build the parallel-run semaphore, rebinding if the loop changed."""
    global _run_slots, _run_slots_loop
    loop = asyncio.get_running_loop()
    if _run_slots is None or _run_slots_loop is not loop:
        _run_slots = asyncio.Semaphore(_max_parallel_runs())
        _run_slots_loop = loop
    return _run_slots


def in_flight_count() -> int:
    """Number of dispatched run batches still executing (tests / diagnostics)."""
    return len(_in_flight)


async def _run_user_batch(user_id: str, task_ids: List[str]) -> None:
    """Run one user's due routines one after another, each under a global slot.

    Serializing per user keeps N due routines from racing each other into
    ``acquire_turn``'s per-user concurrent-turn cap; the semaphore bounds
    total parallel LLM streams across users.
    """
    for task_id in task_ids:
        async with _get_run_slots():
            await _run_one(task_id)


def _dispatch_batch(user_id: str, task_ids: List[str]) -> asyncio.Task:
    """Fire-and-forget with a held reference so the Task is never GC'd
    mid-run and its outcome is always observed (see ``_run_one``)."""
    task = asyncio.create_task(
        _run_user_batch(user_id, task_ids),
        name=f"routine_task_batch_{user_id}",
    )
    _in_flight.add(task)
    task.add_done_callback(_in_flight.discard)
    return task


async def run_scheduler_pass() -> int:
    """One sweep over due, active ``RoutineTask`` rows. Returns count dispatched.

    Recomputes + persists ``next_run_at`` BEFORE dispatching (not after) so
    a slow or stuck run is never double-picked on the next tick. Due
    routines are grouped by owner and each group runs serially (one
    ``asyncio.Task`` per user) under the global parallel-run bound.
    """
    now_iso = utc_now_iso()
    try:
        candidates = await RoutineTask.find(
            {"status": "active", "next_run_at": {"$lte": now_iso}}
        )
    except Exception:  # noqa: BLE001
        logger.warning(
            "run_scheduler_pass: failed to query due routines", exc_info=True
        )
        return 0

    dispatched = 0
    by_user: Dict[str, List[str]] = {}
    for task in candidates:
        try:
            from app.agentive.services.routine_tasks import compute_next_run_at

            if (task.cron or "").strip():
                # Recurring: advance next_run_at BEFORE dispatch so a slow
                # run cannot be double-picked on the next tick.
                task.next_run_at = compute_next_run_at(
                    task.cron, task.timezone, base_iso=now_iso
                )
            else:
                # One-shot (empty cron): clear next_run_at so this pass cannot
                # re-pick mid-flight. Failure / skip paths may re-arm a retry.
                task.next_run_at = None
            await task.save()
        except Exception:  # noqa: BLE001
            logger.warning(
                "run_scheduler_pass: failed to recompute next_run_at for %s",
                task.id,
                exc_info=True,
            )
            continue
        by_user.setdefault(task.user_id or "", []).append(task.id)
        dispatched += 1
    for user_id, task_ids in by_user.items():
        _dispatch_batch(user_id, task_ids)
    return dispatched


async def _scheduler_loop() -> None:
    interval = _interval_seconds()
    logger.info("routine_task_scheduler: starting with interval=%ds", interval)
    try:
        while True:
            try:
                n = await run_scheduler_pass()
                if n > 0:
                    logger.info(
                        "routine_task_scheduler: pass dispatched %d routine(s)", n
                    )
            except Exception:  # noqa: BLE001 — never let the loop die
                logger.warning("routine_task_scheduler: pass raised", exc_info=True)
            await asyncio.sleep(interval)
    except asyncio.CancelledError:
        logger.info("routine_task_scheduler: cancelled — shutting down")
        raise


_scheduler_task: Optional[asyncio.Task] = None


def start_scheduler() -> Optional[asyncio.Task]:
    """Start the scheduler background task. Idempotent — second call is no-op.

    Wired from ``app.main`` startup. Returns the Task handle so the lifespan
    can cancel it on shutdown.
    """
    global _scheduler_task
    if _scheduler_task is not None and not _scheduler_task.done():
        return _scheduler_task
    try:
        loop = asyncio.get_event_loop()
    except RuntimeError:
        return None
    if not loop.is_running():
        return None
    _scheduler_task = loop.create_task(_scheduler_loop(), name="routine_task_scheduler")
    return _scheduler_task


async def stop_scheduler() -> None:
    """Cancel + await the scheduler task. Idempotent."""
    global _scheduler_task
    if _scheduler_task is None:
        return
    if _scheduler_task.done():
        _scheduler_task = None
        return
    _scheduler_task.cancel()
    try:
        await _scheduler_task
    except (asyncio.CancelledError, Exception):
        pass
    _scheduler_task = None


def is_running() -> bool:
    """Backs ``app.services.scheduler.scheduler_available()``."""
    return _scheduler_task is not None and not _scheduler_task.done()
