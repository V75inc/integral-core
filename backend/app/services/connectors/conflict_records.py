"""Phase 5 Plan 05-03 — Conflict Node helpers.

Thin helpers around the ``Conflict`` Node (defined in
``backend/app/models/nodes.py``) — create + list + resolve. Consumed by
``app/api/conflicts.py`` (REST surface) and ``app/services/connectors/sync_runtime.py``
(``manual_resolve`` policy creates a Conflict on local-edit collision).

Locked decision #7 — Conflict shape is locked (9 fields). Locked decision #5
— ``conflict.resolve`` is a PolicyAction member (used to gate the REST
endpoint) but is intentionally NOT in ``ChangeEventAction``: the
audit-trail for an applied_external resolution is the resulting
``entry.update`` ChangeEvent. See I-SYNC-04.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from app.models.nodes import Conflict, Entry

logger = logging.getLogger(__name__)


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _snapshot_entry(entry: Entry) -> Dict[str, Any]:
    """Stable snapshot of the local Entry at the moment a conflict is detected.

    Captures the user-facing fields that ``last_write_wins`` /
    ``mirror_only`` would overwrite — so a future resolver can inspect or
    restore them. The shape matches the runtime's ``_snapshot`` helper to
    keep parity across the two paths.
    """
    return {
        "title": entry.title,
        "body": entry.body,
        "tags": list(entry.tags or []),
        "custom_fields": dict(entry.custom_fields or {}),
        "updated_at": entry.updated_at,
    }


async def create_conflict_record(
    *,
    connector_id: str,
    entry: Entry,
    external_snapshot: Dict[str, Any],
) -> Conflict:
    """Persist a Conflict Node describing a local/external divergence.

    Called by ``sync_one_connector`` when ``conflict_policy == "manual_resolve"``
    AND the entry was edited locally after ``connector.last_synced_at``. The
    helper does NOT mutate the underlying Entry — the runtime branch that
    invokes this helper also skips the ``_apply_external`` call so the local
    edit survives until a human resolves the conflict.
    """
    local_snapshot = _snapshot_entry(entry)
    detected_at = _utc_now_iso()
    conflict = Conflict(
        connector_id=connector_id,
        entry_id=entry.id,
        local_snapshot=local_snapshot,
        external_snapshot=dict(external_snapshot or {}),
        detected_at=detected_at,
        status="open",
    )
    await conflict.save()
    # Phase 10.5 Plan 10.5-04 (I-GRAPH-01): wire Entry -HAS_CONFLICT-> Conflict
    # so the divergence record is reachable from the rooted Entry
    # subgraph. Touches I-SYNC-04 — semantics unchanged, reachability added.
    from app.models.edges import HAS_CONFLICT

    try:
        await entry.connect(conflict, edge=HAS_CONFLICT, detected_at=detected_at)
    except Exception as exc:
        logger.exception(
            "create_conflict_record: HAS_CONFLICT wire failed entry=%s conflict=%s",
            entry.id,
            conflict.id,
        )
        try:
            await conflict.delete()
        except Exception:
            logger.exception(
                "create_conflict_record: rollback delete failed conflict=%s",
                conflict.id,
            )
        raise RuntimeError(
            f"HAS_CONFLICT wire failed for entry={entry.id} conflict={conflict.id}"
        ) from exc
    logger.info(
        "conflict record created — connector=%s entry=%s conflict=%s",
        connector_id,
        entry.id,
        conflict.id,
    )
    return conflict


async def list_conflicts(
    *,
    connector_id: Optional[str] = None,
    status: Optional[str] = None,
) -> List[Conflict]:
    """Query helper for ``GET /api/conflicts``.

    Filters are applied via jvspatial ``context.<field>`` selectors. Empty
    filter returns every Conflict (paged-by-caller-policy).
    """
    filters: Dict[str, Any] = {}
    if connector_id:
        filters["context.connector_id"] = connector_id
    if status:
        filters["context.status"] = status
    results = await Conflict.find(filters) if filters else await Conflict.find()
    return list(results)


async def resolve_conflict(
    *,
    conflict: Conflict,
    resolution: str,
    resolver_user_id: str,
) -> Conflict:
    """Mark a Conflict resolved.

    The caller is responsible for actually applying the resolution to the
    underlying Entry — this helper ONLY mutates the Conflict row.

    - ``kept_local``       — no-op on the Entry (the local edit stays).
    - ``applied_external`` — caller writes ``external_snapshot`` into the
      Entry via the standard update path (which emits an ``entry.update``
      ChangeEvent that doubles as the audit trail; see I-SYNC-04).
    - ``merged``           — caller writes a hand-merged payload into the
      Entry (out-of-scope for v1; the REST surface accepts the literal so
      future automation can route through it).
    """
    if resolution not in {"kept_local", "applied_external", "merged"}:
        raise ValueError(f"Unknown resolution: {resolution!r}")
    conflict.resolution = resolution
    conflict.resolved_at = _utc_now_iso()
    conflict.resolved_by = resolver_user_id
    conflict.status = "resolved"
    await conflict.save()
    return conflict
