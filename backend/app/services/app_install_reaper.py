"""Background reaper for abandoned App installs in ``awaiting_settings`` state.

Phase 10 Plan 10-05 (APP-LIFECYCLE-01).

Resolves Architectural Decision 4 cleanup half: when a user starts an install
that requires settings, the install transaction pauses (returns 202 +
install_token + sets ``App.lifecycle_state="awaiting_settings"``). If the
user abandons the install (never submits settings), the App row would
otherwise pile up indefinitely with partial install state.

This reaper sweeps every ``awaiting_settings`` App older than the configured
TTL (``APP_INSTALL_TOKEN_TTL_HOURS``, default 1.0h — same as the token TTL)
and force-uninstalls it. Compensation cleanup is the standard
``uninstall_app(force=True)`` path, which:
- Unregisters skills + agents (Plan 10-04 helpers).
- Cascade-deletes the App row + its Tracks (force = hard delete, not archive).
- Emits a single ``app.force_uninstalled`` ChangeEvent with
  ``details.reason="settings_pause_timeout"`` so admins can audit reaper
  activity post-hoc.

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
from typing import Optional

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
            await uninstall_app(
                app_id=app_node.id,
                actor_id="system",
                force=True,
                archive=False,
                reason="settings_pause_timeout",
            )
            reaped += 1
            logger.info(
                "run_reaper_pass: reaped app %s (age=%.2fh > ttl=%.2fh)",
                app_node.id,
                age_hours,
                ttl_hours,
            )
        except Exception as e:
            logger.warning(
                "run_reaper_pass: failed to force-uninstall app %s: %s",
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
