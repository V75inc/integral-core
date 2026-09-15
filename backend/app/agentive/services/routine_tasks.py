"""CRUD + cadence math for user-created ``RoutineTask`` rows.

Backs the ``P_scheduling`` tool domain (``integral_schedule_task`` /
``integral_list_routines`` / ``integral_update_routine`` /
``integral_cancel_routine`` / ``integral_delete_routine``) and the
``routine_task_scheduler`` background loop's per-tick ``next_run_at``
recomputation. See ``app/agentive/nodes.py::RoutineTask`` for the field
contract and ``docs/backend`` (Routine Tasks) for the product-facing design.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

from app.agentive.edges import HAS_ROUTINE_TASK
from app.agentive.nodes import RoutineTask
from app.api.errors import BadRequestError, ResourceNotFoundError
from app.utils.time import utc_now_iso

logger = logging.getLogger(__name__)


def compute_next_run_at(
    cron: str, timezone: str, *, base_iso: Optional[str] = None
) -> str:
    """Return the next ISO-8601 UTC fire time for ``cron`` evaluated in ``timezone``.

    ``croniter`` is timezone-aware when handed a tz-aware ``datetime`` as its
    base — evaluating in the task's own timezone (not the server's) is the
    whole point of storing ``cron`` + ``timezone`` separately rather than
    reusing jvspatial's scheduler (which has one global timezone for the
    entire service; see routine_task_scheduler.py's module docstring).
    """
    from datetime import datetime
    from datetime import timezone as dt_timezone
    from zoneinfo import ZoneInfo

    from croniter import croniter  # type: ignore[import-untyped]

    if not (cron or "").strip():
        raise BadRequestError(
            message="cron is required to compute the next run",
            details={"cron": cron},
        )

    try:
        tz = ZoneInfo(timezone or "UTC")
    except Exception as exc:
        raise BadRequestError(
            message=f"Unknown timezone {timezone!r}", details={"timezone": timezone}
        ) from exc

    if base_iso:
        base = datetime.fromisoformat(base_iso.replace("Z", "+00:00")).astimezone(tz)
    else:
        base = datetime.now(tz)

    try:
        nxt = croniter(cron, base).get_next(datetime)
    except Exception as exc:
        raise BadRequestError(
            message=f"Invalid cron expression {cron!r}", details={"cron": cron}
        ) from exc

    return nxt.astimezone(dt_timezone.utc).isoformat()


def parse_run_at(run_at: str) -> str:
    """Parse an absolute ISO-8601 fire time to UTC ISO; reject past/invalid."""
    from datetime import datetime
    from datetime import timezone as dt_timezone

    raw = (run_at or "").strip()
    if not raw:
        raise BadRequestError(message="run_at is required")
    try:
        when = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError as exc:
        raise BadRequestError(
            message=f"Invalid run_at {run_at!r} — need ISO-8601",
            details={"run_at": run_at},
        ) from exc
    if when.tzinfo is None:
        when = when.replace(tzinfo=dt_timezone.utc)
    when_utc = when.astimezone(dt_timezone.utc)
    now = datetime.now(dt_timezone.utc)
    if when_utc <= now:
        raise BadRequestError(
            message="run_at must be in the future",
            details={"run_at": run_at},
        )
    return when_utc.isoformat()


async def _get_owned_routine(user_id: str, routine_id: str) -> RoutineTask:
    routine = await RoutineTask.get(routine_id)
    if routine is None or routine.user_id != user_id:
        raise ResourceNotFoundError(message="Routine not found")
    return routine


def _validate_max_runs(max_runs: Optional[int]) -> Optional[int]:
    if max_runs is None:
        return None
    try:
        n = int(max_runs)
    except (TypeError, ValueError) as exc:
        raise BadRequestError(message="max_runs must be an integer") from exc
    if n <= 0:
        raise BadRequestError(message="max_runs must be a positive integer")
    return n


async def create_routine_task(
    *,
    user_id: str,
    workspace_id: str,
    thread_id: str,
    agent_id: str = "",
    instruction: str,
    cron: str = "",
    timezone: str = "UTC",
    write_scope: Optional[List[Dict[str, str]]] = None,
    max_runs: Optional[int] = None,
    run_at: Optional[str] = None,
    source_app_id: str = "",
    source_schedule_key: str = "",
) -> RoutineTask:
    """Create + graph-wire a new ``RoutineTask`` (I-GRAPH-01).

    Called from the ``routine_task_create`` staging executor, i.e. only after
    the user has blessed the staged card describing this exact cadence +
    write-scope grant — never called speculatively.

    Pass either recurring ``cron`` or one-shot ``run_at`` (absolute ISO-8601),
    not both. One-shots default ``max_runs=1`` and store an empty ``cron``;
    the scheduler clears ``next_run_at`` on dispatch so they cannot double-fire.

    ``max_runs`` is the run-count dimension: ``None`` for an open-ended
    routine, or a positive int capping the number of SUCCESSFUL runs before
    the scheduler auto-transitions ``status`` to ``"completed"``.

    ``source_app_id`` / ``source_schedule_key`` mark App-bundled schedule
    materializations so reinstall can find (and not duplicate) them.
    """
    from app.services.permissions import get_user_node

    if not instruction or not instruction.strip():
        raise BadRequestError(message="instruction is required")
    if not thread_id:
        raise BadRequestError(message="thread_id is required")

    cron_s = (cron or "").strip()
    run_at_s = (run_at or "").strip() or None
    if run_at_s and cron_s:
        raise BadRequestError(
            message="pass run_at or cron, not both",
            details={"cron": cron_s, "run_at": run_at_s},
        )
    if not run_at_s and not cron_s:
        raise BadRequestError(message="cron or run_at is required")

    if run_at_s:
        next_run_at = parse_run_at(run_at_s)
        if max_runs is None:
            max_runs = 1
        cron_s = ""
    else:
        next_run_at = compute_next_run_at(cron_s, timezone)

    max_runs = _validate_max_runs(max_runs)

    user = await get_user_node(user_id)
    if user is None:
        raise ResourceNotFoundError(message="User not found")

    now = utc_now_iso()

    routine = await RoutineTask.create(
        user_id=user_id,
        workspace_id=workspace_id,
        thread_id=thread_id,
        agent_id=agent_id,
        instruction=instruction.strip(),
        cron=cron_s,
        timezone=timezone or "UTC",
        status="active",
        write_scope=list(write_scope or []),
        next_run_at=next_run_at,
        last_run_at=None,
        last_run_status=None,
        last_run_error=None,
        consecutive_failures=0,
        max_runs=max_runs,
        run_count=0,
        source_app_id=source_app_id or "",
        source_schedule_key=source_schedule_key or "",
        created_at=now,
        updated_at=now,
    )
    await user.connect(routine, edge=HAS_ROUTINE_TASK, activated_at=now)
    logger.info(
        "create_routine_task: user=%s routine=%s cron=%r tz=%s next_run_at=%s max_runs=%s",
        user_id,
        routine.id,
        cron_s,
        timezone,
        next_run_at,
        max_runs,
    )
    return routine


def serialize_routine(r: RoutineTask) -> Dict[str, Any]:
    """Canonical wire dict for a RoutineTask (REST + tool list payloads)."""
    running = False
    if r.thread_id:
        from app.services.chat_turn_registry import get_turn

        handle = get_turn(r.thread_id)
        if handle is not None and (handle.origin or "").strip() == "routine_task":
            running = True
    return {
        "id": r.id,
        "instruction": r.instruction,
        "cron": r.cron,
        "timezone": r.timezone,
        "status": r.status,
        "write_scope": list(r.write_scope or []),
        "next_run_at": r.next_run_at,
        "last_run_at": r.last_run_at,
        "last_run_status": r.last_run_status,
        "last_run_error": getattr(r, "last_run_error", None),
        "consecutive_failures": r.consecutive_failures,
        "max_runs": r.max_runs,
        "run_count": r.run_count,
        "thread_id": r.thread_id,
        "workspace_id": r.workspace_id,
        "created_at": r.created_at,
        "updated_at": r.updated_at,
        "running": running,
    }


async def get_routine_task(*, user_id: str, routine_id: str) -> RoutineTask:
    """Return an owned routine or raise ResourceNotFoundError."""
    return await _get_owned_routine(user_id, routine_id)


async def list_routine_tasks(
    user_id: str, *, status: Optional[str] = None
) -> Dict[str, Any]:
    """Service-backed read for ``integral_list_routines`` and REST list.

    When ``status`` is omitted, cancelled rows are excluded (tool default).
    When ``status`` is set, only that status is returned (including cancelled).
    """
    if status is not None and status not in (
        "active",
        "paused",
        "completed",
        "cancelled",
    ):
        raise BadRequestError(
            message="status must be active, paused, completed, or cancelled",
            details={"status": status},
        )

    routines = await RoutineTask.find({"user_id": user_id})
    if status is not None:
        routines = [r for r in routines if r.status == status]
    else:
        routines = [r for r in routines if r.status != "cancelled"]
    routines.sort(key=lambda r: r.next_run_at or "")
    return {
        "routines": [serialize_routine(r) for r in routines],
        "total": len(routines),
    }


async def update_routine_task(
    *,
    user_id: str,
    routine_id: str,
    status: Optional[str] = None,
    instruction: Optional[str] = None,
    cron: Optional[str] = None,
    timezone: Optional[str] = None,
    write_scope: Optional[List[Dict[str, str]]] = None,
    max_runs: Optional[int] = None,
    clear_max_runs: bool = False,
) -> RoutineTask:
    """Apply an edit to an owned routine. Recomputes ``next_run_at`` if cadence changed.

    Resuming a ``"completed"`` routine (``status="active"``) resets
    ``run_count`` to 0 — otherwise it would immediately re-complete on the
    very next tick since the prior count already met ``max_runs``.
    ``clear_max_runs=True`` reverts a capped routine to open-ended
    (``max_runs=None``) — a plain ``max_runs=None`` default arg can't
    distinguish "leave unchanged" from "clear it", so this is a separate flag.
    """
    routine = await _get_owned_routine(user_id, routine_id)

    cadence_changed = False
    if status is not None:
        if status not in ("active", "paused"):
            raise BadRequestError(message="status must be 'active' or 'paused'")
        if status == "active" and routine.status == "completed":
            routine.run_count = 0
        if status == "active" and routine.status != "active":
            # A paused/completed routine still carries the (now stale, past)
            # next_run_at it had when it stopped — without a recompute the
            # scheduler fires it on the very next tick instead of at the
            # next cron slot.
            cadence_changed = True
        routine.status = status
        if status == "active":
            routine.consecutive_failures = 0
    if instruction is not None and instruction.strip():
        routine.instruction = instruction.strip()
    if cron is not None:
        routine.cron = cron
        cadence_changed = True
    if timezone is not None:
        routine.timezone = timezone
        cadence_changed = True
    if write_scope is not None:
        routine.write_scope = list(write_scope)
    if clear_max_runs:
        routine.max_runs = None
    elif max_runs is not None:
        routine.max_runs = _validate_max_runs(max_runs)

    if cadence_changed:
        routine.next_run_at = compute_next_run_at(routine.cron, routine.timezone)

    routine.updated_at = utc_now_iso()
    await routine.save()
    return routine


async def abort_routine_run(routine: RoutineTask) -> bool:
    """Abort an in-flight scheduled run on the routine's thread, if any.

    Only cancels a turn whose registry ``origin`` is ``routine_task`` — a live
    human chat on the same thread is left alone.
    """
    if not routine.thread_id:
        return False
    from app.services.chat_turn_registry import cancel_turn_if_origin

    return await cancel_turn_if_origin(routine.thread_id, "routine_task")


async def cancel_routine_task(*, user_id: str, routine_id: str) -> RoutineTask:
    """Stop an owned routine — soft tombstone + abort any in-flight run.

    Sets ``status=cancelled`` so the scheduler stops picking it up. App-bundled
    schedules keep the tombstone so reinstall does not resurrect them. Aborts
    an in-flight ``origin=routine_task`` turn when present; does not abort a
    live human turn on the same thread.
    """
    routine = await _get_owned_routine(user_id, routine_id)
    if routine.status == "cancelled":
        return routine
    aborted = await abort_routine_run(routine)
    routine.status = "cancelled"
    if aborted:
        routine.last_run_status = "stopped"
        routine.last_run_error = None
    routine.updated_at = utc_now_iso()
    await routine.save()
    return routine


async def delete_routine_task(*, user_id: str, routine_id: str) -> str:
    """Hard-delete an owned RoutineTask (I-GRAPH-01: cascade=False).

    Aborts an in-flight scheduled run first, then deletes the node. Does not
    delete the bound ChatThread. App-bundled schedules may rematerialize on a
    later library sync — that is intentional (Remove = this instance).
    """
    routine = await _get_owned_routine(user_id, routine_id)
    workspace_id = routine.workspace_id
    await abort_routine_run(routine)
    await routine.delete(cascade=False)
    logger.info(
        "delete_routine_task: user=%s routine=%s workspace=%s",
        user_id,
        routine_id,
        workspace_id,
    )
    return routine_id
