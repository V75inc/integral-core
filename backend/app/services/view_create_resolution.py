"""Resolve entry-type and field defaults when creating from a saved view.

Mirrors frontend precedence in ``calendarUtils`` and ``kanbanColumnUtils`` so
agent-proposed creates appear in the view the user named (calendar date field,
kanban column constraints, entry_type_keys slices).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

from app.models.nodes import EntryType, Track, View
from app.services.app_graph import get_track_attached_content_profile
from app.services.content_profile_runtime import resolve_track_runtime_profile
from app.utils.text_matching import casefold_match

READONLY_DATE_FIELDS = frozenset({"created_at", "updated_at"})
KANBAN_STAGE_KEY = "_kanban_stage"
DEFAULT_KANBAN_GROUP_BY = f"custom_fields.{KANBAN_STAGE_KEY}"

_MONTH_NAMES = {
    "january": 1,
    "jan": 1,
    "february": 2,
    "feb": 2,
    "march": 3,
    "mar": 3,
    "april": 4,
    "apr": 4,
    "may": 5,
    "june": 6,
    "jun": 6,
    "july": 7,
    "jul": 7,
    "august": 8,
    "aug": 8,
    "september": 9,
    "sep": 9,
    "sept": 9,
    "october": 10,
    "oct": 10,
    "november": 11,
    "nov": 11,
    "december": 12,
    "dec": 12,
}


def _slugify_key(value: str) -> str:
    s = str(value or "").strip().lower()
    s = re.sub(r"[^a-z0-9]+", "_", s).strip("_")
    return s


def _entry_type_matches_slug(name: str, slug: str) -> bool:
    name_slug = _slugify_key(name)
    want = _slugify_key(slug)
    if not name_slug or not want:
        return False
    if name_slug == want:
        return True
    return name_slug.replace("_", "") == want.replace("_", "")


def _normalize_field_key(raw: Any) -> Optional[str]:
    if not isinstance(raw, str):
        return None
    trimmed = raw.strip()
    if not trimmed:
        return None
    if trimmed.startswith("custom_fields."):
        return trimmed[len("custom_fields.") :]
    return trimmed


def normalize_calendar_mapping(config: Dict[str, Any]) -> Dict[str, Optional[str]]:
    """Normalize a calendar view's date/field mapping to canonical keys."""
    cm = config.get("calendar_mapping") if isinstance(config, dict) else None
    if not isinstance(cm, dict):
        cm = config if isinstance(config, dict) else {}
    return {
        "date_field": _normalize_field_key(cm.get("date_field") or cm.get("dateField"))
        or "created_at",
        "end_date_field": _normalize_field_key(
            cm.get("end_date_field") or cm.get("endDateField")
        ),
    }


def _entry_type_field_keys(entry_type: EntryType) -> List[str]:
    schema = entry_type.form_schema if isinstance(entry_type.form_schema, dict) else {}
    fields = schema.get("fields", [])
    if not isinstance(fields, list):
        return []
    out: List[str] = []
    for f in fields:
        if isinstance(f, dict) and f.get("key"):
            out.append(str(f["key"]))
    return out


def _entry_types_with_field(
    entry_types: List[EntryType], field_key: str
) -> List[EntryType]:
    return [et for et in entry_types if field_key in _entry_type_field_keys(et)]


def _view_entry_type_constraints(view: View) -> Tuple[str, List[str]]:
    default_key = _slugify_key(getattr(view, "default_entry_type_key", "") or "")
    from_node = [
        _slugify_key(str(k))
        for k in (getattr(view, "entry_type_keys", None) or [])
        if _slugify_key(str(k))
    ]
    cfg = view.config if isinstance(view.config, dict) else {}
    from_cfg = [
        _slugify_key(str(k))
        for k in (
            cfg.get("entry_type_keys")
            if isinstance(cfg.get("entry_type_keys"), list)
            else []
        )
        if _slugify_key(str(k))
    ]
    type_keys = from_node if from_node else from_cfg
    return default_key, type_keys


def resolve_calendar_create_entry_type_key(
    view: View,
    entry_types: List[EntryType],
    date_field_key: str,
    track_default_key: str = "",
) -> Optional[str]:
    """Pick the entry-type key a calendar view should create entries as."""
    candidates = _entry_types_with_field(entry_types, date_field_key)
    if not candidates:
        return None

    default_key, type_keys = _view_entry_type_constraints(view)
    track_default = _slugify_key(track_default_key)
    prefer_slugs = [k for k in [default_key, *type_keys, track_default] if k]

    for slug in prefer_slugs:
        for et in candidates:
            if _entry_type_matches_slug(getattr(et, "name", "") or "", slug):
                return _slugify_key(getattr(et, "name", "") or "")

    return _slugify_key(getattr(candidates[0], "name", "") or "")


def resolve_view_create_entry_type_key(view: View) -> Optional[str]:
    """Resolve the entry-type key a view should create new entries as."""
    default_key = _slugify_key(getattr(view, "default_entry_type_key", "") or "")
    if default_key:
        return default_key
    keys = getattr(view, "entry_type_keys", None) or []
    if keys:
        return _slugify_key(str(keys[0]))
    return None


def _resolve_kanban_group_by(raw: Any) -> str:
    g = str(raw or "").strip()
    if not g or g == "status":
        return DEFAULT_KANBAN_GROUP_BY
    return g


def _resolve_kanban_group_field_key(group_by: str) -> str:
    g = str(group_by or "").strip()
    if g.startswith("custom_fields."):
        return g[len("custom_fields.") :].split(".")[-1] or g
    return g.split(".")[-1] or g


async def _track_default_entry_type_key(track: Track) -> str:
    try:
        _, tier, _ = await resolve_track_runtime_profile(track)
        if isinstance(tier, dict):
            defaults = tier.get("defaults")
            if isinstance(defaults, dict):
                det = defaults.get("default_entry_type")
                if det is not None and str(det).strip():
                    return _slugify_key(str(det))
    except Exception:
        pass
    return ""


async def _list_views_for_track(track: Track) -> List[View]:
    from app.api.views import _list_track_views

    return await _list_track_views(track)


async def resolve_view_for_filing(
    track: Track,
    *,
    view_id: Optional[str] = None,
    view_hint: Optional[str] = None,
    focused_view_id: Optional[str] = None,
    min_score: float = 0.5,
) -> Optional[View]:
    """Resolve a view by explicit id, fuzzy name hint, or UI focus."""
    views = await _list_views_for_track(track)
    if not views:
        return None

    if view_id:
        for v in views:
            if v.id == view_id:
                return v
        standalone = await View.get(view_id)
        if standalone and getattr(standalone, "track_id", "") == track.id:
            return standalone
        return None

    if view_hint and view_hint.strip():
        hint = view_hint.strip()
        candidates: List[Tuple[View, float]] = []
        for v in views:
            name = getattr(v, "name", "") or ""
            name_fold = getattr(v, "name_fold", "") or name.casefold()
            score = casefold_match(hint, name_fold)
            cfg = v.config if isinstance(v.config, dict) else {}
            manifest_key = str(cfg.get("_manifest_view_key") or "").casefold()
            view_type = str(getattr(v, "type", "") or "").casefold()
            hint_fold = hint.casefold()
            if manifest_key and (
                hint_fold in manifest_key or manifest_key in hint_fold
            ):
                score = max(score, 0.75)
            if view_type and hint_fold in view_type:
                score = max(score, 0.65)
            if "calendar" in hint_fold and view_type == "calendar":
                score = max(score, 0.85)
            if "board" in hint_fold and view_type == "kanban":
                score = max(score, 0.8)
            if "kanban" in hint_fold and view_type == "kanban":
                score = max(score, 0.85)
            if v.id == focused_view_id:
                score = min(score + 0.15, 1.0)
            if score >= min_score:
                candidates.append((v, score))
        if candidates:
            candidates.sort(key=lambda x: x[1], reverse=True)
            return candidates[0][0]

    if focused_view_id:
        for v in views:
            if v.id == focused_view_id:
                return v

    return None


def _format_iso_date(year: int, month: int, day: int) -> Optional[str]:
    try:
        return datetime(year, month, day).strftime("%Y-%m-%d")
    except ValueError:
        return None


def extract_date_iso(
    text: Optional[str],
    fields: Optional[Dict[str, Any]],
    *,
    reference_year: Optional[int] = None,
) -> Optional[str]:
    """Best-effort ISO date (yyyy-mm-dd) from fields or natural-language text."""
    ref_year = reference_year or datetime.now().year

    for value in (fields or {}).values():
        if value is None or value == "":
            continue
        s = str(value).strip()
        if re.match(r"^\d{4}-\d{2}-\d{2}", s):
            return s[:10]

    if not text or not str(text).strip():
        return None

    blob = str(text)
    iso = re.search(r"\b(\d{4}-\d{2}-\d{2})\b", blob)
    if iso:
        return iso.group(1)

    # "19 June 2026" / "June 19, 2026"
    for pattern in (
        r"\b(\d{1,2})\s+([A-Za-z]+)(?:\s+(\d{4}))?\b",
        r"\b([A-Za-z]+)\s+(\d{1,2})(?:,?\s+(\d{4}))?\b",
    ):
        for match in re.finditer(pattern, blob, re.IGNORECASE):
            if match.lastindex and match.lastindex >= 2:
                if pattern.startswith(r"\b(\d"):
                    day_s, month_s, year_s = (
                        match.group(1),
                        match.group(2),
                        match.group(3),
                    )
                else:
                    month_s, day_s, year_s = (
                        match.group(1),
                        match.group(2),
                        match.group(3),
                    )
                month = _MONTH_NAMES.get(month_s.casefold())
                if not month:
                    continue
                try:
                    day = int(day_s)
                except ValueError:
                    continue
                year = int(year_s) if year_s else ref_year
                parsed = _format_iso_date(year, month, day)
                if parsed:
                    return parsed

    return None


@dataclass
class ViewCreateResolution:
    entry_type: Optional[str] = None
    fields: Dict[str, Any] = field(default_factory=dict)
    view_name: str = ""
    view_type: str = ""
    warnings: List[str] = field(default_factory=list)


async def resolve_create_params_for_view(
    track: Track,
    view: View,
    entry_types: List[EntryType],
    *,
    agent_entry_type: Optional[str] = None,
    agent_fields: Optional[Dict[str, Any]] = None,
    text: Optional[str] = None,
    kanban_stage_hint: Optional[str] = None,
) -> ViewCreateResolution:
    """Fill gaps in agent-supplied create params from view configuration.

    Agent-supplied ``entry_type`` and ``fields`` win on conflict; this only
    seeds missing values.
    """
    out_fields: Dict[str, Any] = dict(agent_fields or {})
    warnings: List[str] = []
    resolved_type: Optional[str] = (agent_entry_type or "").strip() or None

    track_default = await _track_default_entry_type_key(track)
    view_type = str(getattr(view, "type", "") or "feed").lower()
    view_name = str(getattr(view, "name", "") or "")

    if view_type == "calendar":
        cfg = view.config if isinstance(view.config, dict) else {}
        mapping = normalize_calendar_mapping(cfg)
        date_field = mapping.get("date_field") or "created_at"

        if not resolved_type:
            slug = resolve_calendar_create_entry_type_key(
                view, entry_types, date_field, track_default
            )
            if slug:
                for et in entry_types:
                    if _entry_type_matches_slug(getattr(et, "name", "") or "", slug):
                        resolved_type = getattr(et, "name", "") or slug
                        break
                if not resolved_type:
                    resolved_type = slug

        if (
            not agent_entry_type
            and date_field not in READONLY_DATE_FIELDS
            and date_field not in out_fields
        ):
            iso = extract_date_iso(text, out_fields)
            if iso:
                out_fields[date_field] = iso
            else:
                warnings.append(
                    f"Calendar view '{view_name}' maps date field '{date_field}' — "
                    "no date was inferred; entry may not appear on the calendar."
                )

    elif view_type == "kanban":
        if not resolved_type:
            slug = resolve_view_create_entry_type_key(view)
            if slug:
                for et in entry_types:
                    if _entry_type_matches_slug(getattr(et, "name", "") or "", slug):
                        resolved_type = getattr(et, "name", "") or slug
                        break
                if not resolved_type:
                    resolved_type = slug

        cfg = view.config if isinstance(view.config, dict) else {}
        group_by = _resolve_kanban_group_by(cfg.get("group_by"))
        write_key = _resolve_kanban_group_field_key(group_by)
        stage_hint = (kanban_stage_hint or "").strip()
        if (
            stage_hint
            and write_key not in out_fields
            and KANBAN_STAGE_KEY not in out_fields
        ):
            key = write_key if write_key != KANBAN_STAGE_KEY else KANBAN_STAGE_KEY
            out_fields[key] = _slugify_key(stage_hint) or stage_hint

    else:
        if not resolved_type:
            slug = resolve_view_create_entry_type_key(view)
            if not slug and track_default:
                slug = track_default
            if slug:
                for et in entry_types:
                    if _entry_type_matches_slug(getattr(et, "name", "") or "", slug):
                        resolved_type = getattr(et, "name", "") or slug
                        break
                if not resolved_type:
                    resolved_type = slug

    return ViewCreateResolution(
        entry_type=resolved_type,
        fields=out_fields,
        view_name=view_name,
        view_type=view_type,
        warnings=warnings,
    )


async def load_entry_types_for_track(track: Track) -> List[EntryType]:
    """Load all EntryType nodes under a track's attached content profile."""
    cp = await get_track_attached_content_profile(track)
    if not cp:
        return []
    nodes = await cp.nodes(edge=["CONTAINS"], node=["EntryType"])
    return [n for n in nodes if isinstance(n, EntryType)]
