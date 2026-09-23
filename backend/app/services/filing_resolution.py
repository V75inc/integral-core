"""Thin filing resolution primitives for agentive propose tools.

Mechanical helpers only: fuzzy name matching and hint-based entry-type lookup.
Classification, field extraction, facet decomposition, and tag suggestion are
owned by skill-based agents via ``integral_list_tracks``,
``integral_get_track_schema``, and explicit ``integral_file_content`` params.
"""

from __future__ import annotations

import logging
from typing import List, Optional, Tuple

from app.models.nodes import App, EntryType, Track
from app.services.agent_scope import (
    accessible_apps_for_scope,
    accessible_tracks_for_scope,
    active_workspace_id,
)
from app.services.operational_model_runtime import resolve_track_runtime_profile
from app.services.request_scope import matches_workspace
from app.utils.text_matching import casefold_match

logger = logging.getLogger(__name__)


async def resolve_track_by_name(
    user_id: str,
    track_hint: str,
    focused_track_id: Optional[str] = None,
    min_score: float = 0.5,
) -> Optional[Tuple[Track, float]]:
    """Resolve a track hint to a Track node using fuzzy name matching."""
    if not track_hint or not track_hint.strip():
        return None

    tracks = await accessible_tracks_for_scope(user_id)
    if not tracks:
        return None

    from app.services.app_graph import get_track_attached_operational_model

    candidates: List[Tuple[Track, float]] = []
    for track in tracks:
        score = casefold_match(
            track_hint,
            track.title_fold or track.title.casefold() if track.title else "",
        )
        if track.id == focused_track_id:
            score = min(score + 0.2, 1.0)
        if score < min_score:
            try:
                cp = await get_track_attached_operational_model(track)
                if cp:
                    cp_name = (cp.name or "").casefold()
                    cp_desc = (getattr(cp, "description", "") or "").casefold()
                    hint_fold = track_hint.strip().casefold()
                    if cp_name and (
                        hint_fold in cp_name or cp_name.startswith(hint_fold)
                    ):
                        score = max(score, 0.7)
                    elif cp_desc and hint_fold in cp_desc:
                        score = max(score, 0.6)
                    manifest = cp.manifest or {}
                    pkg = manifest.get("package", {})
                    if isinstance(pkg, dict):
                        pkg_name = str(pkg.get("name", "")).casefold()
                        if pkg_name and (
                            hint_fold in pkg_name or pkg_name.startswith(hint_fold)
                        ):
                            score = max(score, 0.75)
            except Exception:
                pass
        if score >= min_score:
            candidates.append((track, score))

    if not candidates:
        return None

    candidates.sort(key=lambda x: x[1], reverse=True)
    return candidates[0]


async def resolve_space_by_name(
    user_id: str,
    space_hint: str,
    min_score: float = 0.5,
) -> Optional[Tuple[App, float]]:
    """Resolve an App hint to an App node using fuzzy name matching."""
    if not space_hint or not space_hint.strip():
        return None

    apps = await accessible_apps_for_scope(user_id)
    if not apps:
        return None

    candidates: List[Tuple[App, float]] = []
    for app_node in apps:
        score = casefold_match(
            space_hint,
            app_node.name_fold or app_node.name.casefold() if app_node.name else "",
        )
        if score >= min_score:
            candidates.append((app_node, score))

    if not candidates:
        return None

    candidates.sort(key=lambda x: x[1], reverse=True)
    return candidates[0]


async def resolve_track_for_filing(
    user_id: str,
    *,
    track_id: Optional[str] = None,
    track_hint: Optional[str] = None,
    focused_track_id: Optional[str] = None,
) -> Optional[Track]:
    """Resolve a track from explicit id, hint, or UI focus — no recency fallback."""
    if track_id:
        track = await Track.get(track_id)
        if track and (
            not active_workspace_id() or matches_workspace(track, active_workspace_id())
        ):
            return track
        return None

    if track_hint and track_hint.strip():
        match = await resolve_track_by_name(
            user_id, track_hint, focused_track_id=focused_track_id
        )
        if match:
            return match[0]

    if focused_track_id:
        candidate = await Track.get(focused_track_id)
        if candidate and (
            not active_workspace_id()
            or matches_workspace(candidate, active_workspace_id())
        ):
            return candidate

    return None


async def resolve_entry_type_for_track(
    track: Track,
    type_hint: Optional[str] = None,
) -> Optional[EntryType]:
    """Resolve an entry type by exact/substring hint match only.

    Does not score text against form_schema or fall back to the Operational Model's
    first entry type — the agent supplies ``type_hint`` from schema introspection.
    """
    cp, _, _ = await resolve_track_runtime_profile(track)
    if cp is None:
        return None

    entry_types = await cp.nodes(edge=["CONTAINS"], node=["EntryType"])
    if not entry_types:
        return None

    if not type_hint or not type_hint.strip():
        return None

    hint_fold = type_hint.strip().casefold()
    for et in entry_types:
        name_fold = (et.name or "").casefold()
        key_fold = (
            getattr(et, "key", None)
            or et.form_schema.get("_manifest_entry_type_key")
            or ""
        ).casefold()

        if (
            hint_fold == name_fold
            or hint_fold in name_fold
            or name_fold.startswith(hint_fold)
            or (key_fold and (hint_fold == key_fold or hint_fold in key_fold))
        ):
            return et

    return None
