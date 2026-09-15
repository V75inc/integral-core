"""Manifest-declared track-type alias groups (F0).

Cross-app handoffs (e.g. CRM → Projects) declare aliases in
``app.track_aliases`` on the bundle manifest. Substrate never hardcodes
domain track names (I-EXT-01).
"""

from __future__ import annotations

from typing import Dict, Iterable, List, Optional, Set

# workspace_id -> list of frozensets of equivalent track type slugs
_ALIAS_GROUPS: Dict[str, List[frozenset]] = {}


def _slug(value: object) -> str:
    return str(value or "").strip().lower().replace(" ", "_").replace("-", "_")


def clear_workspace_track_aliases(workspace_id: str) -> None:
    _ALIAS_GROUPS.pop(workspace_id, None)


def register_track_aliases(
    workspace_id: str,
    groups: Iterable[Iterable[str]],
    *,
    replace: bool = False,
) -> None:
    """Register alias groups for a workspace.

    Each group is a collection of equivalent track template_id / title slugs.
    When ``replace`` is False, groups are merged with any already registered.
    """
    bucket = [] if replace else list(_ALIAS_GROUPS.get(workspace_id) or [])
    existing = {frozenset(g) for g in bucket}
    for group in groups:
        normalized = frozenset(_slug(x) for x in group if _slug(x))
        if len(normalized) < 2:
            continue
        if normalized not in existing:
            bucket.append(normalized)
            existing.add(normalized)
    _ALIAS_GROUPS[workspace_id] = bucket


def register_track_aliases_from_manifest(
    workspace_id: str, manifest: Optional[dict]
) -> None:
    """Read ``app.track_aliases`` from a compiled/attached manifest."""
    if not isinstance(manifest, dict):
        return
    app_block = manifest.get("app") or {}
    raw = app_block.get("track_aliases") or []
    if not isinstance(raw, list):
        return
    groups: List[List[str]] = []
    for item in raw:
        if isinstance(item, dict) and "aliases" in item:
            groups.append([str(x) for x in (item.get("aliases") or [])])
        elif isinstance(item, (list, tuple)):
            groups.append([str(x) for x in item])
    register_track_aliases(workspace_id, groups, replace=False)


def track_type_want_set(track_type: str, workspace_id: Optional[str] = None) -> Set[str]:
    """Return the set of slugs equivalent to ``track_type``."""
    want = _slug(track_type)
    if not want:
        return set()
    groups = _ALIAS_GROUPS.get(workspace_id or "") or []
    for group in groups:
        if want in group:
            return set(group)
    return {want}


def track_types_equivalent(
    a: object, b: object, workspace_id: Optional[str] = None
) -> bool:
    sa, sb = _slug(a), _slug(b)
    if not sa or not sb:
        return sa == sb
    if sa == sb:
        return True
    want = track_type_want_set(sa, workspace_id)
    return sb in want
