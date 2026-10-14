"""Scheduled App tools: queue a tool as a background App job on its cron slots.

A manifest tool may declare ``schedule: "<cron>"`` or ``{cron, timezone}``.
Every minute this pass looks at the tools registered in each workspace and,
for each scheduled tool whose most recent slot fell in the last pass window,
queues an ``app_tool`` work item. The idempotency key is the slot itself, so
overlapping passes, restarts or several processes never run a slot twice.
Slots missed while the service was down are skipped, not replayed.

The job runs as the App's installing person (the same principal Core uses
for an App's default routines), in the App's own workspace.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Optional

log = logging.getLogger(__name__)

#: How far back a pass looks for a due slot; a little over the tick interval.
WINDOW_SECONDS = 90


def _due_slot(schedule: Dict[str, str], now: datetime) -> Optional[str]:
    """The slot (ISO UTC) that fell inside the window ending at ``now``, if any."""
    from app.agentive.services.routine_tasks import compute_next_run_at

    base = (now - timedelta(seconds=WINDOW_SECONDS)).isoformat()
    try:
        slot = compute_next_run_at(
            schedule["cron"], schedule.get("timezone") or "UTC", base_iso=base
        )
    except Exception:  # noqa: BLE001 - a bad schedule must not stop the others
        log.warning("invalid App tool schedule %r", schedule, exc_info=True)
        return None
    try:
        slot_dt = datetime.fromisoformat(slot.replace("Z", "+00:00"))
    except ValueError:
        return None
    return slot if slot_dt <= now else None


async def _app_for(workspace_id: str, bundle_slug: str) -> Optional[Any]:
    from app.models.nodes import App

    for app in (
        await App.find({"context.source_operational_model_slug": bundle_slug}) or []
    ):
        if (
            getattr(app, "workspace_id", "") == workspace_id
            and getattr(app, "lifecycle_state", "") == "active"
        ):
            return app
    return None


async def enqueue_due_app_schedules(now: Optional[datetime] = None) -> int:
    """Queue every scheduled App tool whose slot is due; returns how many."""
    from app.agentive.services.uplink_registry import _resolve_installing_principal
    from app.agentive.services.work_items import enqueue_work_item
    from app.services.hooks.registry import _TOOLS

    now = now or datetime.now(timezone.utc)
    queued = 0
    for workspace_id, tools in list(_TOOLS.items()):
        for key, spec in list((tools or {}).items()):
            schedule = spec.get("schedule")
            if not isinstance(schedule, dict) or not schedule.get("cron"):
                continue
            slot = _due_slot(schedule, now)
            if not slot:
                continue
            bundle_slug = str(spec.get("_bundle_slug") or "")
            app = await _app_for(workspace_id, bundle_slug) if bundle_slug else None
            if app is None:
                continue
            principal = await _resolve_installing_principal(app)
            if not principal:
                log.info(
                    "scheduled tool %s skipped: no installing principal for %s",
                    key,
                    app.id,
                )
                continue
            try:
                await enqueue_work_item(
                    kind="app_tool",
                    origin="app_schedule",
                    principal_id=principal,
                    workspace_id=workspace_id,
                    idempotency_key=f"{bundle_slug}:{key}:schedule:{slot}",
                    input_payload={
                        "tool_key": key,
                        "bundle_slug": bundle_slug,
                        "input": {},
                        "label": f"{spec.get('name') or key} (scheduled)",
                    },
                    app_id=str(app.id),
                )
                queued += 1
            except Exception:  # noqa: BLE001 - one failure must not stop the pass
                log.exception(
                    "could not queue scheduled tool %s in %s", key, workspace_id
                )
    return queued
