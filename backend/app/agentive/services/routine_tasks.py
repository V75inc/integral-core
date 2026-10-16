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


def parse_run_at(run_at: str, timezone: str = "UTC") -> str:
    """Convert absolute or zone-local ISO time to UTC; reject invalid/past/DST gaps."""
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
        from zoneinfo import ZoneInfo

        from app.schemas.user_timezone import UserTimezoneUpdate

        try:
            tz = ZoneInfo(UserTimezoneUpdate(timezone=timezone).timezone)
        except ValueError as exc:
            raise BadRequestError(message="Invalid time zone") from exc
        early = when.replace(tzinfo=tz, fold=0)
        late = when.replace(tzinfo=tz, fold=1)
        roundtrip = (
            early.astimezone(dt_timezone.utc).astimezone(tz).replace(tzinfo=None)
        )
        if roundtrip != when or early.utcoffset() != late.utcoffset():
            raise BadRequestError(
                message="This local time is ambiguous or does not exist; provide an explicit UTC offset"
            )
        when = early
    when_utc = when.astimezone(dt_timezone.utc)
    now = datetime.now(dt_timezone.utc)
    if when_utc <= now:
        raise BadRequestError(
            message="run_at must be in the future",
            details={"run_at": run_at},
        )
    return when_utc.isoformat()


async def _get_owned_routine(user_id: str, routine_id: str) -> RoutineTask:
    routine = await RoutineTask.find_one({"id": routine_id})
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
    timezone: Optional[str] = None,
    write_scope: Optional[List[Dict[str, str]]] = None,
    max_runs: Optional[int] = None,
    run_at: Optional[str] = None,
    source_app_id: str = "",
    source_schedule_key: str = "",
    delivery_mode: str = "agent",
    source_staging_token: str = "",
) -> RoutineTask:
    """Create + graph-wire a new ``RoutineTask`` (I-GRAPH-01).

    Called from the ``routine_task_create`` staging executor, i.e. only after
    the user has blessed the staged card describing this exact cadence +
    write-scope grant — never called speculatively.

    Pass either recurring ``cron`` or one-shot ``run_at`` (ISO-8601; local when offset omitted),
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
    validate_delivery_mode(delivery_mode, instruction, write_scope)
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

    from app.schemas.user_timezone import UserTimezoneUpdate
    from app.services.user_timezone import get_user_timezone

    timezone = timezone or await get_user_timezone(user_id)
    try:
        timezone = UserTimezoneUpdate(timezone=timezone).timezone
    except ValueError as exc:
        raise BadRequestError(message="Invalid time zone") from exc
    if run_at_s:
        next_run_at = parse_run_at(run_at_s, timezone)
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
        delivery_mode=delivery_mode,
        source_staging_token=source_staging_token,
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
        if (
            handle is not None
            and handle.routine_id == r.id
            and bool(handle.work_item_id)
            and (handle.origin or "").strip() == "routine_task"
        ):
            running = True
    return {
        "id": r.id,
        "instruction": r.instruction,
        "delivery_mode": r.delivery_mode,
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
    delivery_mode: Optional[str] = None,
) -> RoutineTask:
    """Apply an owned delta from the same fresh row used by scheduler writers."""
    from app.agentive.services.routine_state import locked_routine

    async with locked_routine(routine_id, principal_id=user_id):
        return await _update_owned_routine_task(
            user_id=user_id,
            routine_id=routine_id,
            status=status,
            instruction=instruction,
            cron=cron,
            timezone=timezone,
            write_scope=write_scope,
            max_runs=max_runs,
            clear_max_runs=clear_max_runs,
            delivery_mode=delivery_mode,
        )


async def _update_owned_routine_task(
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
    delivery_mode: Optional[str] = None,
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

    validate_delivery_mode(
        delivery_mode or routine.delivery_mode,
        instruction if instruction is not None else routine.instruction,
        write_scope if write_scope is not None else routine.write_scope,
    )
    if delivery_mode is not None:
        routine.delivery_mode = delivery_mode
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
    """Best-effort local interruption for this exact routine's leased worker."""
    from app.agentive.services.work_items import _current_work_item
    from app.services.chat_turn_registry import cancel_turn_if_work_item, get_turn

    handle = get_turn(routine.thread_id) if routine.thread_id else None
    if handle is None or handle.routine_id != routine.id or not handle.work_item_id:
        return False
    item = await _current_work_item(handle.work_item_id)
    if (
        item is None
        or item.kind != "routine_turn"
        or (item.input_payload or {}).get("routine_id") != routine.id
        or item.principal_id != routine.user_id
        or item.workspace_id != routine.workspace_id
        or item.thread_id != routine.thread_id
    ):
        return False
    return await cancel_turn_if_work_item(routine.thread_id, item.work_item_id)


async def offer_routine_interruption(routine: RoutineTask) -> None:
    """Offer post-commit local interruption; durable state remains authoritative."""
    try:
        await abort_routine_run(routine)
    except Exception:
        logger.exception("Routine stop committed; local interruption unavailable")


async def _control_owned_routine(
    user_id: str, routine_id: str, *, purge: bool
) -> RoutineTask:
    from jvspatial.core.context import get_default_context

    from app.services.entry_write_scope import (
        entry_write_scope,
        is_postgres_entry_transaction,
    )
    from app.services.identity_admission import identity_admission

    caller = get_default_context()
    async with entry_write_scope(routine_id) as graph, identity_admission((user_id,)):
        row = await graph.database.find_one_and_update(
            "node",
            {"id": routine_id, "entity": RoutineTask._entity_name()},
            {"$set": {}},
        )
        if row is None:
            raise ResourceNotFoundError(message="Routine not found")
        routine = await _get_owned_routine(user_id, routine_id)
        if purge:
            await routine.delete(cascade=False)
        elif routine.status != "cancelled":
            routine.status = "cancelled"
            routine.updated_at = utc_now_iso()
            await routine.save()
    # Nested approved commands offer interruption after their own outer commit.
    # Across workers, every later effect checks this same locked routine state.
    if not is_postgres_entry_transaction(graph.database) or graph is not caller:
        await offer_routine_interruption(routine)
    return routine


async def cancel_routine_task(*, user_id: str, routine_id: str) -> RoutineTask:
    """Commit a stop tombstone before offering exact local worker interruption."""
    return await _control_owned_routine(user_id, routine_id, purge=False)


async def delete_routine_task(*, user_id: str, routine_id: str) -> str:
    """Remove the owned schedule, preserving its conversation and receipt history."""
    await _control_owned_routine(user_id, routine_id, purge=True)
    return routine_id


def routine_work_idempotency_key(*, routine_id: str, scheduled_for: str) -> str:
    """Deterministic WorkItem key for one routine fire slot."""
    return f"routine:{routine_id}:{scheduled_for}"


async def enqueue_routine_turn_work(
    *,
    routine_id: str,
    principal_id: str,
    workspace_id: str,
    scheduled_for: str,
    thread_id: str = "",
    app_id: str = "",
) -> Any:
    """Enqueue a ``kind=routine_turn`` WorkItem for a due fire (idempotent)."""
    from jvspatial.core.context import get_default_context

    from app.agentive.services import work_items
    from app.agentive.services.routine_mandate import routine_mandate_fingerprint
    from app.agentive.services.routine_state import locked_routine
    from app.schemas.agentive.work import WorkError
    from app.services.entry_write_scope import is_postgres_entry_transaction

    async with locked_routine(routine_id, principal_id=principal_id) as routine:
        if (
            routine is None
            or routine.user_id != principal_id
            or routine.workspace_id != workspace_id
            or routine.thread_id != thread_id
        ):
            raise WorkError("work.policy_denied", "Routine enqueue binding mismatch")
        return await work_items.enqueue_work_item(
            kind="routine_turn",
            origin="scheduler",
            principal_id=principal_id,
            workspace_id=workspace_id or "",
            idempotency_key=routine_work_idempotency_key(
                routine_id=routine_id, scheduled_for=scheduled_for
            ),
            input_payload={
                "routine_id": routine_id,
                "scheduled_for": scheduled_for,
                "routine_mandate_fingerprint": routine_mandate_fingerprint(routine),
            },
            thread_id=thread_id or None,
            app_id=app_id or None,
            transaction=(
                get_default_context().database
                if is_postgres_entry_transaction(get_default_context().database)
                else None
            ),
        )


async def execute_routine_turn_under_lease(item: Any, ctx: Any) -> None:
    """Worker body: run the existing agent-turn path under a WorkItem lease."""
    from app.agentive.nodes import RoutineTask
    from app.agentive.services.work_execution import assert_effect_boundary_allowed
    from app.schemas.agentive.work import WorkError
    from app.services.routine_task_scheduler import (
        _permission_gate,
        _run_agent_turn,
        _TurnBusy,
    )

    payload = dict(getattr(item, "input_payload", None) or {})
    routine_id = str(payload.get("routine_id") or "").strip()
    if not routine_id:
        raise WorkError("work.permanent", "routine_id missing from work input")
    task = await RoutineTask.get(routine_id)
    if task is None:
        raise WorkError("work.permanent", f"routine {routine_id} not found")
    # The scheduler's checks are not authority for a fire recovered directly
    # by the worker. Bind the saved routine to this claimed principal/scope
    # and repeat live admission before reaching the resident harness.
    for field, expected in (
        ("principal_id", task.user_id),
        ("workspace_id", task.workspace_id),
        ("thread_id", task.thread_id),
    ):
        if str(getattr(item, field, "") or "") != str(expected or "") or str(
            getattr(ctx, field, "") or ""
        ) != str(expected or ""):
            raise WorkError("work.policy_denied", "routine execution binding mismatch")
    if task.status != "active":
        raise WorkError("work.policy_denied", "routine is no longer active")
    reason = await _permission_gate(task)
    if reason is not None:
        raise WorkError("work.policy_denied", reason)
    # Permission reads may await I/O while the work lease is cancelled or
    # expires. Recheck the existing effect fence after those reads as well.
    await assert_effect_boundary_allowed(ctx)
    if task.delivery_mode == "reminder":
        await _deliver_reminder(task, ctx)
        return
    from app.agentive.services.work_execution_binding import bind_work_execution

    try:
        with bind_work_execution(ctx):
            success, error_reason = await _run_agent_turn(task)
    except _TurnBusy:
        raise
    if not success:
        raise WorkError(
            "work.permanent",
            error_reason or "routine agent turn failed",
        )


def validate_delivery_mode(mode: str, instruction: str, write_scope: Any) -> None:
    """Reminder mode posts reviewed literal text; it cannot run tools or writes."""
    if mode not in {"agent", "reminder"}:
        raise BadRequestError(message="delivery_mode must be agent or reminder")
    if mode == "reminder" and (
        write_scope or not instruction.strip() or len(instruction) > 2000
    ):
        raise BadRequestError(
            message="A reminder requires 1–2000 characters of literal text and no write scope"
        )


async def _deliver_reminder(task: RoutineTask, context: Any) -> None:
    """Exactly one graph-wired message per durable fire, inside its lease fence."""
    import hashlib

    from app.agentive.services.work_items import authorized_work_item_effect
    from app.models.edges import CONTAINS
    from app.models.nodes import ChatMessage, ChatThread
    from app.schemas.agentive.work import WorkError
    from app.services.chat_threads import append_message
    from app.services.routine_task_scheduler import _permission_gate

    validate_delivery_mode(task.delivery_mode, task.instruction, task.write_scope)
    async with authorized_work_item_effect(context):
        # Repeat live admission inside the transaction; a paused/deleted routine
        # or an archived/wrong-owner thread must not receive a queued reminder.
        fresh = await RoutineTask.get(task.id)
        thread = await ChatThread.get(task.thread_id)
        if (
            fresh is None
            or fresh.status != "active"
            or fresh.delivery_mode != "reminder"
            or fresh.instruction != task.instruction
            or fresh.write_scope
            or fresh.user_id != context.principal_id
            or fresh.workspace_id != context.workspace_id
            or fresh.thread_id != context.thread_id
            or thread is None
            or thread.user_id != context.principal_id
            or thread.workspace_id != context.workspace_id
            or await _permission_gate(fresh) is not None
        ):
            raise WorkError("work.policy_denied", "reminder delivery scope changed")
        message_id = (
            "n.ChatMessage."
            + hashlib.sha256(f"{context.work_item_id}:reminder".encode()).hexdigest()
        )
        parts = [{"type": "text", "text": fresh.instruction}]
        metadata = {
            "origin": "routine_task",
            "routine_id": fresh.id,
            "delivery_mode": "reminder",
            "steps": [],
        }
        existing = await ChatMessage.get(message_id)
        if existing is not None:
            graph = await thread.get_context()
            edges = await graph.find_edges_between(
                thread.id, message_id, edge_class=CONTAINS
            )
            if (
                not edges
                or existing.thread_id != thread.id
                or existing.role != "assistant"
                or existing.parts != parts
                or existing.provider_metadata != metadata
            ):
                raise WorkError(
                    "work.idempotency_conflict", "reminder delivery differs"
                )
            return
        await append_message(
            thread=thread,
            role="assistant",
            parts=parts,
            provider_metadata=metadata,
            message_id=message_id,
        )
