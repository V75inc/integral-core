"""Staged tag rename, tag merge, and track merge or split.

A preview names every entry that would change. A destination that cannot
store a source field refuses the whole card and moves nothing.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple

from app.api.validators_common import compute_fold
from app.models.edges import CONTAINS, TAGGED_WITH
from app.models.nodes import App, Entry, EntryType, OperationalModel, Tag, Track
from app.services.app_graph import get_track_attached_operational_model
from app.services.operational_model_graph import sync_attached_manifest


def _keys(track_model: Optional[OperationalModel]) -> set:
    manifest = (track_model.manifest or {}) if track_model else {}
    tier = (
        manifest.get("track") if isinstance(manifest.get("track"), dict) else manifest
    )
    keys = set()
    for entry_type in (tier or {}).get("entry_types") or []:
        if not isinstance(entry_type, dict):
            continue
        for field in entry_type.get("fields") or []:
            if isinstance(field, dict) and field.get("key"):
                keys.add(str(field["key"]))
    return keys


async def _model_keys(track: Track) -> set:
    model = await get_track_attached_operational_model(track)
    return _keys(model)


def _entry_field_keys(entry: Entry) -> set:
    keys = set((entry.custom_fields or {}).keys())
    keys.discard("_cp_index")
    return keys


async def tags_on_app(app_id: str) -> List[Tuple[Tag, Track]]:
    """Every tag on the app's tracks, with the track that owns it."""
    app = await App.get(app_id)
    if app is None:
        return []
    tracks = await app.nodes(edge=[CONTAINS], direction="out", node=["Track"], limit=40)
    found: List[Tuple[Tag, Track]] = []
    for track in tracks:
        if not isinstance(track, Track):
            continue
        model = await get_track_attached_operational_model(track)
        if model is None:
            continue
        tags = await model.nodes(edge=[CONTAINS], node=["Tag"], limit=50)
        for tag in tags:
            if isinstance(tag, Tag):
                found.append((tag, track))
    return found


async def find_tag(app_id: str, name: str) -> Optional[Tuple[Tag, Track]]:
    """The tag whose name matches, on this app."""
    wanted = compute_fold(name)
    for tag, track in await tags_on_app(app_id):
        if (tag.name_fold or compute_fold(tag.name)) == wanted:
            return tag, track
    return None


async def _tagged_entries(tag: Tag) -> List[Entry]:
    rows = await tag.nodes(
        edge=[TAGGED_WITH], direction="in", node=["Entry"], limit=100
    )
    return [row for row in rows if isinstance(row, Entry)]


async def _sync_tag(tag: Tag) -> None:
    model = None
    if tag.track_id:
        track = await Track.get(tag.track_id)
        if track is not None:
            model = await get_track_attached_operational_model(track)
    if model is not None:
        await sync_attached_manifest(model)


async def stage_tag_rename(
    *,
    user_id: str,
    session_id: Optional[str],
    app_id: str,
    current_name: str,
    new_name: str,
) -> str:
    """One card that renames a tag. Edges stay on the same tag."""
    from app.agentive.staging import create_staged_change

    found = await find_tag(app_id, current_name)
    if found is None:
        return ""
    tag, track = found
    entries = await _tagged_entries(tag)
    titles = [entry.title for entry in entries if entry.title]
    payload = {
        "app_id": app_id,
        "tag_id": tag.id,
        "name": new_name.strip(),
        "track_id": track.id,
        "entry_titles": titles,
    }
    await create_staged_change(
        user_id=user_id,
        session_id=session_id,
        kind="update_tag",
        summary=f"Rename tag {tag.name} to {new_name.strip()}",
        diff_human=(
            f"**Rename tag** {tag.name} → {new_name.strip()}\n\n"
            "Still tagged: " + (", ".join(titles) if titles else "no entries")
        ),
        diff_machine={"op": "update_tag", **payload},
        payload=payload,
    )
    return (
        f"Renaming the {tag.name} tag to {new_name.strip()} is waiting " "for approval."
    )


async def apply_update_tag(user_id: str, payload: Dict[str, Any]) -> Dict[str, Any]:
    """Rename or reparent the tag. The same tag id keeps its entries."""
    if not user_id:
        return {"error": True, "message": "Refused. No principal."}
    tag = await Tag.get(str(payload.get("tag_id") or ""))
    if tag is None:
        return {"error": True, "message": "Tag not found"}
    name = str(payload.get("name") or "").strip()
    if name:
        tag.name = name
        tag.name_fold = compute_fold(name)
    parent = payload.get("parent_tag_id")
    if parent:
        tag.parent_tag_id = str(parent)
    await tag.save()
    await _sync_tag(tag)
    return {"ok": True, "message": "Tag updated", "tag_id": tag.id, "name": tag.name}


async def preview_merge_tags(
    app_id: str, source_name: str, target_name: str
) -> Dict[str, Any]:
    """Entries that would move from the source tag onto the target tag."""
    source = await find_tag(app_id, source_name)
    target = await find_tag(app_id, target_name)
    if source is None or target is None:
        return {"error": True, "message": "Tag not found"}
    source_tag, _track = source
    target_tag, _other = target
    entries = await _tagged_entries(source_tag)
    return {
        "app_id": app_id,
        "source_tag_id": source_tag.id,
        "target_tag_id": target_tag.id,
        "entry_ids": [entry.id for entry in entries],
        "entry_titles": [entry.title for entry in entries if entry.title],
    }


async def apply_merge_tags(user_id: str, payload: Dict[str, Any]) -> Dict[str, Any]:
    """Retag every source entry onto the target, then remove the source tag."""
    if not user_id:
        return {"error": True, "message": "Refused. No principal."}
    source = await Tag.get(str(payload.get("source_tag_id") or ""))
    target = await Tag.get(str(payload.get("target_tag_id") or ""))
    if source is None or target is None:
        return {"error": True, "message": "Tag not found"}
    moved: List[str] = []
    for entry in await _tagged_entries(source):
        ctx = await entry.get_context()
        already = await ctx.find_edges_between(
            entry.id, target.id, edge_class=TAGGED_WITH
        )
        if not already:
            await entry.connect(target, edge=TAGGED_WITH)
        old = await ctx.find_edges_between(entry.id, source.id, edge_class=TAGGED_WITH)
        for edge in old:
            await edge.delete()
        tags = [item for item in (entry.tags or []) if item != source.id]
        if target.id not in tags:
            tags.append(target.id)
        entry.tags = tags
        await entry.save()
        moved.append(entry.id)
    await _sync_tag(source)
    await source.delete()
    await _sync_tag(target)
    return {
        "ok": True,
        "message": "Tags merged",
        "moved": moved,
        "removed_tag_id": payload.get("source_tag_id"),
    }


async def _track_by_title(app_id: str, title: str) -> Optional[Track]:
    app = await App.get(app_id)
    if app is None:
        return None
    wanted = title.strip().casefold()
    tracks = await app.nodes(edge=[CONTAINS], direction="out", node=["Track"], limit=40)
    for track in tracks:
        if isinstance(track, Track) and track.title.casefold() == wanted:
            return track
    return None


async def _entries(track: Track) -> List[Entry]:
    rows = await track.nodes(
        edge=[CONTAINS], direction="out", node=["Entry"], limit=100
    )
    return [row for row in rows if isinstance(row, Entry)]


async def _missing(entries: List[Entry], destination: Track) -> List[str]:
    dest_keys = await _model_keys(destination)
    missing = set()
    for entry in entries:
        missing.update(_entry_field_keys(entry) - dest_keys)
    return sorted(missing)


async def preview_tracks(
    app_id: str,
    source_title: str,
    destination_title: str,
    *,
    entry_type: str = "",
) -> Dict[str, Any]:
    """Field gap between two tracks. A gap is a refusal, not a partial move."""
    source = await _track_by_title(app_id, source_title)
    destination = await _track_by_title(app_id, destination_title)
    if source is None or destination is None:
        return {"error": True, "message": "Track not found"}
    entries = await _entries(source)
    if entry_type:
        kept = []
        for entry in entries:
            kind = await EntryType.get(entry.type_id) if entry.type_id else None
            label = (kind.name if kind else "").casefold().replace(" ", "_")
            if entry_type.casefold() in (label, (kind.name if kind else "").casefold()):
                kept.append(entry)
        entries = kept
    missing = await _missing(entries, destination)
    return {
        "app_id": app_id,
        "source_track_id": source.id,
        "destination_track_id": destination.id,
        "entry_ids": [entry.id for entry in entries],
        "entry_titles": [entry.title for entry in entries if entry.title],
        "missing_fields": missing,
        "entry_type": entry_type,
    }


async def apply_move_entries(user_id: str, payload: Dict[str, Any]) -> Dict[str, Any]:
    """Move the listed entries, or refuse when a field would be dropped."""
    missing = list(payload.get("missing_fields") or [])
    if missing:
        return {
            "error": True,
            "message": "Refused. The destination has no field for: "
            + ", ".join(str(item) for item in missing)
            + ".",
        }
    if not user_id:
        return {"error": True, "message": "Refused. No principal."}
    destination = await Track.get(str(payload.get("destination_track_id") or ""))
    if destination is None:
        return {"error": True, "message": "Destination track not found"}
    moved: List[str] = []
    for entry_id in payload.get("entry_ids") or []:
        entry = await Entry.get(str(entry_id))
        if entry is None:
            continue
        source = await Track.get(entry.track_id) if entry.track_id else None
        if source is not None and source.workspace_id != destination.workspace_id:
            return {
                "error": True,
                "message": "Refused. The destination is in another workspace.",
            }
        if source is not None:
            ctx = await entry.get_context()
            existing = await ctx.find_edges_between(
                source.id, entry.id, edge_class=CONTAINS
            )
            for edge in existing:
                await edge.delete()
        entry.track_id = destination.id
        await destination.connect(entry, edge=CONTAINS)
        await entry.save()
        moved.append(entry.id)
    return {"ok": True, "message": "Entries moved", "moved": moved}


async def stage_rate_dashboard(
    *,
    user_id: str,
    session_id: Optional[str],
    app_id: str,
) -> str:
    """One aggregate tile for the Vehicles daily rate."""
    from app.agentive.staging import create_staged_change
    from app.agentive.tooling.bindings import _stage_create_dashboard

    track = await _track_by_title(app_id, "Vehicles")
    if track is None:
        return ""
    widget = {
        "id": "daily_rate_total",
        "type": "metric_card",
        "title": "Daily rate total",
        "grid": {"x": 0, "y": 0, "w": 4, "h": 2},
        "config": {
            "rationale": ("Vehicles daily_rate is a number, so this tile is its sum.")
        },
        "data_source": {
            "kind": "aggregate",
            "op": "sum",
            "field": "daily_rate",
            "track_id": track.id,
        },
    }
    staged = await _stage_create_dashboard(
        {"app_id": app_id, "name": "Daily rates", "widgets": [widget]}
    )
    await create_staged_change(
        user_id=user_id,
        session_id=session_id,
        kind=staged["kind"],
        summary=staged["summary"],
        diff_human=staged["diff_human"],
        diff_machine=staged["diff_machine"],
        payload=staged["payload"],
    )
    return "The daily rate dashboard is waiting for approval."
