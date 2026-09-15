"""Canonical content profile runtime helpers.

Orchestration facade over split modules — see ``.planning/refactors/content_profile_runtime_split_plan.md``.
"""

from __future__ import annotations

import contextlib
import logging
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

from app.exceptions import BadRequestError
from app.models.nodes import App, ContentProfile, Track, View

# Public re-exports (import paths unchanged for callers).
from app.services.content_profile_compile import (  # noqa: F401
    SCHEMA_VERSION,
    VALID_PROFILE_SCOPES,
    app_manifest_provisioning_enabled,
    compile_canonical_manifest,
    find_app_track_spec_by_key,
    find_app_track_template_spec_by_key,
    invalidate_manifest_cache,
    materialize_view_config_from_spec,
    normalize_entry_type_form_schema,
    normalize_view_config,
    repair_stored_manifest_for_compile,
    slug_manifest_key,
)
from app.services.content_profile_entry_fields import (  # noqa: F401
    build_entry_index_document,
    resolve_entry_type_spec,
    restrict_custom_fields_to_entry_type,
    transition_custom_fields_on_type_change,
    validate_and_materialize_entry_custom_fields,
    validate_tags_apply_to_entry_type,
    validate_taxonomy_constraints,
)
from app.services.content_profile_graph import (  # noqa: F401
    materialize_anchor_track,
    seed_taxonomy_for_track,
    sync_attached_manifest,
    sync_relation_edges,
)

logger = logging.getLogger(__name__)

from app.models.edges import CATALOGS
from app.services.app_graph import (
    get_app_attached_content_profile,
    get_or_create_views_registry_for_content_profile,
    get_track_attached_content_profile,
)
from app.services.content_profile_compile import (
    _as_dict,
    _as_list,
    _scan_seeded_libraries_for_view_key,
    _slug,
)
from app.services.content_profile_graph import (
    _resolve_or_create_template_content_profile,
)


async def backfill_view_entry_type_constraints_from_manifest(
    track: Track,
    views: List[View],
) -> None:
    """Hydrate Views that pre-date the entry-type constraint fields.

    .. deprecated::
        Prefer ``write_view_entry_type_constraints_from_manifest`` at
        merge/materialization time. This read-path backfill remains for
        legacy rows until list endpoints stop calling it.

    Reads the matching manifest view spec for each View and copies
    ``entry_types`` / ``default_entry_type`` onto the node when empty.
    Idempotent — only writes when the View's current value is empty and the
    spec carries a non-empty value. Matches by ``_manifest_view_key`` (or
    falls back to slugified view name). When the persisted manifest lacks
    ``entry_types`` (DB pre-dates the field), falls back to the matching
    seeded library spec.
    """
    if not views:
        return
    cp, tier, _ = await resolve_track_runtime_profile(track)
    by_key: Dict[str, Dict[str, Any]] = {}
    if tier:
        for spec in _as_list(tier.get("views"), where="track.views"):
            if not isinstance(spec, dict):
                continue
            key = str(spec.get("key") or "").strip()
            if key:
                by_key[key] = spec

    for v in views:
        cfg = v.config if isinstance(v.config, dict) else {}
        manifest_key = str(cfg.get("_manifest_view_key") or "").strip() or _slug(
            str(v.name or "")
        )
        spec = by_key.get(manifest_key)
        # Manifest spec may pre-date the entry_types/default_entry_type
        # fields. Fall back to the seed library declaration.
        if spec is None or not (
            spec.get("entry_types") or spec.get("default_entry_type")
        ):
            seed_spec = _scan_seeded_libraries_for_view_key(manifest_key)
            if seed_spec is not None:
                spec = seed_spec
        if not spec:
            continue
        dirty = False
        if not (getattr(v, "entry_type_keys", None) or []):
            raw = list(spec.get("entry_types") or [])
            keys = []
            seen = set()
            for k in raw:
                s = _slug(str(k))
                if s and s not in seen:
                    keys.append(s)
                    seen.add(s)
            if keys:
                v.entry_type_keys = keys
                dirty = True
        if not (getattr(v, "default_entry_type_key", "") or ""):
            raw_default = str(spec.get("default_entry_type") or "").strip()
            if raw_default:
                d = _slug(raw_default)
                if d and d != "item":
                    v.default_entry_type_key = d
                    dirty = True
        if dirty:
            # Best-effort; the runtime filter has no fallback when this
            # fails, but a retry on next list call is harmless.
            with contextlib.suppress(Exception):
                await v.save()


async def write_view_entry_type_constraints_from_manifest(
    track: Track,
    views: List[View],
) -> None:
    """Authoritatively copy manifest view entry-type constraints onto View nodes.

    Called from ``content_profile_merge`` after view materialization so
    constraints are written at merge time rather than lazily on list.
    Overwrites ``entry_type_keys`` / ``default_entry_type_key`` when the
    manifest spec carries values.
    """
    if not views:
        return
    cp, tier, _ = await resolve_track_runtime_profile(track)
    by_key: Dict[str, Dict[str, Any]] = {}
    if tier:
        for spec in _as_list(tier.get("views"), where="track.views"):
            if not isinstance(spec, dict):
                continue
            key = str(spec.get("key") or "").strip()
            if key:
                by_key[key] = spec

    for v in views:
        cfg = v.config if isinstance(v.config, dict) else {}
        manifest_key = str(cfg.get("_manifest_view_key") or "").strip() or _slug(
            str(v.name or "")
        )
        spec = by_key.get(manifest_key)
        if spec is None or not (
            spec.get("entry_types") or spec.get("default_entry_type")
        ):
            seed_spec = _scan_seeded_libraries_for_view_key(manifest_key)
            if seed_spec is not None:
                spec = seed_spec
        if not spec:
            continue
        dirty = False
        raw = list(spec.get("entry_types") or spec.get("entry_type_keys") or [])
        keys: List[str] = []
        seen: set[str] = set()
        for k in raw:
            s = _slug(str(k))
            if s and s not in seen:
                keys.append(s)
                seen.add(s)
        if keys and list(getattr(v, "entry_type_keys", None) or []) != keys:
            v.entry_type_keys = keys
            dirty = True
        raw_default = str(spec.get("default_entry_type") or "").strip()
        if raw_default:
            d = _slug(raw_default)
            if d and d != "item" and getattr(v, "default_entry_type_key", "") != d:
                v.default_entry_type_key = d
                dirty = True
        if dirty:
            with contextlib.suppress(Exception):
                await v.save()


async def _try_refresh_anchor_template_cp(
    track: Track, stale_cp: ContentProfile
) -> Optional[ContentProfile]:
    """Refresh an anchor-track template CP from the parent App's current
    ``app.track_templates[]`` spec.

    Returns the refreshed CP on success, or ``None`` when the track is not
    anchored (no ``template_id``) or the parent App's current manifest
    does not declare a template under that key. Used by
    ``resolve_track_runtime_profile`` to recover from stale template CPs
    that fail strict compile validation (e.g. dangling
    ``defaults.default_entry_type`` references after a manifest spec edit).
    """
    template_key = str(getattr(track, "template_id", "") or "").strip()
    if not template_key:
        return None
    apps = await track.nodes(edge=["CONTAINS"], direction="in", node=["WorkspaceApp"])
    app_node: Optional[App] = None
    for cand in apps:
        if isinstance(cand, App):
            app_node = cand
            break
    if app_node is None:
        return None
    from app.services.app_graph import get_app_attached_content_profile

    app_cp = await get_app_attached_content_profile(app_node)
    if app_cp is None or not app_cp.manifest:
        return None
    try:
        canonical = compile_canonical_manifest(
            manifest=_as_dict(app_cp.manifest, where="app content profile manifest")
        )
    except BadRequestError:
        logger.exception("anchor template refresh: App manifest compile failed")
        return None
    template_spec = find_app_track_template_spec_by_key(canonical, template_key)
    if template_spec is None:
        return None
    # Drop the stale CP from the cache so the refresh resolver can refetch
    # candidates by traversal; rebuild via the resolver so the materialization
    # discriminator + canonical manifest match the current spec.
    now = datetime.now(timezone.utc).isoformat()
    refreshed = await _resolve_or_create_template_content_profile(
        app_node=app_node,
        template_key=template_key,
        template_spec=template_spec,
        now=now,
    )
    invalidate_manifest_cache()
    return refreshed


async def resolve_track_runtime_profile(
    track: Track,
) -> Tuple[Optional[ContentProfile], Dict[str, Any], Optional[str]]:
    """Resolve effective profile for a track.

    Returns ``(content_profile, tier, track_type_key)`` where tier is a canonical
    track-level contract with ``entry_types/views/taxonomy/defaults``.

    Anchored tracks (``track.template_id`` matches a
    ``app.track_templates[]`` key) lazily refresh their attached CP's
    manifest from the current template spec on every read — this keeps
    template-CP nodes in sync after the source template spec is edited (e.g.
    library version bump, agent-authored revision). Without this, a stale
    template CP can hold dangling ``defaults.default_entry_type`` references
    that fail strict compile validation.
    """
    cp = await get_track_attached_content_profile(track)
    if cp and cp.manifest:
        try:
            manifest = compile_canonical_manifest(
                manifest=repair_stored_manifest_for_compile(
                    _as_dict(cp.manifest, where="content profile manifest")
                )
            )
        except BadRequestError:
            # Stale anchor template CP — try to refresh from the parent
            # app_node's current ``track_templates[]`` spec, then re-compile.
            refreshed = await _try_refresh_anchor_template_cp(track, cp)
            if refreshed is None:
                raise
            manifest = compile_canonical_manifest(
                manifest=repair_stored_manifest_for_compile(
                    _as_dict(refreshed.manifest, where="content profile manifest")
                )
            )
            cp = refreshed
        if manifest.get("scope") == "track":
            track_tier = manifest.get("track") or {}
            # Anchored track whose template CP compiled fine but has lost its
            # entry_types (e.g. auto-provisioned before the parent App's
            # ``track_templates[]`` spec carried them, or a drifted snapshot).
            # The compile-failure refresh above never fires for a
            # valid-but-empty CP, leaving the tier typeless — which makes entry
            # filing raise "Entry type not found" (e.g. adding a task to a
            # project's anchored tasks board). Honour the documented "refresh
            # from the current template spec on every read" intent: when an
            # anchored track resolves to an empty-typed tier, refresh from the
            # App's current template and use that.
            if (
                not track_tier.get("entry_types")
                and str(getattr(track, "template_id", "") or "").strip()
            ):
                refreshed = await _try_refresh_anchor_template_cp(track, cp)
                if refreshed is not None and refreshed.manifest:
                    try:
                        r_manifest = compile_canonical_manifest(
                            manifest=repair_stored_manifest_for_compile(
                                _as_dict(
                                    refreshed.manifest,
                                    where="content profile manifest",
                                )
                            )
                        )
                    except BadRequestError:
                        r_manifest = None
                    if r_manifest is not None and r_manifest.get("scope") == "track":
                        return refreshed, r_manifest.get("track") or {}, None
            return cp, track_tier, None

    apps = await track.nodes(edge=["CONTAINS"], direction="in", node=["WorkspaceApp"])
    if apps:
        app_node = apps[0]
        if isinstance(app_node, App):
            scp = await get_app_attached_content_profile(app_node)
            if scp and scp.manifest:
                manifest = compile_canonical_manifest(
                    manifest=repair_stored_manifest_for_compile(
                        _as_dict(scp.manifest, where="app content profile manifest")
                    )
                )
                if manifest.get("scope") == "app":
                    tracks = _as_list(
                        _as_dict(manifest.get("app"), where="app").get("tracks"),
                        where="app.tracks",
                    )
                    key_candidates = [
                        _slug(str(track.template_id or "")),
                        _slug(str(track.title or "")),
                    ]
                    selected = None
                    for cand in key_candidates:
                        if not cand:
                            continue
                        for t in tracks:
                            td = _as_dict(t, where="app.tracks[]")
                            if str(td.get("key") or "") == cand:
                                selected = td
                                break
                        if selected:
                            break
                    if selected is None and tracks:
                        selected = _as_dict(tracks[0], where="app.tracks[0]")
                    if selected:
                        return scp, selected, str(selected.get("key") or "")
    return cp, {}, None


async def ensure_track_views_materialized_from_tier(track: Track) -> bool:
    """Create or patch per-track View nodes from the manifest tier when missing.

    Anchored tracks share a by-reference template ContentProfile. Views must be
    materialized with ``track_id`` set so editors can persist board settings
    (group_by, columns) and permission checks resolve against the anchor track.
    """
    cp = await get_track_attached_content_profile(track)
    if cp is None:
        return False
    _, tier, _ = await resolve_track_runtime_profile(track)
    if not isinstance(tier, dict):
        return False
    view_specs = list(tier.get("views") or [])
    if not view_specs:
        return False

    vreg = await get_or_create_views_registry_for_content_profile(cp, track=track)
    raw_views = await vreg.nodes(edge=[CATALOGS], node=["View"])
    views = [v for v in raw_views if isinstance(v, View)]
    track_id = str(track.id or "")
    track_views = [
        v for v in views if str(getattr(v, "track_id", "") or "") == track_id
    ]
    existing_keys: set[str] = set()
    for v in track_views:
        cfg = v.config if isinstance(v.config, dict) else {}
        key = slug_manifest_key(str(cfg.get("_manifest_view_key") or ""))
        if key:
            existing_keys.add(key)
    needed_keys = {
        slug_manifest_key(str(spec.get("key") or ""))
        for spec in view_specs
        if str(spec.get("key") or "").strip()
    }
    if needed_keys and needed_keys.issubset(existing_keys):
        return False

    template_key = str(getattr(track, "template_id", "") or "").strip()
    if template_key:
        apps = await track.nodes(
            edge=["CONTAINS"], direction="in", node=["WorkspaceApp"]
        )
        app_node: Optional[App] = None
        for cand in apps:
            if isinstance(cand, App):
                app_node = cand
                break
        if app_node is not None:
            from app.services.content_profile_merge import (
                refresh_app_track_template_materialization,
            )

            await refresh_app_track_template_materialization(app_node, template_key)

    from app.services.content_profile_merge import (
        _InMemoryLibraryManifest,
        merge_library_manifest_into_content_profile,
    )

    shim = _InMemoryLibraryManifest(
        {
            "content_profile_schema_version": SCHEMA_VERSION,
            "scope": "track",
            "track": tier,
        }
    )
    await merge_library_manifest_into_content_profile(shim, cp, track, for_space=False)
    await synchronize_track_view_default_flags(track)
    return True


async def _catalog_views_for_track(track: Track) -> List[View]:
    cp = await get_track_attached_content_profile(track)
    if not cp:
        return []
    vreg = await get_or_create_views_registry_for_content_profile(cp, track=track)
    views = await vreg.nodes(edge=[CATALOGS], node=["View"])
    # Anchored tracks share one Views registry per template CP. Default-flag
    # sync and pickers must only see this track's rows — otherwise one
    # track's default clears ``is_default`` on every sibling.
    return [
        v
        for v in views
        if isinstance(v, View)
        and str(getattr(v, "track_id", "") or "") == str(track.id)
    ]


async def pick_default_view_id_for_track(
    track: Track, views: List[View]
) -> Optional[str]:
    """Choose the single canonical default view id for a track (manifest + DB flags)."""
    if not views:
        return None
    _, tier, _ = await resolve_track_runtime_profile(track)
    defaults = (tier.get("defaults") or {}) if isinstance(tier, dict) else {}
    dv_raw = str(defaults.get("default_view") or "").strip()
    want_key = _slug(dv_raw) if dv_raw else ""

    if want_key:
        for v in views:
            cfg = getattr(v, "config", None) or {}
            if not isinstance(cfg, dict):
                cfg = {}
            vk = str(cfg.get("_manifest_view_key") or "").strip()
            if vk and _slug(vk) == want_key:
                return str(v.id)

    trues = [v for v in views if bool(getattr(v, "is_default", False))]
    if len(trues) == 1:
        return str(trues[0].id)
    if len(trues) > 1:
        trues.sort(key=lambda x: (str(getattr(x, "created_at", "") or ""), str(x.id)))
        return str(trues[0].id)
    ordered = sorted(
        views, key=lambda x: (str(getattr(x, "created_at", "") or ""), str(x.id))
    )
    return str(ordered[0].id) if ordered else None


def normalize_view_list_default_exports(
    items: List[Dict[str, Any]], winner_id: Optional[str]
) -> List[Dict[str, Any]]:
    """Expose exactly one ``is_default: true`` in list payloads."""
    if not items:
        return items
    out: List[Dict[str, Any]] = []
    for it in items:
        row = dict(it)
        row["is_default"] = bool(winner_id) and str(row.get("id")) == str(winner_id)
        out.append(row)
    return out


async def synchronize_track_view_default_flags(track: Track) -> None:
    """Persist a single ``View.is_default`` per track, aligned with manifest defaults."""
    views = await _catalog_views_for_track(track)
    if not views:
        return
    wid = await pick_default_view_id_for_track(track, views)
    if not wid:
        return
    for v in views:
        want = str(v.id) == wid
        if bool(getattr(v, "is_default", False)) != want:
            v.is_default = want
            await v.save()


# ----------------------------------------------------------------------
# Phase 9 Plan 09-05 (ONBD-01 / W11) — in-process library-package list.
# ----------------------------------------------------------------------

from dataclasses import dataclass as _onbd_dataclass


@_onbd_dataclass
class LibraryPackageSummary:
    """Typed summary of a catalogued library-package ContentProfile.

    Used by the onboarding MCP tool (``integral_onboard_user``) and any
    other in-process caller that needs to enumerate library packages
    without HTTP round-tripping. Mirrors the existing
    ``GET /api/content-profiles`` projection, but as a Python dataclass.
    """

    id: str
    name: str
    scope: str
    description: str


async def list_library_packages() -> List[LibraryPackageSummary]:
    """Return cataloged library-package ContentProfiles as typed summaries.

    Mirrors the query shape used by
    ``services/content_profile_library_seed.py`` (jvspatial context-field
    filter on ``library_package=True``). Use this in-process from agentive
    tools — there is no HTTP equivalent that returns this exact shape.
    """
    # Local import avoids the circular dep with app.models.nodes that
    # would occur at module-import time (this module is itself imported
    # by services/app_graph.py which models/nodes.py transitively pulls).
    from app.models.nodes import ContentProfile

    raw = await ContentProfile.find({"context.library_package": True})
    if raw is None:
        rows: list = []
    elif isinstance(raw, (list, tuple)):
        rows = list(raw)
    else:
        rows = [raw]
    return [
        LibraryPackageSummary(
            id=cp.id,
            name=cp.name,
            scope=cp.scope,
            description=cp.description,
        )
        for cp in rows
    ]
