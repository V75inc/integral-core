"""Attach track, parent app_node, and resolved tags to exported entry payloads."""

import logging
from typing import Any, Dict, List, Optional, Tuple

from app.models.edges import REFERENCES, Anchors
from app.models.nodes import Attachment, Entry, Tag, Track
from app.services.content_profile_runtime import resolve_track_runtime_profile


async def export_node(node) -> dict:
    """Serialize a jvspatial Node to a flat dict for inclusion in API payloads.

    Local copy of ``app.api.utils.export_node`` — duplicated to avoid a
    module-load ``from app.api.utils import export_node``, which triggers
    ``app.api`` package init and a circular import via ``api/entries`` →
    ``services/entry_context`` (this module).
    """
    return await node.export(flat=True)


logger = logging.getLogger(__name__)

# Maximum number of incoming REFERENCES backlinks surfaced per entry. Keeps
# the EntryDetail header bounded; pagination is intentionally out of scope
# for v1 — long backlink lists are rare in practice and the section can scroll.
BACKLINKS_REFERENCED_BY_LIMIT = 25


def _tag_id_from_item(item: Any) -> Optional[str]:
    if isinstance(item, str) and item.strip():
        return str(item)
    if isinstance(item, dict) and item.get("id"):
        return str(item["id"])
    return None


def _tag_payload_with_name(tag: Tag, data: Dict[str, Any]) -> Dict[str, Any]:
    """Ensure human-readable ``name`` (model + export can disagree)."""
    inst = (getattr(tag, "name", None) or "").strip()
    top = (str(data.get("name") or "")).strip()
    ctx = data.get("context")
    ctx_name = ""
    if isinstance(ctx, dict):
        ctx_name = str(ctx.get("name") or "").strip()
    label = inst or top or ctx_name
    if label:
        data["name"] = label
    return data


async def _entry_tag_id_list(entry: Entry, entry_data: Dict[str, Any]) -> List[str]:
    """Tag ids from the Entry field, else from ``Entry → Tag`` ``TAGGED_WITH`` edges."""
    raw: List[Any] = list(getattr(entry, "tags", None) or entry_data.get("tags") or [])
    out: List[str] = []
    seen: set[str] = set()
    for item in raw:
        tid = _tag_id_from_item(item)
        if tid and tid not in seen:
            seen.add(tid)
            out.append(tid)
    if out:
        return out
    try:
        tags = await entry.nodes(edge=["TAGGED_WITH"], direction="out", node=["Tag"])
        return [t.id for t in tags if getattr(t, "id", None)]
    except Exception:
        return []


async def _attachments_for_entry(
    entry: Entry,
    *,
    attachment_lookup: Optional[Dict[str, List[Attachment]]] = None,
) -> List[Attachment]:
    """Resolve ``Attachment`` nodes linked to ``entry`` via ``HAS_ATTACHMENT``.

    Mirrors ``list_entry_attachments`` in ``api/attachments.py``: walk the
    edge without a ``node=`` type filter (registry quirks can make that
    filter drop valid attachments), then keep ``Attachment`` instances only.
    Falls back to denormalized ``entry.attachment_ids`` when the walk is empty.
    """
    if attachment_lookup is not None:
        cached = attachment_lookup.get(entry.id)
        if cached is not None:
            return cached

    neighbours = await entry.nodes(edge=["HAS_ATTACHMENT"], direction="out")
    attachments = [a for a in neighbours if isinstance(a, Attachment)]
    if attachments:
        return attachments
    ids = [
        str(aid).strip()
        for aid in (getattr(entry, "attachment_ids", None) or [])
        if aid
    ]
    if not ids:
        return []
    resolved: List[Attachment] = []
    seen: set[str] = set()
    for aid in ids:
        if aid in seen:
            continue
        seen.add(aid)
        try:
            att = await Attachment.get(aid)
        except Exception:
            att = None
        if att is not None:
            resolved.append(att)
    return resolved


async def prefetch_attachments_for_entries(
    entries: List[Entry],
) -> Dict[str, List[Attachment]]:
    """Batch-load attachments for a page of entries via one edge query + find.

    Returns ``{entry_id: [Attachment, ...]}``.
    """
    if not entries:
        return {}
    entry_ids = [e.id for e in entries if getattr(e, "id", None)]
    if not entry_ids:
        return {}
    lookup: Dict[str, List[Attachment]] = {eid: [] for eid in entry_ids}
    try:
        ctx = await entries[0].get_context()
        edges = await ctx.database.find(
            "edge",
            {"source": {"$in": entry_ids}, "entity": "HAS_ATTACHMENT"},
        )
        targets_by_source: Dict[str, List[str]] = {}
        all_att_ids: List[str] = []
        for edge in edges:
            src = edge.get("source")
            tgt = edge.get("target")
            if not src or not tgt:
                continue
            targets_by_source.setdefault(src, []).append(tgt)
            all_att_ids.append(tgt)
        for entry in entries:
            for aid in getattr(entry, "attachment_ids", None) or []:
                s = str(aid).strip()
                if s:
                    targets_by_source.setdefault(entry.id, []).append(s)
                    all_att_ids.append(s)
        unique_ids = list(dict.fromkeys(all_att_ids))
        if unique_ids:
            att_nodes = await Attachment.find({"id": {"$in": unique_ids}})
            att_by_id = {a.id: a for a in att_nodes if getattr(a, "id", None)}
            for eid, tids in targets_by_source.items():
                seen: set[str] = set()
                for tid in tids:
                    if tid in seen:
                        continue
                    seen.add(tid)
                    att = att_by_id.get(tid)
                    if att is not None:
                        lookup.setdefault(eid, []).append(att)
    except Exception:
        pass
    return lookup


async def attach_entry_attachments(
    entry_data: Dict[str, Any],
    entry: Entry,
    *,
    attachment_lookup: Optional[Dict[str, List[Attachment]]] = None,
) -> Dict[str, Any]:
    """Set ``attachments`` to exported ``Attachment`` nodes linked via ``HAS_ATTACHMENT``."""
    from app.services.attachment_urls import (
        enrich_attachment_export,
        is_visible_to_user,
    )

    try:
        attachments = await _attachments_for_entry(
            entry, attachment_lookup=attachment_lookup
        )
        items: List[Dict[str, Any]] = []
        for a in attachments:
            if not is_visible_to_user(a):
                continue
            item = await export_node(a)
            await enrich_attachment_export(item, a)
            items.append(item)
        entry_data["attachments"] = items
    except Exception:
        entry_data.setdefault("attachments", [])
    return entry_data


async def prefetch_tags_for_entries(
    entries: List[Entry],
    entry_datas: Optional[List[Dict[str, Any]]] = None,
) -> Dict[str, Tag]:
    """Batch-load all Tag nodes referenced by a page of entries.

    Returns ``{tag_id: Tag}`` lookup for use with ``attach_entry_tags(tag_lookup=...)``.
    """
    all_ids: set[str] = set()
    datas = entry_datas or [{}] * len(entries)
    for entry, ed in zip(entries, datas):
        raw: list = list(getattr(entry, "tags", None) or ed.get("tags") or [])
        for item in raw:
            tid = _tag_id_from_item(item)
            if tid:
                all_ids.add(tid)
    if not all_ids:
        return {}
    from app.services.graph_hydration import batch_get_by_ids

    return await batch_get_by_ids(Tag, list(all_ids))


async def attach_entry_tags(
    entry_data: Dict[str, Any],
    entry: Entry,
    *,
    tag_lookup: Optional[Dict[str, Tag]] = None,
) -> Dict[str, Any]:
    """Replace ``tags`` id list with ``Tag`` exports including a correct ``name``."""
    tag_ids = await _entry_tag_id_list(entry, entry_data)
    if not tag_ids:
        entry_data["tags"] = []
        return entry_data
    resolved: List[Dict[str, Any]] = []
    seen: set[str] = set()
    for tid in tag_ids:
        if tid in seen:
            continue
        seen.add(tid)
        tag = tag_lookup.get(tid) if tag_lookup else None
        if tag is None:
            tag = await Tag.get(tid)
        if tag:
            resolved.append(_tag_payload_with_name(tag, await export_node(tag)))
    entry_data["tags"] = resolved
    return entry_data


async def attach_parent_app_to_track_data(
    track_data: Dict[str, Any],
    track: Track,
) -> Dict[str, Any]:
    """If an App ``CONTAINS`` this track, set ``app`` to that app's export."""
    try:
        apps = await track.nodes(
            edge=["CONTAINS"], direction="in", node=["WorkspaceApp"]
        )
        if apps:
            track_data["app"] = await export_node(apps[0])
    except Exception:
        pass
    return track_data


async def attach_anchor_source_to_track_data(
    track_data: Dict[str, Any],
    track: Track,
) -> Dict[str, Any]:
    """If an Entry ``ANCHORS`` this track, set ``anchor_source``.

    Anchor tracks are auto-provisioned by ``materialize_anchor_track`` to
    hold extended data for an Entry whose relation field has
    ``target: "track"``. Walking the inbound ``ANCHORS`` edge from the
    Track exposes the originating Entry so the frontend Tracks page can
    treat these as "entry extensions" rather than first-class tracks.

    Payload shape (always present so the frontend can treat it as a
    stable contract):

      ``anchor_source = {entry_id, entry_title, track_id, track_title, field_key}``
      or ``None`` when the Track has no inbound ANCHORS edge.

    Best-effort: traversal failures surface as ``None``.
    """
    track_data["anchor_source"] = None
    try:
        inbound = await track.nodes(edge=[Anchors], direction="in", node=["Entry"])
    except Exception:
        logger.exception("attach_anchor_source: ANCHORS inbound traversal failed")
        return track_data
    if not inbound:
        return track_data

    source = inbound[0]
    field_key = ""
    try:
        ctx = await source.get_context()
        edges = await ctx.find_edges_between(source.id, track.id, edge_class=Anchors)
        if edges:
            field_key = str(getattr(edges[0], "field_key", "") or "")
    except Exception:
        logger.exception("attach_anchor_source: ANCHORS edge field_key fetch failed")

    track_data["anchor_source"] = {
        "entry_id": getattr(source, "id", ""),
        "entry_title": getattr(source, "title", "") or "",
        "track_id": str(getattr(source, "track_id", "") or ""),
        "track_title": "",
        "field_key": field_key,
    }
    parent_track_id = str(getattr(source, "track_id", "") or "")
    if parent_track_id:
        try:
            parent_track = await Track.get(parent_track_id)
            if parent_track is not None:
                track_data["anchor_source"]["track_title"] = (
                    getattr(parent_track, "title", "") or ""
                )
        except Exception:
            logger.exception("attach_anchor_source: parent track title fetch failed")
    return track_data


async def attach_content_profile_defaults_to_track_data(
    track_data: Dict[str, Any],
    track: Track,
) -> Dict[str, Any]:
    """Set ``content_profile_defaults`` from the resolved runtime tier (manifest)."""
    try:
        _, tier, _ = await resolve_track_runtime_profile(track)
        raw = tier.get("defaults") if isinstance(tier, dict) else None
        defaults = raw if isinstance(raw, dict) else {}
        out: Dict[str, Any] = {}
        det = defaults.get("default_entry_type")
        if det is not None and str(det).strip():
            out["default_entry_type"] = str(det).strip()
        dv = defaults.get("default_view")
        if dv is not None and str(dv).strip():
            out["default_view"] = str(dv).strip()
        if out:
            track_data["content_profile_defaults"] = out
    except Exception:
        pass
    return track_data


async def prefetch_tracks_and_spaces_for_entries(
    entries: List[Entry],
) -> Tuple[Dict[str, Track], Dict[str, Optional[Dict[str, Any]]]]:
    """Batch-load tracks and parent space exports for a page of entries (list view).

    Returns:
        (track_by_id, space_export_by_track_id). Missing ``track_id`` on an entry
        is skipped; callers fall back to per-entry graph resolution.
    """
    track_ids: set[str] = set()
    for e in entries:
        tid = (getattr(e, "track_id", None) or "").strip()
        if tid:
            track_ids.add(tid)
    if not track_ids:
        return {}, {}
    tracks = await Track.find({"id": {"$in": list(track_ids)}})
    track_by_id = {t.id: t for t in tracks if getattr(t, "id", None)}
    space_export_by_track: Dict[str, Optional[Dict[str, Any]]] = {}
    # Single bulk edge pass for every track's parent app (vs one traversal
    # per track) — jvspatial Node.nodes_bulk.
    try:
        apps_by_track = await Track.nodes_bulk(
            list(track_by_id.keys()),
            direction="in",
            edge=["CONTAINS"],
            node=["WorkspaceApp"],
        )
    except Exception:
        apps_by_track = {}
    for tid in track_by_id:
        try:
            apps = apps_by_track.get(tid) or []
            space_export_by_track[tid] = await export_node(apps[0]) if apps else None
        except Exception:
            space_export_by_track[tid] = None
    return track_by_id, space_export_by_track


async def attach_track_and_space(
    entry_data: Dict[str, Any],
    entry: Entry,
    *,
    track: Optional[Track] = None,
    prefetched_app_export: Optional[Dict[str, Any]] = None,
    use_prefetched_track_app: bool = False,
    tag_lookup: Optional[Dict[str, Tag]] = None,
    attachment_lookup: Optional[Dict[str, List[Attachment]]] = None,
) -> Dict[str, Any]:
    """Mutates ``entry_data`` with ``track`` and optional parent ``app`` exports."""
    resolved: Optional[Track] = track
    use_prefetch = use_prefetched_track_app and track is not None

    if not use_prefetch and resolved is None:
        t_id = entry.track_id
        if t_id:
            resolved = await Track.get(t_id)
        if not resolved:
            tracks = await entry.nodes(
                edge=["CONTAINS"], direction="in", node=["Track"]
            )
            resolved = tracks[0] if tracks else None

    if resolved:
        entry_data["track"] = await export_node(resolved)
        if use_prefetch:
            if prefetched_app_export is not None:
                entry_data["app"] = prefetched_app_export
        else:
            try:
                apps = await resolved.nodes(
                    edge=["CONTAINS"], direction="in", node=["WorkspaceApp"]
                )
                if apps:
                    entry_data["app"] = await export_node(apps[0])
            except Exception:
                pass

    # Attach the entry-type slug so the frontend can drive view-scoped
    # filtering (TrackDetailPage's slice-view filter uses ``entry.type``
    # to project the correct rows for entry-type-keyed slice tabs).
    et_id = (getattr(entry, "type_id", "") or "").strip()
    if et_id and "type" not in entry_data:
        from app.api.entries import _slugify_entry_type_key
        from app.models.nodes import EntryType

        try:
            et = await EntryType.get(et_id)
            if et is not None:
                entry_data["type"] = _slugify_entry_type_key(
                    getattr(et, "name", "") or ""
                )
        except Exception:
            pass

    await attach_entry_tags(entry_data, entry, tag_lookup=tag_lookup)
    await attach_entry_attachments(
        entry_data, entry, attachment_lookup=attachment_lookup
    )
    return entry_data


# ---------------------------------------------------------------------------
# Backlinks — anchor source + incoming REFERENCES
# ---------------------------------------------------------------------------


def _short_entry_summary(
    entry_node: Entry, *, track_lookup: Dict[str, Track]
) -> Dict[str, Any]:
    """Minimal serialization for a backlink target (no payload heavy fields)."""
    tid = str(getattr(entry_node, "track_id", "") or "")
    track = track_lookup.get(tid)
    return {
        "id": entry_node.id,
        "title": getattr(entry_node, "title", "") or "",
        "track_id": tid,
        "track_title": (getattr(track, "title", "") if track else "") or "",
    }


async def attach_backlinks(
    entry_data: Dict[str, Any],
    entry: Entry,
    *,
    track: Optional[Track] = None,
) -> Dict[str, Any]:
    """Surface inbound graph context on ``entry_data``.

    Adds two keys (always present so the frontend can treat them as a stable
    contract):

      * ``anchor_source`` — the Entry whose ANCHORS edge points at this
        entry's parent Track. ``None`` when the parent Track is not an
        anchored track.
      * ``referenced_by`` — list of Entries with an outbound REFERENCES
        edge pointing at this Entry. Each item is a
        ``{id, title, track_id, track_title, field_key}`` summary. Capped
        at ``BACKLINKS_REFERENCED_BY_LIMIT`` to keep the response bounded.

    Best-effort: traversal failures are logged and surface as empty values
    (the section renders nothing rather than failing the entry fetch).
    """
    entry_data["anchor_source"] = None
    entry_data["referenced_by"] = []

    # --- anchor_source ---------------------------------------------------
    parent_track: Optional[Track] = track
    if parent_track is None:
        t_id = getattr(entry, "track_id", "")
        if t_id:
            try:
                parent_track = await Track.get(t_id)
            except Exception:
                logger.exception("attach_backlinks: parent track load failed")

    if parent_track is not None:
        try:
            inbound = await parent_track.nodes(
                edge=[Anchors], direction="in", node=["Entry"]
            )
        except Exception:
            inbound = []
            logger.exception("attach_backlinks: ANCHORS inbound traversal failed")
        if inbound:
            source = inbound[0]
            field_key = ""
            try:
                ctx = await source.get_context()
                edges = await ctx.find_edges_between(
                    source.id, parent_track.id, edge_class=Anchors
                )
                if edges:
                    field_key = str(getattr(edges[0], "field_key", "") or "")
            except Exception:
                logger.exception(
                    "attach_backlinks: ANCHORS edge field_key fetch failed"
                )
            src_track_id = str(getattr(source, "track_id", "") or "")
            src_track: Optional[Track] = None
            if src_track_id:
                try:
                    src_track = await Track.get(src_track_id)
                except Exception:
                    src_track = None
            payload = _short_entry_summary(
                source, track_lookup={src_track_id: src_track} if src_track else {}
            )
            payload["field_key"] = field_key
            entry_data["anchor_source"] = payload

    # --- referenced_by ---------------------------------------------------
    try:
        referrers = await entry.nodes(
            edge=["REFERENCES"], direction="in", node=["Entry"]
        )
    except Exception:
        referrers = []
        logger.exception("attach_backlinks: REFERENCES inbound traversal failed")

    if referrers:
        capped = referrers[:BACKLINKS_REFERENCED_BY_LIMIT]
        track_ids = {
            str(getattr(r, "track_id", "") or "")
            for r in capped
            if getattr(r, "track_id", "")
        }
        track_lookup: Dict[str, Track] = {}
        for tid in track_ids:
            try:
                t = await Track.get(tid)
            except Exception:
                t = None
            if t is not None:
                track_lookup[tid] = t
        out: List[Dict[str, Any]] = []
        try:
            ctx = await entry.get_context()
        except Exception:
            ctx = None
        for r in capped:
            field_key = ""
            if ctx is not None:
                try:
                    edges = await ctx.find_edges_between(
                        r.id, entry.id, edge_class=REFERENCES
                    )
                    if edges:
                        field_key = str(getattr(edges[0], "field_key", "") or "")
                except Exception:
                    field_key = ""
            payload = _short_entry_summary(r, track_lookup=track_lookup)
            payload["field_key"] = field_key
            out.append(payload)
        entry_data["referenced_by"] = out
        if len(referrers) > BACKLINKS_REFERENCED_BY_LIMIT:
            entry_data["referenced_by_total"] = len(referrers)

    return entry_data
