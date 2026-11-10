"""Workspace-scoped attachment storage accounting.

Tracks denormalised bytes-used per ``Workspace`` so the UI can show a
usage bar without scanning the attachment graph on every render, and so
quota enforcement runs as a cheap O(1) read on the upload path.

Single source of truth for *current* usage is the denormalised counter
on the Workspace node. The attachment graph is the *authoritative*
ledger; ``recalculate_for_workspace`` reconciles drift by re-summing.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import List, Optional

from app.config import settings
from app.models.nodes import App, Attachment, ChatThread, Entry, Track, Workspace

logger = logging.getLogger(__name__)

SOFT_WARN_THRESHOLD = 0.80


async def _fresh_workspace(workspace_id: str) -> Optional[Workspace]:
    """Read the persisted counter instead of an older hydrated node."""
    from jvspatial.core.context import get_default_context

    from app.services.authority_facts import primary_node_record

    context = get_default_context()
    row = await primary_node_record(context.database, Workspace, workspace_id)
    return await context._deserialize_entity(Workspace, row) if row else None


@dataclass(frozen=True)
class StorageUsage:
    """Snapshot of a workspace's storage accounting."""

    workspace_id: str
    bytes_used: int
    quota_bytes: int  # 0 = unlimited
    enforcement_enabled: bool

    @property
    def percent(self) -> float:
        """Bytes-used as a percentage of quota (0 when unlimited)."""
        if self.quota_bytes <= 0:
            return 0.0
        return min(100.0, (self.bytes_used / self.quota_bytes) * 100.0)

    @property
    def is_unlimited(self) -> bool:
        """True when no quota is configured."""
        return self.quota_bytes <= 0

    @property
    def is_soft_warning(self) -> bool:
        """True when usage crosses the soft-warning threshold."""
        if self.is_unlimited:
            return False
        return self.bytes_used >= int(self.quota_bytes * SOFT_WARN_THRESHOLD)

    def would_exceed(self, additional_bytes: int) -> bool:
        """True when ``additional_bytes`` would push usage past quota."""
        if self.is_unlimited:
            return False
        return (self.bytes_used + additional_bytes) > self.quota_bytes

    def to_dict(self) -> dict:
        """Serialise the snapshot for API responses."""
        return {
            "workspace_id": self.workspace_id,
            "bytes_used": int(self.bytes_used),
            "quota_bytes": int(self.quota_bytes),
            "percent": self.percent,
            "is_unlimited": self.is_unlimited,
            "is_soft_warning": self.is_soft_warning,
            "enforcement_enabled": self.enforcement_enabled,
            "soft_warn_threshold": SOFT_WARN_THRESHOLD,
        }


async def workspace_for_entry(entry: Entry) -> Optional[Workspace]:
    """Resolve the parent Workspace for an entry's track.

    Resolution order:
        1. ``track.workspace_id`` — populated for every Track post-W2.
        2. The App that contains the Track (via CONTAINS edge) and
           that App's ``workspace_id``.
        3. None — should not happen post-migration; treated as no-quota.
    """
    if not entry.track_id:
        return None
    track = await Track.get(entry.track_id)
    if track is None:
        return None
    workspace_id = (getattr(track, "workspace_id", "") or "").strip()
    if not workspace_id:
        try:
            apps = await track.nodes(edge=["CONTAINS"], direction="in")
        except Exception:  # noqa: BLE001
            apps = []
        for app_node in apps:
            sp_ws = (getattr(app_node, "workspace_id", "") or "").strip()
            if isinstance(app_node, App) and sp_ws:
                workspace_id = sp_ws
                break
    if not workspace_id:
        return None
    return await _fresh_workspace(workspace_id)


async def get_usage(workspace_id: str) -> Optional[StorageUsage]:
    """Return the current storage snapshot for a workspace id."""
    if not workspace_id:
        return None
    ws = await _fresh_workspace(workspace_id)
    if ws is None:
        return None
    return _snapshot(ws)


def _snapshot(ws: Workspace) -> StorageUsage:
    return StorageUsage(
        workspace_id=ws.id,
        bytes_used=int(ws.storage_bytes_used or 0),
        quota_bytes=int(ws.storage_quota_bytes or 0),
        enforcement_enabled=bool(settings.STORAGE_QUOTA_ENFORCE),
    )


async def check_quota_for_entry(
    entry: Entry, additional_bytes: int
) -> Optional[StorageUsage]:
    """Return the workspace snapshot when ``additional_bytes`` would exceed quota."""
    if additional_bytes <= 0:
        return None
    ws = await workspace_for_entry(entry)
    if ws is None:
        return None
    usage = _snapshot(ws)
    if not usage.enforcement_enabled:
        return None
    if not usage.would_exceed(additional_bytes):
        return None
    return usage


async def check_quota(
    workspace_id: str, additional_bytes: int
) -> Optional[StorageUsage]:
    """Workspace-id-keyed variant of ``check_quota_for_entry`` for callers
    (e.g. chat file upload) that don't hold an ``Entry``."""
    if additional_bytes <= 0 or not workspace_id:
        return None
    ws = await _fresh_workspace(workspace_id)
    if ws is None:
        return None
    usage = _snapshot(ws)
    if not usage.enforcement_enabled:
        return None
    if not usage.would_exceed(additional_bytes):
        return None
    return usage


async def increment(workspace_id: str, delta_bytes: int) -> None:
    """Workspace-id-keyed variant of ``increment_for_entry``."""
    if delta_bytes <= 0 or not workspace_id:
        return
    try:
        from jvspatial.core.context import get_default_context

        # PostgreSQL's native conditional update serializes counter changes
        # without saving a stale, fully hydrated Workspace over other writers.
        context = get_default_context()
        updated = await context.database.find_one_and_update(
            "node",
            {"id": workspace_id, "entity": Workspace._entity_name()},
            {"$inc": {"context.storage_bytes_used": int(delta_bytes)}},
        )
        if updated is not None:
            await context._evict_from_cache(workspace_id)
    except Exception as e:  # noqa: BLE001
        logger.warning(
            "workspace_storage_usage: increment failed for ws=%s by=%d: %s",
            workspace_id,
            delta_bytes,
            e,
        )


async def increment_for_entry(entry: Entry, delta_bytes: int) -> None:
    """Add ``delta_bytes`` to the entry's workspace usage counter."""
    if delta_bytes <= 0:
        return
    ws = await workspace_for_entry(entry)
    if ws is None:
        return
    await increment(ws.id, delta_bytes)


async def decrement_for_entry(entry: Entry, delta_bytes: int) -> None:
    """Floors at zero so transient miscounts can't drive the counter negative."""
    if delta_bytes <= 0:
        return
    ws = await workspace_for_entry(entry)
    if ws is None:
        return
    try:
        from jvspatial.core.context import get_default_context

        context = get_default_context()
        database = context.database
        identity = {"id": ws.id, "entity": Workspace._entity_name()}
        updated = await database.find_one_and_update(
            "node",
            {**identity, "context.storage_bytes_used": {"$gte": int(delta_bytes)}},
            {"$inc": {"context.storage_bytes_used": -int(delta_bytes)}},
        )
        if updated is None:
            updated = await database.find_one_and_update(
                "node",
                {**identity, "context.storage_bytes_used": {"$lt": int(delta_bytes)}},
                {"$set": {"context.storage_bytes_used": 0}},
            )
        if updated is not None:
            await context._evict_from_cache(ws.id)
    except Exception as e:  # noqa: BLE001
        logger.warning(
            "workspace_storage_usage: decrement failed for ws=%s by=%d: %s",
            getattr(ws, "id", "?"),
            delta_bytes,
            e,
        )


async def recalculate_for_workspace(workspace_id: str) -> StorageUsage:
    """Recount Entry and unfiled chat attachment bytes in this workspace."""
    from jvspatial.core.context import get_default_context

    from app.api.errors import ResourceConflictError

    ws = await _fresh_workspace(workspace_id)
    if ws is None:
        raise ValueError(f"Workspace {workspace_id} not found")
    previous = int(ws.storage_bytes_used or 0)

    total = 0
    tracks: List[Track] = await Track.find({"context.workspace_id": workspace_id})
    for track in tracks:
        entries = await track.nodes(edge=["CONTAINS"], direction="out")
        for entry in entries:
            attachments = await entry.nodes(edge=["HAS_ATTACHMENT"], direction="out")
            for att in attachments:
                if isinstance(att, Attachment):
                    if att.source_type != "file":
                        continue
                    total += int(att.size or 0)

    # A filed chat upload retains its thread edge as provenance. Count it on
    # the Entry path above only; count unfiled chat-owned blobs here. Fail the
    # recalculation if this read fails rather than saving a partial total.
    threads = await ChatThread.find({"context.workspace_id": workspace_id})
    for thread in threads:
        attachments = await thread.nodes(
            edge=["HAS_ATTACHMENT"], node=["Attachment"], direction="out"
        )
        for att in attachments:
            if (
                isinstance(att, Attachment)
                and att.owner_kind == "chat"
                and att.source_type == "file"
                and att.storage_key
                and att.scan_status not in {"pending", "quota_rejected"}
            ):
                total += int(att.size or 0)

    # Refuse to overwrite a counter change that landed while the graph was
    # being scanned. This does not yet make non-transactional upload steps
    # atomic with the scan; it prevents a known lost-update failure.
    context = get_default_context()
    counter_guard = (
        {
            "$or": [
                {"context.storage_bytes_used": 0},
                {"context.storage_bytes_used": {"$exists": False}},
            ]
        }
        if previous == 0
        else {"context.storage_bytes_used": previous}
    )
    row = await context.database.find_one_and_update(
        "node",
        {"id": workspace_id, "entity": Workspace._entity_name(), **counter_guard},
        {"$set": {"context.storage_bytes_used": total}},
    )
    if row is None:
        raise ResourceConflictError(
            message="Storage changed while recalculating. Try again."
        )
    await context._evict_from_cache(workspace_id)
    ws.storage_bytes_used = total
    return _snapshot(ws)
