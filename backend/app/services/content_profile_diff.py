"""Structural diff + entry-impact for ContentProfile drafts.

Pillar 2 of the agent-authorable substrate. Pure functions — no I/O for diff
itself. ``compute_entry_impact`` does query the graph for the affected
entries and dry-run validates them against the candidate (draft) spec,
returning a structured impact summary the agent and UI can act on.

Diff shape (returned by :func:`compute_manifest_diff`):

    {
      "scope": "track" | "app",
      "field_types":   {"added": [...], "removed": [...], "changed": [...]},
      "view_types":    {"added": [...], "removed": [...], "changed": [...]},
      "entry_types":   {"added": [...], "removed": [...], "changed": [...]},
      "views":         {"added": [...], "removed": [...], "changed": [...]},
      "tags":          {"added": [...], "removed": [...], "changed": [...]},
      "relations":     {"added": [...], "removed": [...]},
    }

The ``changed`` lists for entry_types and views include nested per-field
deltas. Diff entries always carry ``key`` so the UI / agent can reference
back into the manifest.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple

from app.exceptions import BadRequestError
from app.models.edges import CONTAINS
from app.models.nodes import ContentProfile, EntryType, Track

# ---------------------------------------------------------------------------
# Pure structural diff
# ---------------------------------------------------------------------------


def _by_key(items: Optional[List[Dict[str, Any]]]) -> Dict[str, Dict[str, Any]]:
    out: Dict[str, Dict[str, Any]] = {}
    for item in items or []:
        if not isinstance(item, dict):
            continue
        k = str(item.get("key") or item.get("name") or "").strip()
        if not k:
            continue
        out[k] = item
    return out


def _set_diff(
    before: Dict[str, Dict[str, Any]],
    after: Dict[str, Dict[str, Any]],
) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]], List[str]]:
    """Return (added_items, removed_items, common_keys)."""
    before_keys = set(before.keys())
    after_keys = set(after.keys())
    added = [after[k] for k in sorted(after_keys - before_keys)]
    removed = [before[k] for k in sorted(before_keys - after_keys)]
    common = sorted(before_keys & after_keys)
    return added, removed, common


def _entry_type_field_diff(
    before_fields: List[Dict[str, Any]],
    after_fields: List[Dict[str, Any]],
) -> Dict[str, List[Dict[str, Any]]]:
    before_by = _by_key(before_fields)
    after_by = _by_key(after_fields)
    added, removed, common = _set_diff(before_by, after_by)
    changed: List[Dict[str, Any]] = []
    for k in common:
        if before_by[k] != after_by[k]:
            changed.append(
                {
                    "key": k,
                    "before": before_by[k],
                    "after": after_by[k],
                }
            )
    return {"added": added, "removed": removed, "changed": changed}


def _entry_type_diff(
    before: Dict[str, Dict[str, Any]],
    after: Dict[str, Dict[str, Any]],
) -> Dict[str, List[Dict[str, Any]]]:
    added, removed, common = _set_diff(before, after)
    changed: List[Dict[str, Any]] = []
    for k in common:
        b = before[k]
        a = after[k]
        field_delta = _entry_type_field_diff(
            b.get("fields") or [], a.get("fields") or []
        )
        any_field_change = (
            field_delta["added"] or field_delta["removed"] or field_delta["changed"]
        )
        meta_changed = {
            mk: (b.get(mk), a.get(mk))
            for mk in ("name", "icon", "base_fields", "required_tag_groups")
            if b.get(mk) != a.get(mk)
        }
        if any_field_change or meta_changed:
            changed.append(
                {
                    "key": k,
                    "fields": field_delta,
                    "meta_changed": meta_changed,
                }
            )
    return {"added": added, "removed": removed, "changed": changed}


def _shallow_diff(
    before: Dict[str, Dict[str, Any]],
    after: Dict[str, Dict[str, Any]],
) -> Dict[str, List[Dict[str, Any]]]:
    added, removed, common = _set_diff(before, after)
    changed: List[Dict[str, Any]] = []
    for k in common:
        if before[k] != after[k]:
            changed.append({"key": k, "before": before[k], "after": after[k]})
    return {"added": added, "removed": removed, "changed": changed}


def _tag_diff(
    before_taxonomy: Dict[str, Any],
    after_taxonomy: Dict[str, Any],
) -> Dict[str, List[Dict[str, Any]]]:
    """Flatten taxonomy.tag_groups[].tags[] across groups for diff."""

    def _flatten(tx: Dict[str, Any]) -> Dict[str, Dict[str, Any]]:
        out: Dict[str, Dict[str, Any]] = {}
        for grp in (tx or {}).get("tag_groups") or []:
            gkey = str((grp or {}).get("key") or "default")
            for tag in (grp or {}).get("tags") or []:
                tk = str((tag or {}).get("key") or (tag or {}).get("name") or "")
                if not tk:
                    continue
                out[f"{gkey}:{tk}"] = {**tag, "_group_key": gkey}
        return out

    return _shallow_diff(_flatten(before_taxonomy), _flatten(after_taxonomy))


def _track_tier_diff(
    before_tier: Dict[str, Any],
    after_tier: Dict[str, Any],
) -> Dict[str, Any]:
    return {
        "entry_types": _entry_type_diff(
            _by_key(before_tier.get("entry_types") or []),
            _by_key(after_tier.get("entry_types") or []),
        ),
        "views": _shallow_diff(
            _by_key(before_tier.get("views") or []),
            _by_key(after_tier.get("views") or []),
        ),
        "tags": _tag_diff(
            before_tier.get("taxonomy") or {},
            after_tier.get("taxonomy") or {},
        ),
    }


def _relation_diff(
    before_relations: List[Dict[str, Any]],
    after_relations: List[Dict[str, Any]],
) -> Dict[str, List[Dict[str, Any]]]:
    def _key(rel: Dict[str, Any]) -> str:
        return (
            f"{rel.get('source_track_type')}->"
            f"{rel.get('target_track_type')}@"
            f"{rel.get('relation_field') or ''}"
        )

    before_by = {_key(r): r for r in before_relations or []}
    after_by = {_key(r): r for r in after_relations or []}
    added_keys = sorted(set(after_by) - set(before_by))
    removed_keys = sorted(set(before_by) - set(after_by))
    return {
        "added": [after_by[k] for k in added_keys],
        "removed": [before_by[k] for k in removed_keys],
    }


def compute_manifest_diff(
    before: Optional[Dict[str, Any]],
    after: Optional[Dict[str, Any]],
) -> Dict[str, Any]:
    """Pure structural diff between two compiled canonical manifests.

    Either side may be empty / missing — treated as a minimal manifest with no
    types, views, tags, or relations.
    """
    before = before or {}
    after = after or {}
    scope = str(after.get("scope") or before.get("scope") or "track")

    # Composites
    field_types_diff = _shallow_diff(
        _by_key(before.get("field_types") or []),
        _by_key(after.get("field_types") or []),
    )
    view_types_diff = _shallow_diff(
        _by_key(before.get("view_types") or []),
        _by_key(after.get("view_types") or []),
    )

    if scope == "app":
        b_space = before.get("app") or {}
        a_space = after.get("app") or {}
        track_diffs: List[Dict[str, Any]] = []
        b_tracks = _by_key(b_space.get("tracks") or [])
        a_tracks = _by_key(a_space.get("tracks") or [])
        added, removed, common = _set_diff(b_tracks, a_tracks)
        for k in common:
            tier_delta = _track_tier_diff(b_tracks[k], a_tracks[k])
            empty = all(
                not (
                    section["added"] or section["removed"] or section.get("changed", [])
                )
                for section in tier_delta.values()
            )
            if not empty:
                track_diffs.append({"key": k, **tier_delta})
        # Phase 10 Plan 10-03 — v2 operational-layer section diffs.
        skills_diff = _shallow_diff(
            _by_key(b_space.get("skills") or []),
            _by_key(a_space.get("skills") or []),
        )
        agents_diff = _shallow_diff(
            _by_key(b_space.get("agents") or []),
            _by_key(a_space.get("agents") or []),
        )

        # seeds keyed by ``track`` (one seed group per track)
        def _seeds_by_track(items: List[Dict[str, Any]]) -> Dict[str, Dict[str, Any]]:
            out: Dict[str, Dict[str, Any]] = {}
            for it in items or []:
                if not isinstance(it, dict):
                    continue
                tkey = str(it.get("track") or "")
                if tkey:
                    out[tkey] = it
            return out

        seeds_diff = _shallow_diff(
            _seeds_by_track(b_space.get("seeds") or []),
            _seeds_by_track(a_space.get("seeds") or []),
        )
        requires_apps_diff = _shallow_diff(
            _by_key(b_space.get("requires_apps") or []),
            _by_key(a_space.get("requires_apps") or []),
        )
        # settings_schema + permissions are dict singletons — surface
        # before/after pairs when they differ, empty list otherwise.
        b_settings = b_space.get("settings_schema") or {}
        a_settings = a_space.get("settings_schema") or {}
        settings_schema_diff = (
            {"before": b_settings, "after": a_settings, "changed": True}
            if b_settings != a_settings
            else {"before": b_settings, "after": a_settings, "changed": False}
        )
        b_perms = b_space.get("permissions") or {}
        a_perms = a_space.get("permissions") or {}
        permissions_diff = (
            {"before": b_perms, "after": a_perms, "changed": True}
            if b_perms != a_perms
            else {"before": b_perms, "after": a_perms, "changed": False}
        )
        return {
            "scope": "app",
            "field_types": field_types_diff,
            "view_types": view_types_diff,
            "tracks": {
                "added": added,
                "removed": removed,
                "changed": track_diffs,
            },
            "relations": _relation_diff(
                b_space.get("relations") or [],
                a_space.get("relations") or [],
            ),
            # v2 operational layer
            "skills": skills_diff,
            "agents": agents_diff,
            "settings_schema": settings_schema_diff,
            "seeds": seeds_diff,
            "permissions": permissions_diff,
            "requires_apps": requires_apps_diff,
        }

    # track scope (default)
    b_track = before.get("track") or {}
    a_track = after.get("track") or {}
    tier = _track_tier_diff(b_track, a_track)
    return {
        "scope": "track",
        "field_types": field_types_diff,
        "view_types": view_types_diff,
        "entry_types": tier["entry_types"],
        "views": tier["views"],
        "tags": tier["tags"],
        "relations": {"added": [], "removed": []},
    }


# ---------------------------------------------------------------------------
# Entry-impact: dry-run validate existing entries against the candidate spec
# ---------------------------------------------------------------------------


def _candidate_track_tier(
    candidate_manifest: Dict[str, Any],
    track: Track,
) -> Dict[str, Any]:
    """Extract the track tier from a candidate manifest.

    Mirrors ``resolve_track_runtime_profile`` but skips reading the live
    attached profile.
    """
    from app.services.content_profile_compile import _slug, compile_canonical_manifest

    if not candidate_manifest:
        return {}
    compiled = compile_canonical_manifest(manifest=dict(candidate_manifest))
    if compiled.get("scope") == "track":
        return compiled.get("track") or {}
    if compiled.get("scope") == "app":
        tracks = (compiled.get("app") or {}).get("tracks") or []
        if not tracks:
            return {}
        candidates = [
            _slug(str(track.template_id or "")),
            _slug(str(track.title or "")),
        ]
        for cand in candidates:
            if not cand:
                continue
            for t in tracks:
                if str((t or {}).get("key") or "") == cand:
                    return dict(t)
        return dict(tracks[0])
    return {}


async def compute_entry_impact(
    *,
    track: Track,
    candidate_manifest: Dict[str, Any],
    sample_limit: int = 20,
) -> Dict[str, Any]:
    """Walk a track's entries and report how many would be affected.

    Returns:
      ``{"track_id": ..., "total": <total entries in track>,
      "would_fail_validation": <count failing under candidate>,
      "would_need_migration": <count needing default-fill / coercion>,
      "sample_failing_ids": [entry_ids ...],
      "sample_failure_reasons": [{"entry_id": ..., "reason": "..."}, ...]}``

    "would_need_migration" is approximated as: entries that pass strict
    validation but reference a field key that no longer exists. Real migration
    runner (Phase 1 migrations module) handles the transform; this is the
    dry-run view.
    """
    from app.services.content_profile_entry_fields import (
        validate_and_materialize_entry_custom_fields,
    )

    runtime_tier = _candidate_track_tier(candidate_manifest, track)
    candidate_entry_types = runtime_tier.get("entry_types") or []
    candidate_keys = {
        str((et or {}).get("key") or "")
        for et in candidate_entry_types
        if isinstance(et, dict)
    }
    candidate_field_keys: Dict[str, set] = {
        str((et or {}).get("key") or ""): {
            str((f or {}).get("key") or "") for f in (et or {}).get("fields") or []
        }
        for et in candidate_entry_types
        if isinstance(et, dict)
    }

    entries = await track.nodes(edge=[CONTAINS], node=["Entry"])
    total = len(entries)
    would_fail_validation = 0
    would_need_migration = 0
    sample_failing_ids: List[str] = []
    sample_failure_reasons: List[Dict[str, str]] = []

    for entry in entries:
        et = await EntryType.get(getattr(entry, "type_id", None) or "")
        if et is None:
            would_fail_validation += 1
            if len(sample_failing_ids) < sample_limit:
                sample_failing_ids.append(entry.id)
                sample_failure_reasons.append(
                    {"entry_id": entry.id, "reason": "entry_type missing"}
                )
            continue
        et_key = str(et.name or "").strip().lower().replace(" ", "_")
        if et_key not in candidate_keys:
            would_fail_validation += 1
            if len(sample_failing_ids) < sample_limit:
                sample_failing_ids.append(entry.id)
                sample_failure_reasons.append(
                    {
                        "entry_id": entry.id,
                        "reason": (f"entry_type '{et_key}' removed from candidate"),
                    }
                )
            continue
        try:
            await validate_and_materialize_entry_custom_fields(
                track=track,
                entry_type=et,
                custom_fields=getattr(entry, "custom_fields", None) or {},
                runtime_tier=runtime_tier,
                entry=entry,
            )
        except BadRequestError as exc:
            would_fail_validation += 1
            if len(sample_failing_ids) < sample_limit:
                sample_failing_ids.append(entry.id)
                sample_failure_reasons.append(
                    {"entry_id": entry.id, "reason": exc.message}
                )
            continue
        # Field drift check — entry uses a key that no longer exists, or
        # is missing a new required field.
        custom = getattr(entry, "custom_fields", None) or {}
        legal = candidate_field_keys.get(et_key, set())
        used = {str(k) for k in custom.keys() if not str(k).startswith("_")}
        if used - legal:
            would_need_migration += 1

    return {
        "track_id": track.id,
        "total": total,
        "would_fail_validation": would_fail_validation,
        "would_need_migration": would_need_migration,
        "sample_failing_ids": sample_failing_ids,
        "sample_failure_reasons": sample_failure_reasons,
    }


async def compute_entry_impact_for_attached(
    *,
    cp: ContentProfile,
    candidate_manifest: Dict[str, Any],
    sample_limit: int = 20,
) -> List[Dict[str, Any]]:
    """Walk every track that uses ``cp`` and aggregate impact summaries.

    For track-attached profiles, finds the owning Track via the
    ``HAS_CONTENT_PROFILE`` reverse edge and runs :func:`compute_entry_impact`
    once. For app-attached profiles, repeats per child Track.
    """
    impacts: List[Dict[str, Any]] = []
    # Track-attached
    if cp.scope == "track":
        # Resolve track via app_graph helper (reverse edge).
        from app.models.nodes import Track as TrackNode

        candidate_tracks = await TrackNode.find(
            {"context.attached_content_profile_id": cp.id}
        )
        for t in candidate_tracks:
            impacts.append(
                await compute_entry_impact(
                    track=t,
                    candidate_manifest=candidate_manifest,
                    sample_limit=sample_limit,
                )
            )
    elif cp.scope == "app":
        from app.models.nodes import App as SpaceNode
        from app.models.nodes import Track as TrackNode

        candidate_spaces = await SpaceNode.find(
            {"context.attached_content_profile_id": cp.id}
        )
        for sp in candidate_spaces:
            tracks = await sp.nodes(edge=[CONTAINS], node=["Track"])
            for t in tracks:
                impacts.append(
                    await compute_entry_impact(
                        track=t,
                        candidate_manifest=candidate_manifest,
                        sample_limit=sample_limit,
                    )
                )
    return impacts
