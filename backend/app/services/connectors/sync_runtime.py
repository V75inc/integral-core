"""Phase 5 Plan 05-03 — ``sync_one_connector`` runtime.

CON-03 sync semantics — pull-only:

* Per-record idempotency dedup (via ``Entry.idempotency_key``) BEFORE write —
  re-sync is upsert, never duplicate (I-SYNC-03).
* Conflict-policy dispatch on existing entries — ``mirror_only`` /
  ``last_write_wins`` / ``manual_resolve`` (locked decision §Q10).
* Connector-as-actor on every emit — every ChangeEvent carries
  ``actor_kind="connector"`` + ``actor_id=<connector.id>`` (I-SYNC-01).
* Provenance split shape — every materialized Entry uses
  ``Provenance(source="connector", source_id="<connector_id>:<external_id>", …)``
  — NEVER the free-string ``"connector:..."`` form (I-CON-01).
* Phase 4 re-embed hooks fire automatically — synced Entries become
  semantically searchable on first write WITHOUT any explicit
  ``embedding_store.upsert`` call from here (the entries.py re-embed hooks
  do NOT run on direct Node write, so we make the explicit best-effort
  call ourselves to preserve the contract).
* D-05 single-emission-path invariant — every persisted ChangeEvent goes
  through ``emit_change_event``, NEVER ``ChangeEvent.create`` directly.

I-CON-02 — subclass dispatch reads ``connector.subclass_slug`` (Plan 05-01),
NEVER ``connector.mapping_profile`` (deprecated by the
``IS_CONNECTED_TO`` edge).
"""

from __future__ import annotations

import asyncio
import dataclasses
import hashlib
import json
import logging
from datetime import datetime, timezone
from typing import Any, Dict, Optional

from app.models.nodes import Entry, Track
from app.schemas.provenance import Provenance
from app.services.change_event import emit_change_event
from app.services.connectors.conflict_records import create_conflict_record
from app.services.connectors.registry import get_sync_connector
from app.services.connectors.sync_lease import (
    LEASE_SECONDS,
    claim_sync,
    current_sync_lease,
    release_sync,
    renew_sync,
    save_connector_changes,
    sync_effect,
)

logger = logging.getLogger(__name__)


def connector_record_key(
    *, workspace_id: str, connector_id: str, upstream_key: str
) -> str:
    """Runtime identity; two installed instances deliberately own separate records."""
    if not workspace_id or not connector_id or not upstream_key:
        raise ValueError(
            "Connector record identity requires workspace, instance and source"
        )
    identity = json.dumps(
        [workspace_id, connector_id, upstream_key], separators=(",", ":")
    )
    return "connector:v2:" + hashlib.sha256(identity.encode()).hexdigest()


async def _find_owned_entry(
    *, connector, record, track: Track, key: str, legacy_key: str
):
    """Never adopt a record from another destination or source owner.

    Legacy records are considered only on this bound destination. A collision
    or wrong provenance is an explicit reconciliation error, never a re-key.
    """
    source_id = f"{connector.id}:{record.external_id}"
    graph = await track.get_context()
    matches = await graph.find(
        Entry,
        {
            "context.track_id": track.id,
            "context.idempotency_key": {"$in": [key, legacy_key]},
        },
        limit=2,
    )
    if len(matches) > 1:
        raise ValueError("Ambiguous connector records require reconciliation")
    if not matches:
        return None
    entry = matches[0]
    prov = entry.provenance
    if prov is None or prov.source != "connector" or prov.source_id != source_id:
        raise ValueError("Connector record provenance does not match its binding")
    return entry


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _parse_iso(value: Optional[str]) -> Optional[datetime]:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value)
        return parsed.replace(tzinfo=timezone.utc) if parsed.tzinfo is None else parsed
    except (ValueError, TypeError):
        return None


def _local_edited_after_sync(entry: Entry, last_synced_at: Optional[str]) -> bool:
    """Returns True iff the local Entry has been edited since the last sync.

    Edge case (first sync): ``last_synced_at`` is ``None`` — by definition
    there cannot be a local edit "since" a sync that never happened, so
    returns False.

    Invalid timestamps on a previously synchronized record cannot prove that
    an overwrite is safe. Surface them as a conflict rather than losing edits.
    """
    provenance = getattr(entry, "provenance", None)
    baseline = provenance.synced_at if provenance else None
    if baseline is None and not last_synced_at:
        return False
    local_dt = _parse_iso(entry.updated_at)
    last_dt = baseline or _parse_iso(last_synced_at)
    if local_dt is None or last_dt is None:
        return True
    if last_dt.tzinfo is None:
        last_dt = last_dt.replace(tzinfo=timezone.utc)
    return local_dt > last_dt


def _snapshot(entry: Entry) -> Dict[str, Any]:
    """Stable audit-trail snapshot of the Entry's user-facing fields."""
    return {
        "title": entry.title,
        "body": entry.body,
        "tags": list(entry.tags or []),
        "custom_fields": dict(entry.custom_fields or {}),
    }


def _materialized_to_dict(materialized: Any) -> Dict[str, Any]:
    """Serialize a ``MaterializedEntry`` dataclass into a JSON-stable dict for
    persistence in ``Conflict.external_snapshot``."""
    if dataclasses.is_dataclass(materialized):
        return dataclasses.asdict(materialized)  # type: ignore[arg-type]
    # Defensive fallback — already a dict-like object.
    return dict(getattr(materialized, "__dict__", {}) or {})


async def _mark_unbound(connector) -> None:
    """Record "installed but not bound to a Track" as connector health.

    Best-effort — a sync that cannot run must not also fail on bookkeeping.
    """
    message = (
        "Connected, but not bound to a Track yet — nothing will sync until "
        "you bind this connector to a Track."
    )
    try:
        from app.utils.time import utc_now_iso

        if (getattr(connector, "health_status", "") or "") == "degraded" and (
            getattr(connector, "last_error", "") or ""
        ) == message:
            return
        connector.health_status = "degraded"
        connector.last_error = message
        connector.last_health_at = utc_now_iso()
        connector.updated_at = utc_now_iso()
        await connector.save()
    except Exception:  # noqa: BLE001
        logger.debug("sync_one_connector: unbound health update failed", exc_info=True)


async def _resolve_bound_track(connector) -> Optional[Any]:
    """Resolve the Track this connector is bound to via IS_CONNECTED_TO.

    I-CON-02 — bindings live on the IsConnectedTo edge, NEVER on
    ``Connector.mapping_profile``. Returns the first bound Track when only
    one binding exists; callers that need per-entity routing across multiple
    bound Tracks (Phase 18 Finance App — five tracks, one connector)
    use the authorized ``resolve_connector_bindings`` walker instead.
    """
    try:
        bound = await connector.nodes(
            edge=["IsConnectedTo"], direction="out", node=["Track"], limit=1
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "sync_one_connector: edge traversal failed for connector %s: %s",
            getattr(connector, "id", "<unknown>"),
            exc,
        )
        return None
    if not bound:
        return None
    return bound[0]


async def _reembed_synced_entry(entry: Entry) -> None:
    """Phase 4 re-embed parity for connector-materialized entries.

    The api/entries.py ``_reembed_entry`` hook runs INSIDE the HTTP handler
    flow — direct ``Entry().save()`` bypasses it. Mirror the same
    fire-and-forget try/except pattern so a missing wheel / failing store
    does NOT roll back the sync write (the embedding store is a projection,
    not a system-of-record; see api/entries.py:74-90).

    Skips entirely when ``semantic_retrieval_available()`` is False (the
    graph-only / null-store path — Wave 2 of the SaaS-deployment plan).
    Avoids loading the embedding model on a deployment that has no
    vector store to write to.
    """
    try:
        from app.services.retrieval import (
            get_embedding_store,
            semantic_retrieval_available,
        )
        from app.services.retrieval.embedding_model import embed_entry_text

        if not semantic_retrieval_available():
            return

        vector = await embed_entry_text(entry)
        await get_embedding_store().upsert(
            entry_id=entry.id,
            vector=vector,
            metadata={"track_id": getattr(entry, "track_id", "")},
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "sync_one_connector: re-embed failed for entry %s: %s", entry.id, exc
        )


async def _run_connector_hooks(
    *,
    entry: Entry,
    connector,
    entry_type_key: str,
) -> None:
    """Phase 30.5 — fire connector.dedup / connector.auto_link after materialize."""
    workspace_id = str(getattr(connector, "workspace_id", "") or "").strip()
    if not workspace_id and entry.track_id:
        track = await Track.get(entry.track_id)
        if track is not None:
            workspace_id = str(getattr(track, "workspace_id", "") or "").strip()
    slug = str(getattr(connector, "subclass_slug", "") or "").strip()
    actor = str(getattr(connector, "owner", "") or "")
    if not workspace_id or not slug:
        return
    from app.services.hooks.connector_runtime import run_connector_post_materialize

    await run_connector_post_materialize(
        entry=entry,
        workspace_id=workspace_id,
        connector_slug=slug,
        entry_type_key=entry_type_key,
        actor_user_id=actor,
    )


async def _sync_owned(connector, lease) -> Dict[str, int]:
    """Pull external records, dedupe via idempotency_key, materialize/update Entries.

    Returns stats dict ``{created, updated, conflict, skipped, failed}``.

    Locked decisions consumed:
      - #6 (idempotency): per-record dedup via ``Entry.idempotency_key``.
      - §Q10 (conflict): per-policy dispatch (3 branches).
      - §Q11 / I-CON-01 (provenance): split source + source_id shape.
      - §Q9 (actor): connector-as-actor on every emit.
      - §Q9 / I-SYNC-02 (no-op when registry is empty / unknown slug).

    The function is non-transactional: each per-record write commits
    independently. A mid-stream failure persists everything written up to
    that point AND emits ``connector.sync.failed`` with the error message.
    The exception is re-raised so the on-demand endpoint surfaces the
    failure to the caller; the background scheduler swallows it at the
    tick level (see ``sync_scheduler.sync_loop``).
    """
    # I-CON-02 — subclass dispatch via subclass_slug (NEVER mapping_profile).
    slug = (getattr(connector, "subclass_slug", "") or "").strip()
    stats: Dict[str, int] = {
        "created": 0,
        "updated": 0,
        "conflict": 0,
        "skipped": 0,
        "failed": 0,
    }
    actor_id = connector.id

    if not slug:
        logger.warning(
            "sync_one_connector: connector %s has no subclass_slug; skipping",
            connector.id,
        )
        return stats

    try:
        sub = get_sync_connector(slug)
    except ValueError:
        logger.warning(
            "sync_one_connector: no SyncConnector registered for slug %r "
            "(connector=%s); skipping",
            slug,
            connector.id,
        )
        return stats

    target_track = await _resolve_bound_track(connector)
    if target_track is None:
        logger.warning(
            "sync_one_connector: connector %s has no IS_CONNECTED_TO Track binding; "
            "skipping (I-CON-02 — never read mapping_profile)",
            connector.id,
        )
        # Surface it. A native connector installs, authorizes, reports no error
        # and then does nothing forever until someone separately creates a
        # Track binding — the only signal was this log line, which no operator
        # reads. ``degraded`` is the accurate word: authorized and reachable,
        # but not actually moving data.
        await _mark_unbound(connector)
        return stats

    from jvspatial.db.postgres import PostgresTransaction

    from app.schemas.policy import Resource, Subject
    from app.services.connectors.tag_resolution import resolve_connector_tags
    from app.services.entry_create import create_entry_in_track
    from app.services.entry_update import update_entry_in_track
    from app.services.permissions import resolve_role
    from app.services.policy_engine import evaluate
    from app.services.walkers.connector_bindings import resolve_connector_bindings

    bindings = await resolve_connector_bindings(connector)
    decision = await evaluate(
        subject=Subject(kind="connector", id=connector.id),
        action="connector.sync",
        resource=Resource(
            kind="connector", id=connector.id, scope=f"connector:{connector.id}"
        ),
        _dry_run=True,
    )
    if not decision.allowed:
        raise PermissionError("Connector policy does not authorize synchronization")
    # Lifecycle start emit — single emission path (D-05).
    await emit_change_event(
        actor_kind="connector",
        actor_id=actor_id,
        action="connector.sync.start",
        resource_type="Connector",
        resource_id=connector.id,
        before=None,
        after={"connector_id": connector.id, "slug": slug},
        scope=f"user:{connector.owner}",
    )

    try:
        async for record in sub.sync_pull(connector=connector):
            events: list[dict[str, Any]] = []
            outbox_id = None
            outcome = "updated"
            async with sync_effect(lease) as graph:
                from app.middleware.permissions_cache import reset_permissions_cache

                reset_permissions_cache()
                upstream_key = sub.idempotency_key_for(record)
                materialized = sub.to_entry(record)
                entry_type_key = str(materialized.entry_type_key or "").strip()
                if entry_type_key not in bindings:
                    raise ValueError(
                        "Connector payload references an undeclared bound entry type"
                    )
                record_target_track, entry_type = bindings[entry_type_key]
                # Re-traverse authorization under the effect context: binding or
                # owner permission changes during network I/O must take effect.
                live_bindings = await resolve_connector_bindings(connector)
                if (
                    entry_type_key not in live_bindings
                    or live_bindings[entry_type_key][0].id != record_target_track.id
                    or live_bindings[entry_type_key][1].id != entry_type.id
                ):
                    raise PermissionError(
                        "Connector destination binding changed during sync"
                    )
                record_target_track, entry_type = live_bindings[entry_type_key]
                workspace_id = lease.workspace_id
                key = connector_record_key(
                    workspace_id=workspace_id,
                    connector_id=connector.id,
                    upstream_key=upstream_key,
                )
                entry = await _find_owned_entry(
                    connector=connector,
                    record=record,
                    track=record_target_track,
                    key=key,
                    legacy_key=upstream_key,
                )
                action = "entry.update" if entry is not None else "entry.create"
                decision = await evaluate(
                    subject=Subject(kind="connector", id=connector.id),
                    action=action,
                    resource=Resource(
                        kind="entry",
                        id=entry.id if entry else "",
                        scope=f"connector:{connector.id}",
                    ),
                    _dry_run=True,
                )
                if not decision.allowed:
                    raise PermissionError(
                        "Connector policy does not authorize this write"
                    )
                if entry is not None and await resolve_role(
                    lease.principal_id, "entry", entry.id
                ) not in {"owner", "admin", "editor"}:
                    raise PermissionError("Connector owner cannot update this record")
                tag_ids = await resolve_connector_tags(
                    record_target_track, list(materialized.tags or [])
                )
                provenance = Provenance(
                    source="connector",
                    source_id=f"{connector.id}:{record.external_id}",
                    confidence=1.0,
                    synced_at=_utc_now(),
                )
                if entry is not None:
                    local_edit = _local_edited_after_sync(
                        entry, connector.last_synced_at
                    )
                    if sub.conflict_policy == "manual_resolve" and local_edit:
                        await create_conflict_record(
                            connector_id=connector.id,
                            entry=entry,
                            external_snapshot=_materialized_to_dict(materialized),
                        )
                        outcome = "conflict"
                    else:
                        if sub.conflict_policy == "last_write_wins" and local_edit:
                            logger.warning(
                                "connector sync: overwriting local edit on entry %s (connector=%s)",
                                entry.id,
                                connector.id,
                            )
                        entry = await update_entry_in_track(
                            entry_id=entry.id,
                            user_id=lease.principal_id,
                            workspace_id=workspace_id,
                            actor_kind="connector",
                            actor_id=connector.id,
                            title=materialized.title,
                            body=materialized.body,
                            tags=tag_ids,
                            type_id=entry_type.id,
                            custom_fields=dict(materialized.custom_fields or {}),
                            replace_custom_fields=True,
                            provenance=provenance,
                            idempotency_key=key,
                            expected_record_revision=entry.record_revision,
                            change_event_sink=events.append,
                        )
                else:
                    entry = await create_entry_in_track(
                        track=record_target_track,
                        user_id=lease.principal_id,
                        workspace_id=workspace_id,
                        actor_kind="connector",
                        actor_id=connector.id,
                        title=materialized.title,
                        body=materialized.body,
                        entry_type=entry_type,
                        tags=tag_ids,
                        custom_fields=dict(materialized.custom_fields or {}),
                        provenance=provenance,
                        idempotency_key=key,
                        change_event_sink=events.append,
                    )
                    outcome = "created"
                if outcome != "conflict":
                    await _run_connector_hooks(
                        entry=entry, connector=connector, entry_type_key=entry_type_key
                    )
                if events and isinstance(graph.database, PostgresTransaction):
                    from app.services.app_operations.event_outbox import (
                        insert_entry_event,
                    )

                    outbox_id = await insert_entry_event(
                        transaction=graph.database,
                        workspace_id=workspace_id,
                        event=events[0],
                    )
            stats[outcome] += 1
            if outbox_id:
                from app.services.app_operations.event_outbox import (
                    deliver_operation_event,
                )

                try:
                    await deliver_operation_event(outbox_id=outbox_id)
                except Exception:
                    logger.exception("Connector change event awaits recovery")
            elif events:
                await emit_change_event(**events[0])
            if outcome != "conflict":
                fresh = await Entry.get(entry.id)
                if fresh is not None:
                    await _reembed_synced_entry(fresh)
    except Exception as exc:  # noqa: BLE001
        stats["failed"] += 1
        logger.warning(
            "connector sync failed for %s: %s", connector.id, exc, exc_info=True
        )
        await emit_change_event(
            actor_kind="connector",
            actor_id=actor_id,
            action="connector.sync.failed",
            resource_type="Connector",
            resource_id=connector.id,
            before=None,
            after={"error": str(exc)[:500], "stats": dict(stats)},
            scope=f"user:{connector.owner}",
        )
        raise

    # Advance the connector's last_synced_at cursor on successful pull
    # completion. The next tick's local-edit detection compares against
    # this stamp (see ``_local_edited_after_sync``).
    connector.last_synced_at = _utc_now_iso()
    await save_connector_changes(connector, fields=("last_synced_at", "sync_cursor"))

    await emit_change_event(
        actor_kind="connector",
        actor_id=actor_id,
        action="connector.sync.complete",
        resource_type="Connector",
        resource_id=connector.id,
        before=None,
        after=dict(stats),
        scope=f"user:{connector.owner}",
    )
    return stats


async def sync_one_connector(connector) -> Dict[str, int]:
    """Manual and scheduled entry point share one durable exclusive owner."""
    stats = {"created": 0, "updated": 0, "conflict": 0, "skipped": 0, "failed": 0}
    slug = str(getattr(connector, "subclass_slug", "") or "").strip()
    try:
        get_sync_connector(slug)
    except ValueError:
        return stats
    if await _resolve_bound_track(connector) is None:
        await _mark_unbound(connector)
        return stats
    lease = await claim_sync(connector)
    if lease is None:
        stats["skipped"] = 1
        return stats

    async def heartbeat():
        while True:
            await asyncio.sleep(LEASE_SECONDS / 3)
            await renew_sync(lease)

    pulse = asyncio.create_task(heartbeat(), name=f"connector-heartbeat:{connector.id}")

    async def execute():
        token = current_sync_lease.set(lease)
        try:
            return await _sync_owned(connector, lease)
        finally:
            current_sync_lease.reset(token)

    work = asyncio.create_task(execute(), name=f"connector-sync:{connector.id}")
    try:
        done, _ = await asyncio.wait({pulse, work}, return_when=asyncio.FIRST_COMPLETED)
        if pulse in done:
            await pulse
        return await work
    finally:
        for task in (work, pulse):
            if not task.done():
                task.cancel()
        await asyncio.gather(work, pulse, return_exceptions=True)
        try:
            await release_sync(lease)
        except Exception:
            # A failed cleanup cannot authorize effects; expiry still reclaims
            # the lease. Preserve the original failure/cancellation result.
            logger.exception("Connector lease release failed; awaiting expiry")
