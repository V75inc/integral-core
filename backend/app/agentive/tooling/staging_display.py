"""Human-readable labels for agent staging diffs (approval cards).

Ported from the retired ``integral_tools.py`` prepare handlers so manifest
stagers in ``bindings.py`` can resolve entry/track titles instead of raw
node ids in ``summary`` / ``diff_human``.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple

_TITLE_LIKE_FIELD_KEYS = ("name", "title", "subject", "label")


def truncate(value: Any, limit: int = 80) -> str:
    """Coerce *value* to string and ellipsize beyond *limit* characters."""
    text = "" if value is None else str(value)
    return text if len(text) <= limit else text[: limit - 1] + "…"


def derive_title(text: str, *, limit: int = 80) -> str:
    """Use the first non-empty line of *text* as a display title."""
    for line in text.splitlines():
        candidate = line.strip()
        if candidate:
            return (
                candidate if len(candidate) <= limit else candidate[: limit - 1] + "…"
            )
    return "Untitled note"


def short_node_id(node_id: str) -> str:
    """Abbreviate a graph node id for inline display."""
    s = (node_id or "").strip()
    if not s:
        return "?"
    if len(s) <= 12:
        return s
    return f"…{s[-8:]}"


def unwrap_handler_record(resp: Any, *keys: str) -> Optional[Dict[str, Any]]:
    """Return inner record from ``{entry|track|app: ...}`` handlers; None on errors."""
    if not isinstance(resp, dict) or resp.get("error"):
        return None
    for key in keys:
        inner = resp.get(key)
        if isinstance(inner, dict):
            return inner
    if any(k in resp for k in ("title", "name", "id", "track_id", "body")):
        return resp
    return None


def entry_display_label(entry: Dict[str, Any], fallback_id: str) -> str:
    """Resolve a human-readable entry label from exported entry data."""
    title = (entry.get("title") or "").strip()
    if title:
        return title
    body = entry.get("body")
    if isinstance(body, str) and body.strip():
        return derive_title(body)
    cf = entry.get("custom_fields") or entry.get("fields") or {}
    if isinstance(cf, dict):
        for key in _TITLE_LIKE_FIELD_KEYS:
            val = cf.get(key)
            if isinstance(val, str) and val.strip():
                return val.strip()
    return f"Entry {short_node_id(fallback_id)}"


def track_display_label(track: Dict[str, Any], fallback_id: str) -> str:
    """Resolve a human-readable track label from exported track data."""
    title = (track.get("title") or "").strip()
    if title:
        return title
    return f"Track {short_node_id(fallback_id)}"


def app_display_label(app: Dict[str, Any], fallback_id: str) -> str:
    """Resolve a human-readable app label from exported app data."""
    name = (app.get("name") or app.get("title") or "").strip()
    if name:
        return name
    return f"App {short_node_id(fallback_id)}"


async def resolve_track_label(track_id: str) -> str:
    """Load a track and return its user-facing title (or a short fallback)."""
    current = await load_track_record(track_id)
    if current:
        return track_display_label(current, track_id)
    return f"Track {short_node_id(track_id)}"


async def resolve_app_label(app_id: str) -> str:
    """Load an app and return its user-facing name (or a short fallback)."""
    current = await load_app_record(app_id)
    if current:
        return app_display_label(current, app_id)
    return f"App {short_node_id(app_id)}"


async def resolve_container_label(
    *,
    track_id: Optional[str] = None,
    app_id: Optional[str] = None,
) -> str:
    """Resolve a track or app id to a user-facing container label."""
    if track_id:
        return await resolve_track_label(track_id)
    if app_id:
        return await resolve_app_label(app_id)
    return "?"


def _collect_node_ids_in_value(value: Any) -> Tuple[List[str], List[str], List[str]]:
    tag_ids: List[str] = []
    entry_ids: List[str] = []
    track_ids: List[str] = []
    seen_t: set[str] = set()
    seen_e: set[str] = set()
    seen_tr: set[str] = set()

    def visit(item: Any) -> None:
        if isinstance(item, str):
            s = item.strip()
            if s.startswith("n.Tag.") and s not in seen_t:
                seen_t.add(s)
                tag_ids.append(s)
            elif s.startswith("n.Entry.") and s not in seen_e:
                seen_e.add(s)
                entry_ids.append(s)
            elif s.startswith("n.Track.") and s not in seen_tr:
                seen_tr.add(s)
                track_ids.append(s)
        elif isinstance(item, dict):
            tid = item.get("id")
            if isinstance(tid, str):
                visit(tid)
            name = item.get("name") or item.get("title") or ""
            if isinstance(name, str) and name.strip():
                return
            for v in item.values():
                visit(v)
        elif isinstance(item, list):
            for sub in item:
                visit(sub)

    visit(value)
    return tag_ids, entry_ids, track_ids


def _tag_name_from_export(tag_data: Dict[str, Any]) -> str:
    name = (tag_data.get("name") or "").strip()
    if name:
        return name
    ctx = tag_data.get("context")
    if isinstance(ctx, dict):
        ctx_name = str(ctx.get("name") or "").strip()
        if ctx_name:
            return ctx_name
    return ""


async def resolve_node_labels_for_diff(
    fields: Dict[str, Any],
) -> Tuple[Dict[str, str], Dict[str, str], Dict[str, str]]:
    """Batch-resolve tag, entry, and track ids referenced in a fields patch."""
    all_tag_ids: List[str] = []
    all_entry_ids: List[str] = []
    all_track_ids: List[str] = []
    for value in fields.values():
        tids, eids, trids = _collect_node_ids_in_value(value)
        all_tag_ids.extend(tids)
        all_entry_ids.extend(eids)
        all_track_ids.extend(trids)
    tag_names: Dict[str, str] = {}
    entry_names: Dict[str, str] = {}
    track_names: Dict[str, str] = {}
    if not all_tag_ids and not all_entry_ids and not all_track_ids:
        return tag_names, entry_names, track_names
    try:
        from app.models.nodes import Entry, Tag
    except ImportError:
        return tag_names, entry_names, track_names

    unique_tags = list(dict.fromkeys(all_tag_ids))
    if unique_tags:
        try:
            from app.api.utils import export_node

            tags = await Tag.find({"id": {"$in": unique_tags}})
            for tag in tags:
                tid = getattr(tag, "id", None)
                if not tid:
                    continue
                exported = await export_node(tag)
                label = (
                    _tag_name_from_export(exported)
                    or (getattr(tag, "name", None) or "").strip()
                )
                if label:
                    tag_names[tid] = label
        except Exception:  # noqa: BLE001
            pass

    unique_entries = list(dict.fromkeys(all_entry_ids))
    if unique_entries:
        try:
            entries = await Entry.find({"id": {"$in": unique_entries}})
            for entry in entries:
                eid = getattr(entry, "id", None)
                if not eid:
                    continue
                title = (getattr(entry, "title", None) or "").strip()
                if title:
                    entry_names[eid] = title
        except Exception:  # noqa: BLE001
            pass

    unique_tracks = list(dict.fromkeys(all_track_ids))
    if unique_tracks:
        try:
            from app.models.nodes import Track

            tracks = await Track.find({"id": {"$in": unique_tracks}})
            for track in tracks:
                tid = getattr(track, "id", None)
                if not tid:
                    continue
                title = (getattr(track, "title", None) or "").strip()
                if title:
                    track_names[tid] = title
        except Exception:  # noqa: BLE001
            pass

    return tag_names, entry_names, track_names


def format_scalar_for_diff(
    value: Any,
    *,
    tag_names: Dict[str, str],
    entry_names: Dict[str, str],
    track_names: Optional[Dict[str, str]] = None,
) -> str:
    """Render a field value for diff display, resolving tag/entry/track ids to names."""
    track_names = track_names or {}

    def fmt_one(item: Any) -> str:
        if isinstance(item, dict):
            name = (item.get("name") or item.get("title") or "").strip()
            if name:
                return name
            iid = item.get("id")
            if isinstance(iid, str):
                item = iid
            else:
                return truncate(item, 40)

        if not isinstance(item, str):
            return truncate(item, 40)

        s = item.strip()
        if s in tag_names:
            return f"{tag_names[s]} *({truncate(s, 24)})*"
        if s in entry_names:
            return f"{entry_names[s]} *({truncate(s, 24)})*"
        if s in track_names:
            return track_names[s]
        if (
            s.startswith("n.Tag.")
            or s.startswith("n.Entry.")
            or s.startswith("n.Track.")
        ):
            return truncate(s, 40)
        return truncate(s, 40)

    if isinstance(value, list):
        parts = [fmt_one(v) for v in value]
        return ", ".join(parts) if parts else "—"
    return fmt_one(value)


async def format_fields_patch_for_diff(fields: Dict[str, Any]) -> str:
    """Format a fields patch as ``key = value`` pairs for approval-card diffs."""
    tag_names, entry_names, track_names = await resolve_node_labels_for_diff(fields)
    parts = [
        f"`{k}` = {format_scalar_for_diff(v, tag_names=tag_names, entry_names=entry_names, track_names=track_names)}"
        for k, v in fields.items()
    ]
    return ", ".join(parts)


async def load_entry_record(entry_id: str) -> Optional[Dict[str, Any]]:
    """Best-effort entry export for staging labels (no permission gate)."""
    try:
        from app.api.utils import export_node
        from app.models.nodes import Entry

        entry = await Entry.get(entry_id)
        if not entry:
            return None
        return await export_node(entry)
    except Exception:  # noqa: BLE001
        return None


async def load_track_record(track_id: str) -> Optional[Dict[str, Any]]:
    """Best-effort track export for staging labels (no permission gate)."""
    try:
        from app.api.utils import export_node
        from app.models.nodes import Track

        track = await Track.get(track_id)
        if not track:
            return None
        return await export_node(track)
    except Exception:  # noqa: BLE001
        return None


async def load_app_record(app_id: str) -> Optional[Dict[str, Any]]:
    """Best-effort app export for staging labels (no permission gate)."""
    try:
        from app.api.utils import export_node
        from app.models.nodes import App

        app = await App.get(app_id)
        if not app:
            return None
        return await export_node(app)
    except Exception:  # noqa: BLE001
        return None


async def format_scalar_change_line(
    key: str,
    new_val: Any,
    old_val: Any,
    *,
    tag_names: Optional[Dict[str, str]] = None,
    entry_names: Optional[Dict[str, str]] = None,
    track_names: Optional[Dict[str, str]] = None,
) -> str:
    """Build a markdown bullet for one scalar field change (old → new when present)."""
    tag_names = tag_names or {}
    entry_names = entry_names or {}
    track_names = track_names or {}
    new_rendered = format_scalar_for_diff(
        new_val,
        tag_names=tag_names,
        entry_names=entry_names,
        track_names=track_names,
    )
    if old_val is not None and old_val != new_val:
        old_rendered = format_scalar_for_diff(
            old_val,
            tag_names=tag_names,
            entry_names=entry_names,
            track_names=track_names,
        )
        return f"- **{key}:** `{truncate(old_rendered)}` → `{truncate(new_rendered)}`"
    return f"- **{key}:** `{truncate(new_rendered)}`"
