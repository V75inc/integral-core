"""PROPOSE stagers for the ``P_scheduling`` tool domain.

Mirrors ``stagers_filing.py`` — each function maps a tool's call args to
``create_staged_change`` kwargs (``kind`` / ``summary`` / ``diff_human`` /
``diff_machine`` / ``payload``). ``payload`` MUST match exactly what the
matching ``staging_executors`` executor splats into
``app.agentive.services.routine_tasks``. Data only (PC-1 / PC-2): no
``user_id``, no scope-widening value — identity/scope come from the
dispatch principal + bound scope, never from the stager.
"""

from __future__ import annotations

from typing import Any, Dict, List


def _describe_write_scope(write_scope: List[Dict[str, str]]) -> str:
    if not write_scope:
        return "none"
    return ", ".join(
        f"{w.get('resource_type', 'resource')}:{w.get('resource_id', '?')}"
        for w in write_scope
    )


async def stage_schedule_task(args: Dict[str, Any]) -> Dict[str, Any]:
    """Stage a new ``RoutineTask``.

    Resolves the live ``ChatThread`` from the session bound by
    ``_dispatch_propose`` (the routine posts into the thread this
    conversation is happening in) — a stager cannot receive this as a plain
    arg (PC-1), and creating a routine outside a live chat turn (e.g. an
    external MCP call with no session_id) is out of scope for v1: it fails
    closed with a clear error rather than guessing a thread.

    Recurring: ``cron`` (+ optional ``timezone`` / ``max_runs``).
    One-shot: ``run_at`` (ISO-8601 absolute); defaults ``max_runs=1``, empty cron.
    Pass exactly one of ``cron`` / ``run_at``.
    """
    src = args or {}
    instruction = (src.get("instruction") or "").strip()
    cron = (src.get("cron") or "").strip()
    run_at = (src.get("run_at") or "").strip() or None
    timezone = (src.get("timezone") or "UTC").strip() or "UTC"
    write_scope = list(src.get("write_scope") or [])
    max_runs = src.get("max_runs")

    if not instruction:
        raise ValueError("schedule_task: instruction is required")
    if run_at and cron:
        raise ValueError("schedule_task: pass run_at or cron, not both")
    if not run_at and not cron:
        raise ValueError("schedule_task: cron or run_at is required")
    if max_runs is not None:
        try:
            max_runs = int(max_runs)
        except (TypeError, ValueError) as exc:
            raise ValueError("schedule_task: max_runs must be an integer") from exc
        if max_runs <= 0:
            raise ValueError("schedule_task: max_runs must be a positive integer")
    elif run_at:
        max_runs = 1

    # Validate run_at early so the staged card never shows a past/invalid time.
    if run_at:
        from app.agentive.services.routine_tasks import parse_run_at
        from app.api.errors import BadRequestError

        try:
            run_at = parse_run_at(run_at)
        except BadRequestError as exc:
            raise ValueError(f"schedule_task: {exc.message}") from exc

    from app.agentive.tooling.bindings import (
        _bound_propose_principal,
        _bound_propose_session_id,
    )

    session_id = _bound_propose_session_id()
    if not session_id:
        raise ValueError(
            "schedule_task: no active chat session — routines can only be "
            "created from a live chat turn (need a thread to post into)"
        )

    from app.services.chat_proactive_bridge import find_thread_by_provider_session

    principal_id = _bound_propose_principal()
    thread = await find_thread_by_provider_session(
        user_id=principal_id, provider_session_id=session_id
    )
    if thread is None:
        raise ValueError("schedule_task: could not resolve the active chat thread")

    from app.services.agent_scope import active_workspace_id

    payload: Dict[str, Any] = {
        "thread_id": thread.id,
        "agent_id": thread.agent_id or "",
        "instruction": instruction,
        "cron": "" if run_at else cron,
        "timezone": timezone,
        "write_scope": write_scope,
        "workspace_id": active_workspace_id() or thread.workspace_id or "",
        "max_runs": max_runs,
    }
    if run_at:
        payload["run_at"] = run_at

    scope_desc = _describe_write_scope(write_scope)
    stop_desc = (
        f"after {max_runs} run(s)" if max_runs is not None else "never (open-ended)"
    )
    summary = f"New routine: {instruction[:80]}"
    if run_at:
        cadence_line = f"One-shot at: {run_at}"
    else:
        cadence_line = f"Cadence (cron, {timezone}): {cron}"
    diff_lines = [
        f"Instruction: {instruction}",
        cadence_line,
        "Posts to: this chat",
        f"May auto-apply writes to: {scope_desc}",
        f"Stops: {stop_desc}",
    ]

    return {
        "kind": "routine_task_create",
        "summary": summary,
        "diff_human": "\n".join(diff_lines),
        "diff_machine": {"op": "routine_task_create", **payload},
        "payload": payload,
    }


def stage_update_routine(args: Dict[str, Any]) -> Dict[str, Any]:
    """Stage an edit (pause/resume/cadence/instruction/write_scope) to an owned routine."""
    src = args or {}
    routine_id = src.get("routine_id")
    if not routine_id:
        raise ValueError("update_routine: routine_id is required")

    payload: Dict[str, Any] = {"routine_id": routine_id}
    lines: List[str] = []
    for key in ("status", "instruction", "cron", "timezone"):
        if src.get(key) is not None:
            payload[key] = src[key]
            lines.append(f"{key}: {src[key]}")
    if src.get("write_scope") is not None:
        payload["write_scope"] = list(src["write_scope"])
        lines.append(f"write_scope: {_describe_write_scope(payload['write_scope'])}")
    if src.get("clear_max_runs"):
        payload["clear_max_runs"] = True
        lines.append("max_runs: cleared (open-ended)")
    elif src.get("max_runs") is not None:
        payload["max_runs"] = src["max_runs"]
        lines.append(f"max_runs: {src['max_runs']}")

    if len(payload) == 1:
        raise ValueError("update_routine: at least one field to change is required")

    return {
        "kind": "routine_task_update",
        "summary": f"Update routine {routine_id}",
        "diff_human": "\n".join(lines) or "(no changes)",
        "diff_machine": {"op": "routine_task_update", **payload},
        "payload": payload,
    }


def stage_cancel_routine(args: Dict[str, Any]) -> Dict[str, Any]:
    """Stage cancellation of an owned routine."""
    src = args or {}
    routine_id = src.get("routine_id")
    if not routine_id:
        raise ValueError("cancel_routine: routine_id is required")

    payload = {"routine_id": routine_id}
    return {
        "kind": "routine_task_cancel",
        "summary": f"Stop routine {routine_id}",
        "diff_human": (
            f"Stop routine {routine_id} — it will not run again. "
            "An in-progress scheduled run on its thread will be cancelled."
        ),
        "diff_machine": {"op": "routine_task_cancel", **payload},
        "payload": payload,
    }


def stage_delete_routine(args: Dict[str, Any]) -> Dict[str, Any]:
    """Stage hard-delete of an owned routine."""
    src = args or {}
    routine_id = src.get("routine_id")
    if not routine_id:
        raise ValueError("delete_routine: routine_id is required")

    payload = {"routine_id": routine_id}
    return {
        "kind": "routine_task_purge",
        "summary": f"Remove routine {routine_id}",
        "diff_human": (
            f"Permanently remove routine {routine_id}. This cannot be undone. "
            "App-bundled schedules may rematerialize on a later library sync."
        ),
        "diff_machine": {"op": "routine_task_purge", **payload},
        "payload": payload,
    }
