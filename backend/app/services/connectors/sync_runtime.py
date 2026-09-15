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

import dataclasses
import logging
from datetime import datetime, timezone
from typing import Any, Dict, Optional

from app.models.nodes import Entry, EntryType, Track
from app.schemas.provenance import Provenance
from app.services.change_event import emit_change_event
from app.services.connectors.conflict_records import create_conflict_record
from app.services.connectors.registry import get_sync_connector

logger = logging.getLogger(__name__)


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _parse_iso(value: Optional[str]) -> Optional[datetime]:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value)
    except (ValueError, TypeError):
        return None


def _local_edited_after_sync(entry: Entry, last_synced_at: Optional[str]) -> bool:
    """Returns True iff the local Entry has been edited since the last sync.

    Edge case (first sync): ``last_synced_at`` is ``None`` — by definition
    there cannot be a local edit "since" a sync that never happened, so
    returns False.

    Edge case (non-parseable timestamps): treat as "no detectable local
    edit" rather than raising — the worst case is one extra overwrite,
    which is preferable to crashing the sync loop on a stray ISO format.
    """
    if not last_synced_at:
        return False
    local_dt = _parse_iso(entry.updated_at)
    last_dt = _parse_iso(last_synced_at)
    if local_dt is None or last_dt is None:
        return False
    return local_dt > last_dt


async def _apply_external(entry: Entry, materialized: Any) -> None:
    """Overwrite local Entry fields from the external materialized payload.

    Also restamps ``provenance.synced_at`` so subsequent local-edit detection
    on the next sync compares against the right baseline. ``provenance`` is
    only restamped when the entry's source is ``"connector"`` — defensive
    guard against accidentally rewriting human/agent provenance for an entry
    that the runtime tried to upsert through a key collision.
    """
    entry.title = materialized.title
    entry.body = materialized.body
    entry.tags = list(materialized.tags or [])
    entry.custom_fields = dict(materialized.custom_fields or {})
    prov = entry.provenance
    if prov is not None and prov.source == "connector":
        prov.synced_at = _utc_now()
        entry.provenance = prov
    entry.updated_at = _utc_now_iso()
    await entry.save()


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
    use :func:`_resolve_bound_tracks_by_entry_type` instead.
    """
    try:
        bound = await connector.nodes(
            edge=["IsConnectedTo"], direction="out", node=["Track"]
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


async def _resolve_bound_tracks_by_entry_type(
    connector,
) -> Dict[str, Track]:
    """Walk every IS_CONNECTED_TO Track and index its EntryTypes by key.

    Phase 18 — per-entity routing for multi-track connectors. The
    QuickBooks connector pulls five entity types (Invoice / Purchase /
    Customer / Vendor / Bill) and binds to five Finance App tracks; this
    helper builds a {entry_type_key → Track} map so the write loop can
    route each materialized record to the track whose ContentProfile
    declares the matching EntryType.

    The map is additive: a single-track connector (e.g. GitHub Issues
    bound to one track with one ``github_issue`` EntryType) populates one
    map entry, and the write loop's fall-back to ``_resolve_bound_track``
    keeps that path working unchanged.

    Returns an empty dict when the connector has no bound Tracks.
    """
    out: Dict[str, Track] = {}
    try:
        bound = await connector.nodes(
            edge=["IsConnectedTo"], direction="out", node=["Track"]
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "_resolve_bound_tracks_by_entry_type: edge traversal failed for "
            "connector %s: %s",
            getattr(connector, "id", "<unknown>"),
            exc,
        )
        return out
    for tnode in bound:
        if not isinstance(tnode, Track):
            continue
        # A Track's EntryTypes hang off its attached ContentProfile via
        # CONTAINS — walk one hop through the profile to enumerate them.
        try:
            cps = await tnode.nodes(
                edge=["HAS_CONTENT_PROFILE"], direction="out", node=["ContentProfile"]
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "_resolve_bound_tracks_by_entry_type: HAS_CONTENT_PROFILE walk "
                "failed for track %s: %s",
                tnode.id,
                exc,
            )
            continue
        for cp in cps:
            try:
                ets = await cp.nodes(
                    edge=["CONTAINS"], direction="out", node=["EntryType"]
                )
            except Exception as exc:  # noqa: BLE001
                logger.warning(
                    "_resolve_bound_tracks_by_entry_type: EntryType walk failed "
                    "for content_profile %s: %s",
                    cp.id,
                    exc,
                )
                continue
            for et in ets:
                if not isinstance(et, EntryType):
                    continue
                # EntryType nodes carry ``name`` (canonical), not ``key``.
                # The manifest's entry_types[].key normalizes via _slug(name)
                # (see content_profile_compile.slug_manifest_key); mirror
                # that here so the routing dict keys match the connector's
                # MaterializedEntry.entry_type_key values.
                from app.services.content_profile_compile import _slug

                key = _slug(getattr(et, "name", "") or "")
                if key:
                    out[key] = tnode
    return out


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


async def sync_one_connector(connector) -> Dict[str, int]:
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

    # Phase 18 — multi-track routing. For connectors bound to >1 Track
    # (e.g. QuickBooks bound to the five Finance tracks), build a
    # {entry_type_key → Track} map so each materialized record routes to
    # the track whose ContentProfile declares the matching EntryType.
    # Single-track connectors yield an empty/one-entry map; the fall-back
    # ``target_track`` preserves their behavior.
    track_by_entry_type = await _resolve_bound_tracks_by_entry_type(connector)

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
            key = sub.idempotency_key_for(record)
            existing = await Entry.find({"context.idempotency_key": key})
            materialized = sub.to_entry(record)

            # Phase 18 — route per-record by the materialized
            # entry_type_key when the connector has multi-track bindings.
            # Falls back to the legacy single-bound-track resolution.
            entry_type_key = (getattr(materialized, "entry_type_key", "") or "").strip()
            routed_track = (
                track_by_entry_type.get(entry_type_key) if entry_type_key else None
            )
            record_target_track = routed_track or target_track

            if existing:
                entry = existing[0]
                # CRITICAL — capture before_snap BEFORE _apply_external mutates
                # entry in-place. `existing[0] is entry`, so reading
                # _snapshot(existing[0]) AFTER _apply_external would give
                # `before == after` and silently destroy the audit trail
                # (T-05-03-03 mitigation).
                before_snap = _snapshot(entry)

                if sub.conflict_policy == "mirror_only":
                    await _apply_external(entry, materialized)
                    stats["updated"] += 1
                elif sub.conflict_policy == "last_write_wins":
                    if _local_edited_after_sync(entry, connector.last_synced_at):
                        # T-05-03-03 — explicit WARNING surfaces silent
                        # local-edit overwrites in operator logs.
                        logger.warning(
                            "connector sync: overwriting local edit on entry %s "
                            "(connector=%s, policy=last_write_wins)",
                            entry.id,
                            connector.id,
                        )
                    await _apply_external(entry, materialized)
                    stats["updated"] += 1
                else:  # manual_resolve
                    if _local_edited_after_sync(entry, connector.last_synced_at):
                        await create_conflict_record(
                            connector_id=connector.id,
                            entry=entry,
                            external_snapshot=_materialized_to_dict(materialized),
                        )
                        stats["conflict"] += 1
                        # No emit, no _apply_external — the local edit is
                        # preserved until a human resolves the Conflict.
                        continue
                    await _apply_external(entry, materialized)
                    stats["updated"] += 1

                # D-05 single-emission — entry.update for upsert.
                await emit_change_event(
                    actor_kind="connector",
                    actor_id=actor_id,
                    action="entry.update",
                    resource_type="Entry",
                    resource_id=entry.id,
                    before=before_snap,
                    after=_snapshot(entry),
                    scope=f"track:{entry.track_id}",
                )
                # Phase 4 re-embed parity (synced upsert).
                await _reembed_synced_entry(entry)
                await _run_connector_hooks(
                    entry=entry,
                    connector=connector,
                    entry_type_key=entry_type_key,
                )
            else:
                # First-time materialization.
                provenance = Provenance(
                    source="connector",  # I-CON-01 — ActorKind Literal member
                    source_id=f"{connector.id}:{record.external_id}",  # I-CON-01 split shape
                    confidence=1.0,
                    synced_at=_utc_now(),
                )
                now = _utc_now_iso()
                entry = Entry(
                    title=materialized.title,
                    body=materialized.body,
                    track_id=record_target_track.id,
                    tags=list(materialized.tags or []),
                    custom_fields=dict(materialized.custom_fields or {}),
                    provenance=provenance,
                    idempotency_key=key,
                    created_at=now,
                    updated_at=now,
                )
                await entry.save()

                # Wire CONTAINS edge so the Track → Entry traversal works,
                # mirroring api/entries.py:402 create_entry idiom.
                from app.models.edges import CONTAINS

                await record_target_track.connect(entry, edge=CONTAINS, added_at=now)
                stats["created"] += 1

                # D-05 single-emission — entry.create.
                await emit_change_event(
                    actor_kind="connector",
                    actor_id=actor_id,
                    action="entry.create",
                    resource_type="Entry",
                    resource_id=entry.id,
                    before=None,
                    after=_snapshot(entry),
                    scope=f"track:{entry.track_id}",
                )
                # Phase 4 re-embed parity (synced create).
                await _reembed_synced_entry(entry)
                await _run_connector_hooks(
                    entry=entry,
                    connector=connector,
                    entry_type_key=entry_type_key,
                )
    except Exception as exc:  # noqa: BLE001
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
    await connector.save()

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
