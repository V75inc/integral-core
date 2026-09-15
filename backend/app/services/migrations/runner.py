"""Phase 5 Plan 05-02 — async per-Entry migration runner.

Wraps the existing 9-op declarative runner at
``backend/app/services/content_profile_migrations.py`` with per-Entry
status tracking + asyncio task lifecycle. Spawned by
``content_profile_atomic_swap.publish_draft`` via ``asyncio.create_task``
immediately after the atomic swap completes — the runner is NOT awaited
inline; the HTTP response returns immediately with a tracker payload.

Locked decision §A5 (RESEARCH Pitfall 2): orphaned ``pending`` / ``running``
entries on process restart are a documented v1 limitation. A manual retry
endpoint is future work — out of scope for this phase. The limitation is
recorded in ``docs/INVARIANTS.md`` (I-MIG-02).

Locked decision #5 (single ChangeEvent per runner): the runner emits a
single ``migration.run`` ChangeEvent on completion with
``details={state, mutated_entry_count, failed_entry_count, ops_applied,
affected_entry_count}`` covering the full lifecycle via ``details.state``.

Locked decision §security (RESEARCH Pitfall): the runner authorizes via
``policy_engine.evaluate`` with ``Subject(kind="system", id="migration_runner")``
which short-circuits to allowed (system-bypass per Plan 03-01); the call is
audit-only.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any, Dict, List, Optional

from app.models.nodes import ContentProfile, Entry
from app.schemas.policy import Resource, Subject
from app.services.change_event import emit_change_event
from app.services.content_profile_migrations import (
    _OP_HANDLERS,
    _affected_tracks,
)
from app.services.policy_engine import evaluate as policy_evaluate

from .rollup import rollup_cp_status

logger = logging.getLogger(__name__)

_SYSTEM_SUBJECT = Subject(kind="system", id="migration_runner")


async def gather_affected_entries(published_cp: ContentProfile) -> List[Entry]:
    """Collect every Entry under every track the published CP governs."""
    from app.models.edges import CONTAINS

    affected: List[Entry] = []
    for track in await _affected_tracks(published_cp):
        try:
            entries = await track.nodes(edge=[CONTAINS], node=["Entry"])
            affected.extend(entries)
        except Exception as exc:  # noqa: BLE001 — best-effort gather
            logger.warning(
                "gather_affected_entries: track %s walk failed: %s", track.id, exc
            )
    return affected


async def mark_entries_pending(entries: List[Entry]) -> None:
    """Synchronous pre-mark — runs BEFORE the HTTP response so callers polling
    immediately see ``migration_status='pending'``. Best-effort — failures on
    individual entries are logged but do not abort the whole publish."""
    for e in entries:
        try:
            e.migration_status = "pending"
            e.migration_error = None
            await e.save()
        except Exception as exc:  # noqa: BLE001
            logger.warning("mark_entries_pending: entry %s save failed: %s", e.id, exc)


async def _async_migration_runner(
    *,
    published_cp: ContentProfile,
    compiled_manifest: Dict[str, Any],
    affected_entries: List[Entry],
    actor_id: Optional[str] = None,
) -> Dict[str, Any]:
    """Background task spawned by ``publish_draft``.

    Walks each Entry. For every Entry: marks ``"running"``, runs every
    declared op against the Entry's parent track in turn, marks
    ``"complete"`` (or ``"failed"`` + ``migration_error``), continues to the
    next Entry (per-Entry failure isolation — locked decision).

    Emits a single ``migration.run`` ChangeEvent on completion + rolls the
    ContentProfile.migration_status up.
    """
    # Audit-only policy gate — system subject short-circuits in policy_engine
    # (Plan 03-01 system-bypass). Re-raises on Decision(allowed=False) would
    # never happen but we surface the evaluation for observability.
    try:
        await policy_evaluate(
            subject=_SYSTEM_SUBJECT,
            action="migration.run",
            resource=Resource(
                kind="content_profile",
                id=published_cp.id,
                scope=f"content_profile:{published_cp.id}",
            ),
        )
    except Exception as exc:  # noqa: BLE001 — audit-only
        logger.warning("migration runner policy_evaluate failed: %s", exc)

    mutated_total = 0
    failed_total = 0
    ops_applied: List[str] = []

    # Build the per-track op list once.
    declared_ops: List[Dict[str, Any]] = []
    for mig in (compiled_manifest or {}).get("migrations") or []:
        for op in (mig or {}).get("ops") or []:
            if isinstance(op, dict) and op.get("op"):
                declared_ops.append(op)
                if op["op"] not in ops_applied:
                    ops_applied.append(str(op["op"]))

    # Affected tracks once (the runner walks per-Entry but the op handlers
    # consume the parent Track). Build a track-id → Track map so per-Entry
    # routing reuses the gathered Track objects.
    tracks = await _affected_tracks(published_cp)
    track_by_id = {t.id: t for t in tracks}

    for entry in affected_entries:
        try:
            entry.migration_status = "running"
            await entry.save()
        except Exception as exc:  # noqa: BLE001
            logger.warning("runner: mark running failed for %s: %s", entry.id, exc)
        # Resolve the entry's parent track. Fall back to walking all tracks
        # if the Entry lacks a direct track_id pointer.
        parent_track = track_by_id.get(getattr(entry, "track_id", None) or "")
        if parent_track is None and tracks:
            parent_track = tracks[0]
        try:
            for op in declared_ops:
                handler = _OP_HANDLERS.get(str(op.get("op") or ""))
                if handler is None:
                    continue
                log: Dict[str, Any] = {
                    "op": op["op"],
                    "mutated_entries": [],
                    "errors": [],
                    "pending_manual_review": [],
                }
                if parent_track is not None:
                    await handler(track=parent_track, op=op, log=log)
                if log["errors"]:
                    raise RuntimeError(
                        "; ".join(str(e.get("reason") or e) for e in log["errors"][:3])
                    )
                # A per-track handler may have touched this entry; tally a
                # mutation only if its id appears in the handler's log.
                if entry.id in log["mutated_entries"]:
                    mutated_total += 1
            entry.migration_status = "complete"
            entry.migration_error = None
        except Exception as exc:  # noqa: BLE001 — per-Entry failure isolation
            logger.warning("migration runner — entry %s failed: %s", entry.id, exc)
            entry.migration_status = "failed"
            entry.migration_error = str(exc)[:500]
            failed_total += 1
        try:
            await entry.save()
        except Exception as exc:  # noqa: BLE001
            logger.warning("runner: final save failed for %s: %s", entry.id, exc)

    # CP-level rollup
    final_status = await rollup_cp_status(
        published_cp=published_cp,
        affected_entries=affected_entries,
    )

    # Single audit emit on runner completion — covers the full lifecycle via
    # details.state per locked decision #5.
    try:
        await emit_change_event(
            actor_kind="system",
            actor_id="migration_runner",
            action="migration.run",  # type: ignore[arg-type]
            resource_type="ContentProfile",
            resource_id=published_cp.id,
            before=None,
            after={"migration_status": final_status},
            scope=f"content_profile:{published_cp.id}",
            details={
                "state": "failed" if failed_total else "complete",
                "mutated_entry_count": mutated_total,
                "failed_entry_count": failed_total,
                "affected_entry_count": len(affected_entries),
                "ops_applied": ops_applied,
            },
        )
    except Exception as exc:  # noqa: BLE001 — audit best-effort
        logger.warning("migration runner — emit_change_event failed: %s", exc)
    return {
        "status": final_status,
        "mutated_entry_count": mutated_total,
        "failed_entry_count": failed_total,
        "affected_entry_count": len(affected_entries),
    }


async def run_migration_async(
    *,
    published_cp: ContentProfile,
    compiled_manifest: Dict[str, Any],
    actor_id: Optional[str] = None,
    await_runner: bool = False,
) -> Dict[str, Any]:
    """Public entry point spawned by ``publish_draft``.

    Pre-marks every affected Entry ``migration_status='pending'`` +
    ContentProfile.migration_status ``'in_progress'`` SYNCHRONOUSLY (before
    the HTTP response returns). Then fire-and-forget spawns
    ``_async_migration_runner`` via ``asyncio.create_task``.

    ``await_runner=True`` awaits the spawned task inline — used by tests to
    observe terminal state without polling. Production callers leave it
    False so the HTTP response returns immediately.
    """
    affected_entries = await gather_affected_entries(published_cp)
    await mark_entries_pending(affected_entries)
    try:
        published_cp.migration_status = "in_progress"
        await published_cp.save()
    except Exception as exc:  # noqa: BLE001
        logger.warning("run_migration_async: cp pre-mark failed: %s", exc)
    task = asyncio.create_task(
        _async_migration_runner(
            published_cp=published_cp,
            compiled_manifest=compiled_manifest,
            affected_entries=affected_entries,
            actor_id=actor_id,
        )
    )
    if await_runner:
        await task
    return {
        "status": "running",
        "affected_entry_count": len(affected_entries),
    }
