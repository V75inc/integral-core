"""Native-scheduler availability probe.

``uplink_registry._scheduler_available()`` (Plan 10-04, APP-AGENTS-01) has
tried importing ``app.services.scheduler.scheduler_available`` since Phase 10
and always got ``ImportError`` — this module did not exist, so App-bundled
agents' ``default_schedules`` always degraded to ``status="manual"`` per
app_bundles_v1.md §6.4.

The Routine Tasks feature (user-issued recurring chat instructions) is the
first thing in this codebase to actually need a scheduler, so this module
now exists — backed by the hand-rolled asyncio loop in
``app.services.routine_task_scheduler`` (a fourth instance of the
``sync_loop`` / ``approval_ttl_loop`` / ``app_install_reaper`` pattern; see
that module's docstring for why jvspatial's own thread-based
``SchedulerService`` was evaluated and not used).

Wiring this up is a free side effect of Routine Tasks shipping: once the
loop is running, ``scheduler_available()`` returns ``True`` and
``register_app_agent`` starts marking App-bundled agent schedules
``status="scheduled"`` instead of ``"manual"`` — no change needed there.
"""

from __future__ import annotations


def scheduler_available() -> bool:
    """Return whether the routine-task scheduler loop is currently running."""
    try:
        from app.services.routine_task_scheduler import is_running
    except Exception:  # noqa: BLE001
        return False
    return is_running()
