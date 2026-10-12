"""Background reaper for abandoned App installs in ``awaiting_settings`` state.

Phase 10 Plan 10-05 (APP-LIFECYCLE-01).

Resolves Architectural Decision 4 cleanup half: when a user starts an install
that requires settings, the install transaction pauses (returns 202 +
install_token + sets ``App.lifecycle_state="awaiting_settings"``). If the
user abandons the install (never submits settings), the App row would
otherwise pile up indefinitely with partial install state.

This reaper sweeps every ``awaiting_settings`` App older than the configured
TTL (``APP_INSTALL_TOKEN_TTL_HOURS``, default 1.0h — same as the token TTL)
and uninstalls it via ``uninstall_app(archive=False)`` (hard purge).
Dependents still hard-block; abandoned awaiting_settings Apps are leaves.
Emits ``app.uninstalled`` with ``details.reason="settings_pause_timeout"``.

T-10-05-06 (Info Disclosure): reaper does NOT log settings_schema values
in its ChangeEvent — only the lifecycle_state and reason. Settings_schema
itself is shape-only (no user data — that lives in App.settings, which
the reaper never touches because awaiting_settings means settings were
never submitted).

T-10-05-03 (DoS): reaper is the second line of defense against an attacker
who mints many awaiting_settings Apps to fill the DB. The primary defense
is per-workspace rate limiting at the install endpoint (Plan 10-06+);
this reaper guarantees cleanup even if the rate limit is bypassed.

Scheduler integration: jvspatial does not (yet) ship a native scheduler.
For Plan 10-05 we wire this as an ``asyncio.create_task`` background loop
started from ``app.main.lifespan``. When the native scheduler ships
(Plan 10-04 ``_scheduler_available()`` probe), the loop should migrate
to the scheduler primitive — gated by the same probe.
"""

from __future__ import annotations

import asyncio
import logging
import os
from datetime import datetime, timezone
from typing import Any, Optional

from app.models.nodes import App
from app.services.app_lifecycle import uninstall_app

logger = logging.getLogger(__name__)

# Default reaper-sweep interval in seconds. Configurable via env var so
# tests / staging can tighten the loop.
_DEFAULT_INTERVAL_SECONDS = 300  # 5 minutes


def _ttl_hours() -> float:
    """Resolve TTL hours from env. Mirrors app_install_token defaults."""
    env_val = os.environ.get("APP_INSTALL_TOKEN_TTL_HOURS")
    if env_val is not None:
        try:
            return float(env_val)
        except ValueError:
            pass
    return 1.0


def _interval_seconds() -> int:
    """Resolve sweep interval from env."""
    env_val = os.environ.get("APP_INSTALL_REAPER_INTERVAL_SECONDS")
    if env_val is not None:
        try:
            return int(env_val)
        except ValueError:
            pass
    return _DEFAULT_INTERVAL_SECONDS


def _parse_iso(ts: Optional[str]) -> Optional[datetime]:
    """Best-effort ISO 8601 parse; returns None on failure."""
    if not ts:
        return None
    try:
        # Python 3.11+ fromisoformat handles full ISO 8601 incl. Z.
        if ts.endswith("Z"):
            ts = ts[:-1] + "+00:00"
        return datetime.fromisoformat(ts)
    except Exception:
        return None


async def _compensate_expired_install(
    app_node: App, ttl_hours: float, now: datetime
) -> bool:
    """Compensate one unchanged expired install through its current owner rights."""
    from jvspatial.core.context import get_default_context

    from app.exceptions import AuthorizationUnavailableError
    from app.models.edges import OWNS
    from app.services.app_operations.event_outbox import insert_revision_event
    from app.services.app_operations.transaction_scope import (
        graph_transaction_available,
    )
    from app.services.authority_facts import primary_node_record, required_node_text
    from app.services.membership_write_scope import membership_write_scope
    from app.services.resource_location import MANAGEMENT_APP_STATES, locate_resource
    from app.services.workspace_membership import _deliver
    from app.services.workspace_permissions import can_access_workspace

    workspace_id = app_node.workspace_id
    owner_id = app_node.owner_user_id
    if not owner_id or not workspace_id:
        raise AuthorizationUnavailableError()
    events: list[dict[str, Any]] = []
    outbox = None
    async with membership_write_scope(workspace_id, principal_ids=(owner_id,)):
        database = get_default_context().database
        row = await primary_node_record(database, App, app_node.id)
        if row is None:
            return False
        data = row["context"]
        if (
            data.get("lifecycle_state") != "awaiting_settings"
            or data.get("updated_at") != app_node.updated_at
        ):
            return False
        if (
            required_node_text(row, "workspace_id") != workspace_id
            or required_node_text(row, "owner_user_id") != owner_id
        ):
            raise AuthorizationUnavailableError()
        expiry_start = _parse_iso(data.get("updated_at"))
        if (
            expiry_start is None
            or expiry_start.tzinfo is None
            or (now - expiry_start).total_seconds() / 3600 < ttl_hours
        ):
            return False
        if not data.get("installed_from_library_id") or data.get("settings") not in (
            {},
            None,
        ):
            return False
        ownership = await database.find(
            "edge", {"target": app_node.id, "entity": OWNS._entity_name()}, limit=2
        )
        if len(ownership) != 1 or ownership[0].get("source") != owner_id:
            raise AuthorizationUnavailableError()
        location = await locate_resource(
            "app", app_node.id, allowed_app_states=MANAGEMENT_APP_STATES
        )
        if (
            location is None
            or location.workspace_id != workspace_id
            or await can_access_workspace(owner_id, workspace_id) == "none"
        ):
            raise AuthorizationUnavailableError()
        await uninstall_app(
            app_id=app_node.id,
            actor_id=owner_id,
            archive=False,
            reason="settings_pause_timeout",
            _change_event_sink=events.append,
        )
        if (
            len(events) != 1
            or events[0].get("action") != "app.uninstalled"
            or events[0].get("resource_id") != app_node.id
        ):
            raise AuthorizationUnavailableError()
        event = events[0]
        event.update({"actor_kind": "system", "actor_id": "app_install_reaper"})
        event["details"] = {**event.get("details", {}), "authority_owner_id": owner_id}
        if graph_transaction_available(database):
            outbox = await insert_revision_event(
                transaction=database,
                workspace_id=workspace_id,
                namespace="expired-install-compensation-v1",
                subject_id=app_node.id,
                revision=1,
                event=event,
            )
    if outbox:
        await _deliver(outbox)
    else:
        from app.services.change_event import emit_change_event

        await emit_change_event(**event)
    return True


async def run_reaper_pass() -> int:
    """One sweep over ``awaiting_settings`` Apps past TTL. Returns count reaped.

    Iterates Apps with lifecycle_state="awaiting_settings", computes age
    from ``updated_at`` (the field touched when the install transaction
    transitioned to awaiting_settings — NOT created_at, which may predate
    the install if the App row was somehow long-lived).

    Failed force-uninstalls log a warning and continue to the next row.
    """
    ttl_hours = _ttl_hours()
    now = datetime.now(timezone.utc)

    try:
        candidates = await App.find({"lifecycle_state": "awaiting_settings"})
    except Exception as e:
        logger.warning("run_reaper_pass: failed to query awaiting_settings Apps: %s", e)
        return 0

    reaped = 0
    for app_node in candidates:
        anchor_ts = _parse_iso(app_node.updated_at) or _parse_iso(app_node.created_at)
        if anchor_ts is None:
            # No timestamps to reason about — skip rather than reap a row
            # that may not actually be abandoned.
            continue
        # Normalize to aware UTC if naive (shouldn't happen with utc_now_iso
        # but defensive).
        if anchor_ts.tzinfo is None:
            anchor_ts = anchor_ts.replace(tzinfo=timezone.utc)
        age_hours = (now - anchor_ts).total_seconds() / 3600.0
        if age_hours < ttl_hours:
            continue
        try:
            if not await _compensate_expired_install(app_node, ttl_hours, now):
                continue
            reaped += 1
            logger.info(
                "run_reaper_pass: reaped app %s (age=%.2fh > ttl=%.2fh)",
                app_node.id,
                age_hours,
                ttl_hours,
            )
        except Exception as e:
            logger.warning(
                "run_reaper_pass: failed to uninstall app %s: %s",
                app_node.id,
                e,
            )
    return reaped


async def _reaper_loop() -> None:
    """Long-running coroutine — sweeps every ``_interval_seconds()``.

    Exits cleanly on CancelledError so server shutdown is graceful.
    """
    interval = _interval_seconds()
    logger.info(
        "app_install_reaper: starting with ttl_hours=%.2f interval=%ds",
        _ttl_hours(),
        interval,
    )
    try:
        while True:
            try:
                n = await run_reaper_pass()
                if n > 0:
                    logger.info("app_install_reaper: pass reaped %d App(s)", n)
            except Exception as e:  # noqa: BLE001 — never let the loop die
                logger.warning("app_install_reaper: pass raised: %s", e)
            await asyncio.sleep(interval)
    except asyncio.CancelledError:
        logger.info("app_install_reaper: cancelled — shutting down")
        raise


_reaper_task: Optional[asyncio.Task] = None


def start_reaper() -> Optional[asyncio.Task]:
    """Start the reaper background task. Idempotent — second call is no-op.

    Wired from ``app.main`` startup. Returns the Task handle so the lifespan
    can cancel it on shutdown.
    """
    global _reaper_task
    if _reaper_task is not None and not _reaper_task.done():
        return _reaper_task
    try:
        loop = asyncio.get_event_loop()
    except RuntimeError:
        return None
    if not loop.is_running():
        # No running loop — can happen during module import in tests.
        # Caller should re-invoke once the loop is alive.
        return None
    _reaper_task = loop.create_task(_reaper_loop(), name="app_install_reaper")
    return _reaper_task


async def stop_reaper() -> None:
    """Cancel + await the reaper task. Idempotent."""
    global _reaper_task
    if _reaper_task is None:
        return
    if _reaper_task.done():
        _reaper_task = None
        return
    _reaper_task.cancel()
    try:
        await _reaper_task
    except (asyncio.CancelledError, Exception):
        pass
    _reaper_task = None
