"""Previewed, same-scope tag merge with revision-bound atomic apply."""

from __future__ import annotations

import hashlib
import json
from typing import Any, Dict, List

from app.models.edges import TAGGED_WITH
from app.models.nodes import Entry, Tag, Track
from app.schemas.policy import Resource, Subject
from app.services.app_operations.transaction_scope import (
    OperationTransactionUnavailable,
    graph_transaction_available,
    postgres_graph_transaction,
)
from app.services.policy_engine import evaluate as policy_evaluate
from app.utils.time import utc_now_iso

_MAX_ENTRIES = 500


def _fingerprint(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":"), default=str).encode()
    ).hexdigest()


async def _tag_entries(tag: Tag) -> List[Entry]:
    rows, _ = await tag.nodes_page(
        edge=[TAGGED_WITH], direction="in", node=["Entry"], limit=_MAX_ENTRIES + 1
    )
    unique = {str(row.id): row for row in rows}
    return [unique[key] for key in sorted(unique)]


async def prepare_tag_merge(
    *, user_id: str, source_tag_id: str, target_tag_id: str, workspace_id: str = ""
) -> Dict[str, Any]:
    """Validate compatible tags and return a bounded, revision-bound preview."""
    source = await Tag.get(source_tag_id)
    target = await Tag.get(target_tag_id)
    if source is None or target is None:
        return {"error": "not_found", "detail": "Source or target Tag not found"}
    source_scope = (str(source.track_id or ""), str(source.app_id or ""))
    target_scope = (str(target.track_id or ""), str(target.app_id or ""))
    if source_scope != target_scope or source_tag_id == target_tag_id:
        return {
            "error": "incompatible_tags",
            "detail": "Tags must be distinct and belong to the same Track or App",
        }
    if (
        (source.group_key or "") != (target.group_key or "")
        or (source.parent_tag_id or "") != (target.parent_tag_id or "")
        or sorted(source.applies_to_entry_types or [])
        != sorted(target.applies_to_entry_types or [])
    ):
        return {
            "error": "incompatible_taxonomy",
            "detail": "Tags must share a group, parent, and EntryType applicability",
        }
    children = await Tag.find({"context.parent_tag_id": source_tag_id})
    if children:
        return {
            "error": "tag_has_children",
            "detail": "Reparent child tags before merging this tag",
        }
    if source.track_id:
        track = await Track.get(source.track_id)
        if track is None or (workspace_id and track.workspace_id != workspace_id):
            return {
                "error": "workspace_mismatch",
                "detail": "Tag is outside the active Workspace",
            }
        scope = f"track:{track.id}"
    else:
        from app.models.nodes import App

        app = await App.get(source.app_id)
        if app is None or (workspace_id and app.workspace_id != workspace_id):
            return {
                "error": "workspace_mismatch",
                "detail": "Tag is outside the active Workspace",
            }
        scope = f"app:{app.id}"
    for tag in (source, target):
        owner_kind = "track" if tag.track_id else "app"
        owner_id = str(tag.track_id or tag.app_id)
        decision = await policy_evaluate(
            subject=Subject(kind="human", id=user_id),
            action=f"{owner_kind}.update",
            resource=Resource(kind=owner_kind, id=owner_id, scope=scope),
        )
        if not decision.allowed:
            return {"error": "forbidden", "detail": f"Tag {tag.id} is not editable"}
    entries = await _tag_entries(source)
    if len(entries) > _MAX_ENTRIES:
        return {
            "error": "too_many_entries",
            "detail": f"Tag merge is limited to {_MAX_ENTRIES} entries",
        }
    revisions: Dict[str, int] = {}
    for entry in entries:
        if source_tag_id not in (getattr(entry, "tags", None) or []):
            return {
                "error": "tag_membership_inconsistent",
                "detail": f"Entry {entry.id} has the source TAGGED_WITH edge but not the source tag id",
            }
        decision = await policy_evaluate(
            subject=Subject(kind="human", id=user_id),
            action="entry.update",
            resource=Resource(
                kind="entry", id=entry.id, scope=f"track:{entry.track_id}"
            ),
        )
        if not decision.allowed:
            return {"error": "forbidden", "detail": f"Entry {entry.id} is not editable"}
        revisions[entry.id] = int(getattr(entry, "record_revision", 0) or 0)
    snapshot = {
        "source_tag_id": source.id,
        "target_tag_id": target.id,
        "source_revision": int(getattr(source, "record_revision", 0) or 0),
        "target_revision": int(getattr(target, "record_revision", 0) or 0),
        "source_taxonomy": [
            source.group_key,
            source.parent_tag_id,
            source.applies_to_entry_types,
        ],
        "target_taxonomy": [
            target.group_key,
            target.parent_tag_id,
            target.applies_to_entry_types,
        ],
        "entry_revisions": revisions,
    }
    return {
        "source_tag_id": source.id,
        "target_tag_id": target.id,
        "affected_count": len(entries),
        "entry_ids": sorted(revisions),
        "preview_fingerprint": _fingerprint(snapshot),
        "snapshot": snapshot,
    }


async def merge_tags(
    *, user_id: str, payload: Dict[str, Any], workspace_id: str = ""
) -> Dict[str, Any]:
    """Revalidate a staged preview, retag entries, and remove the source."""
    if not graph_transaction_available():
        return {
            "error": True,
            "error_code": "transaction_unavailable",
            "message": "Tag merges require graph transaction support",
        }
    prepared = await prepare_tag_merge(
        user_id=user_id,
        source_tag_id=str(payload.get("source_tag_id") or ""),
        target_tag_id=str(payload.get("target_tag_id") or ""),
        workspace_id=workspace_id,
    )
    if prepared.get("error"):
        return {
            "error": True,
            "error_code": prepared["error"],
            "message": prepared["detail"],
        }
    if prepared["preview_fingerprint"] != payload.get("preview_fingerprint"):
        return {
            "error": True,
            "error_code": "stale_preview",
            "message": "Tag membership changed after preview; stage a fresh merge",
        }
    source_id, target_id = prepared["source_tag_id"], prepared["target_tag_id"]

    async def apply() -> None:
        source, target = await Tag.get(source_id), await Tag.get(target_id)
        if source is None or target is None:
            raise RuntimeError("A Tag disappeared during merge")
        for entry_id, revision in prepared["snapshot"]["entry_revisions"].items():
            entry = await Entry.get(entry_id)
            if (
                entry is None
                or int(getattr(entry, "record_revision", 0) or 0) != revision
            ):
                raise RuntimeError(f"Entry {entry_id} changed during merge")
            tags = list(getattr(entry, "tags", None) or [])
            if source_id not in tags:
                raise RuntimeError(f"Entry {entry_id} no longer has the source tag")
            updated_tags = [tag_id for tag_id in tags if tag_id != source_id]
            if target_id not in updated_tags:
                updated_tags.append(target_id)
            entry.tags = updated_tags
            entry.record_revision = revision + 1
            entry.updated_at = utc_now_iso()
            await entry.save()
            ctx = await entry.get_context()
            source_edges = await ctx.find_edges_between(
                entry.id, source_id, edge_class=TAGGED_WITH
            )
            for edge in source_edges:
                await edge.delete()
            target_edges = await ctx.find_edges_between(
                entry.id, target_id, edge_class=TAGGED_WITH
            )
            if not target_edges:
                await entry.connect(
                    target, edge=TAGGED_WITH, tagged_at=utc_now_iso(), tagged_by=user_id
                )
        await source.delete()

    source_tag = await Tag.get(source_id)
    scope = (
        f"track:{source_tag.track_id}"
        if source_tag and source_tag.track_id
        else f"app:{source_tag.app_id if source_tag else ''}"
    )
    warnings: List[str] = []
    try:
        async with postgres_graph_transaction():
            await apply()
    except OperationTransactionUnavailable:
        return {
            "error": True,
            "error_code": "transaction_unavailable",
            "message": "Tag merges require graph transaction support",
        }
    except Exception as exc:  # noqa: BLE001
        return {
            "error": True,
            "error_code": "tag_merge_rolled_back",
            "message": str(exc) or "Tag merge rolled back",
        }
    try:
        from app.models.nodes import App
        from app.services.app_graph import (
            get_app_attached_operational_model,
            get_track_attached_operational_model,
        )
        from app.services.operational_model_runtime import sync_attached_manifest

        sync_cp = None
        if source_tag and source_tag.track_id:
            owner = await Track.get(source_tag.track_id)
            if owner:
                sync_cp = await get_track_attached_operational_model(owner)
        elif source_tag and source_tag.app_id:
            owner = await App.get(source_tag.app_id)
            if owner:
                sync_cp = await get_app_attached_operational_model(owner)
        if sync_cp:
            await sync_attached_manifest(sync_cp)
    except Exception as exc:  # noqa: BLE001
        warnings.append(f"manifest sync: {exc}")
    try:
        from app.services.change_event import emit_change_event

        await emit_change_event(
            actor_kind="human",
            actor_id=user_id,
            action="tag.merge",
            resource_type="Tag",
            resource_id=source_id,
            before={"tag_id": source_id},
            after=None,
            scope=scope,
            details={
                "target_tag_id": target_id,
                "moved_entries": prepared["affected_count"],
            },
        )
    except Exception as exc:  # noqa: BLE001
        warnings.append(f"audit event: {exc}")
    result = {
        "merged": True,
        "source_tag_id": source_id,
        "target_tag_id": target_id,
        "moved_entries": prepared["affected_count"],
    }
    if warnings:
        result["warnings"] = warnings
    return result
