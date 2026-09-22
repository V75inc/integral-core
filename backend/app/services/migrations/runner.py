"""Durable per-Entry migration runner.

Wraps the existing 9-op declarative runner at
``backend/app/services/operational_model_migrations.py`` with per-Entry
status tracking plus a durable WorkItem lifecycle. Production callers enqueue
an idempotent ``kind=migration`` work item after publishing the schema. The
worker claims it under a lease and recovers it after restart; no production
migration depends on an in-process ``asyncio.create_task``. ``await_runner``
remains a deterministic test seam for the low-level runner.

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

import hashlib
import json
import logging
from typing import Any, Dict, List, Optional

from app.models.nodes import Entry, OperationalModel
from app.schemas.policy import Resource, Subject
from app.services.change_event import emit_change_event
from app.services.operational_model_migrations import (
    _OP_HANDLERS,
    _affected_tracks,
)
from app.services.policy_engine import evaluate as policy_evaluate

from .rollup import rollup_cp_status

logger = logging.getLogger(__name__)

_SYSTEM_SUBJECT = Subject(kind="system", id="migration_runner")


async def gather_affected_entries(published_cp: OperationalModel) -> List[Entry]:
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


async def migration_status_snapshot(
    published_cp: OperationalModel,
    *,
    sample_limit: int = 20,
) -> Dict[str, Any]:
    """Return durable migration progress and actionable failed-entry samples."""

    entries = await gather_affected_entries(published_cp)
    counts = {"pending": 0, "running": 0, "complete": 0, "failed": 0}
    failed_entries: List[Dict[str, str]] = []
    for entry in entries:
        status = str(getattr(entry, "migration_status", "complete") or "complete")
        counts[status if status in counts else "failed"] += 1
        if status == "failed" and len(failed_entries) < sample_limit:
            failed_entries.append(
                {
                    "entry_id": entry.id,
                    "track_id": str(getattr(entry, "track_id", "") or ""),
                    "error": str(getattr(entry, "migration_error", "") or ""),
                }
            )
    return {
        "operational_model_id": published_cp.id,
        "status": str(
            getattr(published_cp, "migration_status", "complete") or "complete"
        ),
        "affected_entry_count": len(entries),
        "counts": counts,
        "failed_entries": failed_entries,
    }


async def reconcile_orphaned_migrations() -> Dict[str, int]:
    """Reconcile only legacy migrations with no durable WorkItem authority."""
    from app.agentive.work_models import WorkItem

    active_work_profile_ids = {
        str((item.input_payload or {}).get("operational_model_id") or "")
        for item in await WorkItem.find({"context.kind": "migration"})
        if str(getattr(item, "status", "") or "")
        in {"queued", "running", "retry_wait", "waiting_for_human", "waiting_for_event"}
    }
    profiles = await OperationalModel.find({"context.migration_status": "in_progress"})
    reconciled_profiles = 0
    reconciled_entries = 0
    for profile in profiles:
        if profile.id in active_work_profile_ids:
            continue
        entries = await gather_affected_entries(profile)
        changed = False
        for entry in entries:
            status = str(getattr(entry, "migration_status", "complete") or "complete")
            if status not in {"pending", "running"}:
                continue
            entry.migration_status = "failed"
            entry.migration_error = (
                "Migration interrupted by process restart; retry required."
            )
            await entry.save()
            reconciled_entries += 1
            changed = True
        if changed:
            profile.migration_status = "failed"
            await profile.save()
            reconciled_profiles += 1
    return {
        "profiles": reconciled_profiles,
        "entries": reconciled_entries,
    }


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
    published_cp: OperationalModel,
    compiled_manifest: Dict[str, Any],
    affected_entries: List[Entry],
    actor_id: Optional[str] = None,
) -> Dict[str, Any]:
    """Background task spawned by ``publish_draft``.

    Groups affected entries by parent Track, runs every declarative operation
    once per Track, then marks the Track's entries ``"complete"`` or
    ``"failed"``. Operation handlers are Track-wide by contract; invoking one
    per Entry would reapply the same transform repeatedly.

    Emits a single ``migration.run`` ChangeEvent on completion + rolls the
    OperationalModel.migration_status up.
    """
    # Audit-only policy gate — system subject short-circuits in policy_engine
    # (Plan 03-01 system-bypass). Re-raises on Decision(allowed=False) would
    # never happen but we surface the evaluation for observability.
    try:
        await policy_evaluate(
            subject=_SYSTEM_SUBJECT,
            action="migration.run",
            resource=Resource(
                kind="operational_model",
                id=published_cp.id,
                scope=f"operational_model:{published_cp.id}",
            ),
        )
    except Exception as exc:  # noqa: BLE001 — audit-only
        logger.warning("migration runner policy_evaluate failed: %s", exc)

    mutated_entry_ids: set[str] = set()
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

    # Affected tracks once. Op handlers consume a whole Track, so group the
    # pending Entries by their durable track_id and execute each op once.
    tracks = await _affected_tracks(published_cp)
    track_by_id = {t.id: t for t in tracks}
    entries_by_track: Dict[str, List[Entry]] = {}
    for entry in affected_entries:
        try:
            entry.migration_status = "running"
            await entry.save()
        except Exception as exc:  # noqa: BLE001
            logger.warning("runner: mark running failed for %s: %s", entry.id, exc)
        track_id = str(getattr(entry, "track_id", "") or "")
        entries_by_track.setdefault(track_id, []).append(entry)

    for track_id, track_entries in entries_by_track.items():
        parent_track = track_by_id.get(track_id)
        if parent_track is None and len(tracks) == 1:
            # Legacy entries can lack track_id. A singleton affected scope is
            # unambiguous; otherwise fail closed rather than mutate a sibling.
            parent_track = tracks[0]
        failure: Optional[str] = None
        try:
            if parent_track is None:
                raise RuntimeError("Entry has no unambiguous parent track")
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
                await handler(track=parent_track, op=op, log=log)
                if log["errors"]:
                    raise RuntimeError(
                        "; ".join(str(e.get("reason") or e) for e in log["errors"][:3])
                    )
                mutated_entry_ids.update(
                    str(entry_id) for entry_id in log["mutated_entries"]
                )
        except Exception as exc:  # noqa: BLE001 — isolate one Track from siblings
            failure = str(exc)[:500]
            logger.warning("migration runner — track %s failed: %s", track_id, exc)

        for entry in track_entries:
            if failure is None:
                entry.migration_status = "complete"
                entry.migration_error = None
            else:
                entry.migration_status = "failed"
                entry.migration_error = failure
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
            resource_type="OperationalModel",
            resource_id=published_cp.id,
            before=None,
            after={"migration_status": final_status},
            scope=f"operational_model:{published_cp.id}",
            details={
                "state": "failed" if failed_total else "complete",
                "mutated_entry_count": len(mutated_entry_ids),
                "failed_entry_count": failed_total,
                "affected_entry_count": len(affected_entries),
                "ops_applied": ops_applied,
            },
        )
    except Exception as exc:  # noqa: BLE001 — audit best-effort
        logger.warning("migration runner — emit_change_event failed: %s", exc)
    return {
        "status": final_status,
        "mutated_entry_count": len(mutated_entry_ids),
        "failed_entry_count": failed_total,
        "affected_entry_count": len(affected_entries),
    }


def _manifest_fingerprint(manifest: Dict[str, Any]) -> str:
    """Stable identity for the exact migration contract queued for execution."""
    encoded = json.dumps(
        manifest, sort_keys=True, separators=(",", ":"), default=str
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


async def resolve_migration_work_scope(
    published_cp: OperationalModel,
) -> tuple[str, str, str]:
    """Resolve durable work scope from a profile's App or Track attachment.

    App-attached profiles retain ``app_id`` as a fast path. Track-attached
    profiles intentionally do not duplicate their Track's workspace onto the
    profile, so resolve that structural owner before queuing durable work.
    """
    workspace_id = str(getattr(published_cp, "workspace_id", "") or "")
    app_id = str(getattr(published_cp, "app_id", "") or "")
    definition_id = ""

    from app.models.edges import CONTAINS
    from app.models.nodes import App, Track

    if app_id:
        app_node = await App.get(app_id)
        if app_node is None:
            raise RuntimeError("migration profile App no longer exists")
        app_workspace_id = str(getattr(app_node, "workspace_id", "") or "")
        if workspace_id and app_workspace_id and workspace_id != app_workspace_id:
            raise RuntimeError("migration profile/App workspace mismatch")
        workspace_id = app_workspace_id or workspace_id
        definition_id = str(getattr(app_node, "active_definition_id", "") or "")
    elif not workspace_id:
        attached_tracks = await Track.find(
            {"context.attached_operational_model_id": published_cp.id}
        )
        if len(attached_tracks) > 1:
            raise RuntimeError("migration profile is attached to multiple Tracks")
        if attached_tracks:
            track = attached_tracks[0]
            workspace_id = str(getattr(track, "workspace_id", "") or "")
            parent_apps = await track.nodes(
                edge=[CONTAINS], direction="in", node=["WorkspaceApp"], limit=2
            )
            if len(parent_apps) > 1:
                raise RuntimeError("migration Track belongs to multiple Apps")
            if parent_apps:
                app_node = parent_apps[0]
                app_id = str(getattr(app_node, "id", "") or "")
                app_workspace_id = str(getattr(app_node, "workspace_id", "") or "")
                if (
                    workspace_id
                    and app_workspace_id
                    and workspace_id != app_workspace_id
                ):
                    raise RuntimeError("migration Track/App workspace mismatch")
                workspace_id = app_workspace_id or workspace_id
                definition_id = str(getattr(app_node, "active_definition_id", "") or "")
    if not workspace_id:
        raise RuntimeError("migration profile has no workspace scope")
    return workspace_id, app_id, definition_id


async def _migration_work_scope(
    published_cp: OperationalModel,
) -> tuple[str, str, str]:
    """Backward-compatible private alias for existing migration callers."""
    return await resolve_migration_work_scope(published_cp)


async def enqueue_migration_work(
    *,
    published_cp: OperationalModel,
    compiled_manifest: Dict[str, Any],
    actor_id: Optional[str],
) -> Dict[str, Any]:
    """Persist a restart-safe migration plan and return its public tracker."""
    from app.agentive.services.work_items import enqueue_work_item

    workspace_id, app_id, definition_id = await resolve_migration_work_scope(
        published_cp
    )
    principal_id = str(actor_id or "system:migration").strip()
    if not principal_id:
        raise RuntimeError("migration actor is required")
    fingerprint = _manifest_fingerprint(compiled_manifest)
    work = await enqueue_work_item(
        kind="migration",
        origin="operational_model",
        principal_id=principal_id,
        workspace_id=workspace_id,
        idempotency_key=f"migration:{published_cp.id}:{fingerprint}",
        input_payload={
            "operational_model_id": published_cp.id,
            "manifest_fingerprint": fingerprint,
        },
        plan_revision=str(getattr(published_cp, "version_number", "") or fingerprint),
        plan={"kind": "migration", "operational_model_id": published_cp.id},
        precommit_draft={"manifest_fingerprint": fingerprint},
        remaining_obligations=[
            {
                "kind": "migration_completion",
                "operational_model_id": published_cp.id,
                "explanation": "Await durable migration work completion.",
            }
        ],
        app_id=app_id or None,
        definition_id=definition_id or None,
    )
    # A published schema is not writable until its migration reaches a
    # terminal state. The write gate blocks queued and in-progress work alike.
    published_cp.migration_status = "queued"
    await published_cp.save()
    return {
        "status": str(getattr(work, "status", "queued") or "queued"),
        "work_item_id": work.work_item_id,
        "affected_entry_count": None,
    }


async def execute_migration_work_item(
    *,
    published_cp: OperationalModel,
    expected_manifest_fingerprint: str,
    actor_id: Optional[str] = None,
) -> Dict[str, Any]:
    """Run a claimed durable migration against the exact queued manifest."""
    compiled_manifest = dict(getattr(published_cp, "manifest", None) or {})
    actual_fingerprint = _manifest_fingerprint(compiled_manifest)
    if actual_fingerprint != expected_manifest_fingerprint:
        from app.schemas.agentive.work import WorkError

        published_cp.migration_status = "failed"
        await published_cp.save()
        raise WorkError(
            "work.definition_stale",
            "migration profile changed after it was queued; publish a new migration plan",
        )
    affected_entries = await gather_affected_entries(published_cp)
    await mark_entries_pending(affected_entries)
    published_cp.migration_status = "in_progress"
    await published_cp.save()
    return await _async_migration_runner(
        published_cp=published_cp,
        compiled_manifest=compiled_manifest,
        affected_entries=affected_entries,
        actor_id=actor_id,
    )


async def run_migration_async(
    *,
    published_cp: OperationalModel,
    compiled_manifest: Dict[str, Any],
    actor_id: Optional[str] = None,
    await_runner: bool = False,
) -> Dict[str, Any]:
    """Queue migration work; retain direct execution only for focused tests.

    Production calls persist a WorkItem before returning. A process restart
    therefore leaves recoverable queued/running work, rather than an orphaned
    event-loop task.
    """
    if not await_runner:
        return await enqueue_migration_work(
            published_cp=published_cp,
            compiled_manifest=compiled_manifest,
            actor_id=actor_id,
        )

    affected_entries = await gather_affected_entries(published_cp)
    await mark_entries_pending(affected_entries)
    published_cp.migration_status = "in_progress"
    await published_cp.save()
    result = await _async_migration_runner(
        published_cp=published_cp,
        compiled_manifest=compiled_manifest,
        affected_entries=affected_entries,
        actor_id=actor_id,
    )
    return {
        "status": result["status"],
        "affected_entry_count": len(affected_entries),
    }
