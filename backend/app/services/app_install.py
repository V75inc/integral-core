"""Bundle install helpers — pre-flight, identity, seeds, and track materialization.

Extracted from ``app_lifecycle`` so install-time utilities can be imported
without pulling in the full install/uninstall state machine. Lifecycle
orchestration remains in ``app.services.app_lifecycle``.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional, Tuple

from app.exceptions import (
    AppDependencyError,
    BadRequestError,
    ContentProfileValidationError,
)
from app.models.edges import CONTAINS
from app.models.nodes import App, ContentProfile, Entry, Track
from app.services.content_profile_runtime import slug_manifest_key
from app.services.entry_type_resolver import resolve_seed_entry_type_id
from app.utils.time import utc_now_iso

logger = logging.getLogger(__name__)

INSTALL_INCLUDE_SEED_DATA_KEY = "install_include_seed_data"

__all__ = [
    "INSTALL_INCLUDE_SEED_DATA_KEY",
    "assert_unique_bundle_install",
    "bundle_identity_key",
    "effective_app_version",
    "find_existing_bundle_install",
    "manifest_seed_entry_count",
    "plant_seeds",
    "plant_seeds_for_install",
    "resolve_canonical_bundle_install",
    "resolve_include_seed_data",
    "stash_install_include_seed_data",
]


# ---------------------------------------------------------------------------
# Version helpers
# ---------------------------------------------------------------------------


def parse_version_tuple(v: str) -> Tuple[int, ...]:
    """Parse a dotted-decimal version string into a comparable tuple.

    Non-numeric segments degrade to 0 (e.g. "1.0.0-rc1" → (1, 0, 0)). This is
    intentionally simple — apps_v1 manifests use semver-like strings; full
    semver pre-release ordering is out of scope. For comparison purposes the
    longer tuple wins ties (so "1.0" < "1.0.1" because (1,0,0) < (1,0,1)).
    """
    if not v:
        return (0,)
    parts: List[int] = []
    for seg in v.strip().split("."):
        digits = "".join(c for c in seg if c.isdigit())
        parts.append(int(digits) if digits else 0)
    return tuple(parts) if parts else (0,)


def version_satisfies_min(installed: str, minimum: str) -> bool:
    """``installed`` version meets or exceeds ``minimum``."""
    if not minimum or minimum == "0.0.0":
        return True
    inst_t = parse_version_tuple(installed or "0.0.0")
    min_t = parse_version_tuple(minimum)
    max_len = max(len(inst_t), len(min_t))
    inst_t = inst_t + (0,) * (max_len - len(inst_t))
    min_t = min_t + (0,) * (max_len - len(min_t))
    return inst_t >= min_t


_parse_version_tuple = parse_version_tuple
_version_satisfies_min = version_satisfies_min


# ---------------------------------------------------------------------------
# Bundle identity + de-duplication
# ---------------------------------------------------------------------------


def app_dependency_index_keys(app: App) -> List[str]:
    """Alias keys used to match ``requires_apps[].key`` against installed Apps."""
    keys: List[str] = []
    name = str(app.name or "").strip()
    if name:
        keys.extend([name, name.casefold(), slug_manifest_key(name)])
    slug = str(getattr(app, "source_profile_slug", None) or "").strip()
    if slug:
        keys.extend([slug, slug.casefold(), slug_manifest_key(slug)])
    lib_id = str(app.installed_from_library_id or "").strip()
    if lib_id:
        keys.append(lib_id)
    seen: set[str] = set()
    out: List[str] = []
    for key in keys:
        if not key or key in seen:
            continue
        seen.add(key)
        out.append(key)
    return out


def bundle_install_rank(app: App) -> tuple:
    """Prefer active installs with the most materialized state."""
    order = {
        "active": 0,
        "paused": 1,
        "awaiting_settings": 2,
        "installing": 3,
    }
    lifecycle = str(getattr(app, "lifecycle_state", "") or "")
    has_workspace = 0 if str(getattr(app, "workspace_id", "") or "") else 1
    return (has_workspace, order.get(lifecycle, 9), app.created_at or "")


def bundle_identity_key(
    source_profile_slug: Optional[str],
    package_meta: Dict[str, Any],
    library_display_name: str = "",
) -> str:
    """Normalized bundle slug used for per-workspace install de-duplication."""
    for candidate in (
        source_profile_slug,
        package_meta.get("slug"),
        package_meta.get("key"),
        library_display_name,
    ):
        key = slug_manifest_key(str(candidate or ""))
        if key:
            return key
    return ""


def app_bundle_identity_keys(app: App) -> set[str]:
    keys: set[str] = set()
    for candidate in (
        getattr(app, "source_profile_slug", None),
        app.name,
    ):
        key = slug_manifest_key(str(candidate or ""))
        if key:
            keys.add(key)
    return keys


def app_matches_bundle_identity(
    app: App,
    identity_key: str,
    library_cp_id: str = "",
) -> bool:
    if not identity_key:
        return False
    lifecycle = str(getattr(app, "lifecycle_state", "") or "")
    if lifecycle == "uninstalled":
        return False
    if library_cp_id and str(app.installed_from_library_id or "") == library_cp_id:
        return True
    return identity_key in app_bundle_identity_keys(app)


async def find_all_bundle_installs(
    workspace_id: str,
    identity_key: str,
    library_cp_id: str = "",
) -> List[App]:
    """All non-uninstalled bundle installs for a workspace identity."""
    matches: List[App] = []
    for app in await App.find({"workspace_id": workspace_id}):
        if app_matches_bundle_identity(app, identity_key, library_cp_id):
            matches.append(app)
    matches.sort(key=bundle_install_rank)
    return matches


async def reparent_tracks_to_keeper(duplicate: App, keeper: App) -> int:
    """Move CONTAINS tracks from duplicate to keeper before duplicate purge.

    For each Track under ``duplicate``, if ``keeper`` does not already
    contain a track with the same ``template_id``, disconnect from
    ``duplicate`` and connect to ``keeper`` via CONTAINS (I-GRAPH-01).
    """
    keeper_tracks = await keeper.nodes(edge=[CONTAINS], node=["Track"])
    keeper_template_ids = {
        str(getattr(t, "template_id", None) or "").strip()
        for t in keeper_tracks
        if str(getattr(t, "template_id", None) or "").strip()
    }

    reparented = 0
    dup_tracks = await duplicate.nodes(edge=[CONTAINS], node=["Track"])
    for track in dup_tracks:
        template_id = str(getattr(track, "template_id", None) or "").strip()
        if template_id and template_id in keeper_template_ids:
            continue

        ctx = await track.get_context()
        if await ctx.find_edges_between(keeper.id, track.id, edge_class=CONTAINS):
            continue

        old_edges = await ctx.find_edges_between(
            duplicate.id, track.id, edge_class=CONTAINS
        )
        for edge in old_edges:
            await edge.delete()

        now = utc_now_iso()
        await keeper.connect(track, edge=CONTAINS, added_at=now)
        if template_id:
            keeper_template_ids.add(template_id)
        reparented += 1

    if reparented:
        logger.info(
            "reparent_tracks_to_keeper: moved %d track(s) from %s to %s",
            reparented,
            duplicate.id,
            keeper.id,
        )
    return reparented


async def purge_duplicate_bundle_installs(
    keeper: App,
    matches: List[App],
    actor_id: str,
) -> int:
    """Force-uninstall every duplicate bundle install except ``keeper``."""
    from app.services.app_lifecycle import uninstall_app

    removed = 0
    for app in matches:
        if app.id == keeper.id:
            continue
        try:
            await reparent_tracks_to_keeper(app, keeper)
            await uninstall_app(
                app_id=app.id,
                actor_id=actor_id,
                force=True,
                archive=True,
            )
            removed += 1
        except Exception:
            logger.exception(
                "purge_duplicate_bundle_installs: failed to remove duplicate %s",
                app.id,
            )
    if removed:
        logger.info(
            "purge_duplicate_bundle_installs: removed %d duplicate(s); kept %s",
            removed,
            keeper.id,
        )
    return removed


async def resolve_canonical_bundle_install(
    workspace_id: str,
    identity_key: str,
    library_cp_id: str,
    actor_id: str,
) -> Optional[App]:
    """Return the canonical install, purging duplicates when needed."""
    matches = await find_all_bundle_installs(workspace_id, identity_key, library_cp_id)
    if not matches:
        return None
    keeper = matches[0]
    if len(matches) > 1:
        await purge_duplicate_bundle_installs(keeper, matches, actor_id)
    return keeper


async def find_existing_bundle_install(
    workspace_id: str,
    source_profile_slug: Optional[str],
    library_cp_id: str,
    *,
    actor_id: str = "",
    library_display_name: str = "",
    package_meta: Optional[Dict[str, Any]] = None,
) -> Optional[App]:
    """Return the canonical bundle install in a workspace, if any."""
    identity_key = bundle_identity_key(
        source_profile_slug,
        package_meta or {},
        library_display_name,
    )
    if not identity_key:
        return None
    return await resolve_canonical_bundle_install(
        workspace_id,
        identity_key,
        library_cp_id,
        actor_id,
    )


async def effective_app_version(app: App) -> str:
    """Resolve semver for ``requires_apps`` checks on legacy + lifecycle installs."""
    explicit = str(app.version or "").strip()
    if explicit and explicit != "0.0.0":
        return explicit
    for lib_id in (
        str(app.installed_from_library_id or "").strip(),
        str(getattr(app, "library_merge_source_id", None) or "").strip(),
    ):
        if not lib_id:
            continue
        lib = await ContentProfile.get(lib_id)
        if not lib:
            continue
        manifest = lib.manifest or {}
        pkg_v = str((manifest.get("package") or {}).get("version") or "").strip()
        if pkg_v:
            return pkg_v
        lib_v = str(getattr(lib, "version", None) or "").strip()
        if lib_v:
            return lib_v
    return explicit or "0.0.0"


async def assert_unique_bundle_install(
    workspace_id: str,
    identity_key: str,
    library_cp_id: str = "",
    *,
    actor_id: str = "",
) -> None:
    """Raise ``BadRequestError`` if an active bundle install already exists."""
    if not identity_key:
        return
    existing = await resolve_canonical_bundle_install(
        workspace_id,
        identity_key,
        library_cp_id,
        actor_id,
    )
    if existing is not None:
        raise BadRequestError(
            message=(f"Bundle {identity_key!r} is already installed in this workspace"),
            details={
                "app_id": existing.id,
                "identity_key": identity_key,
                "workspace_id": workspace_id,
            },
        )


_app_dependency_index_keys = app_dependency_index_keys
_bundle_install_rank = bundle_install_rank
_bundle_identity_key = bundle_identity_key
_app_bundle_identity_keys = app_bundle_identity_keys
_app_matches_bundle_identity = app_matches_bundle_identity
_find_all_bundle_installs = find_all_bundle_installs
_purge_duplicate_bundle_installs = purge_duplicate_bundle_installs
_resolve_canonical_bundle_install = resolve_canonical_bundle_install
_find_existing_bundle_install = find_existing_bundle_install
_effective_app_version = effective_app_version


# ---------------------------------------------------------------------------
# Pre-flight checks
# ---------------------------------------------------------------------------


async def check_requires_apps(
    canonical: Dict[str, Any],
    workspace_id: str,
) -> None:
    """Verify all hard ``requires_apps[]`` deps are installed in workspace.

    Raises ``AppDependencyError`` with ``details.missing_deps`` if any hard
    (``optional: false``) dep is missing OR present at a version below
    ``min_version``. Soft deps generate a log warning but do not block.
    """
    requires = (canonical.get("app") or {}).get("requires_apps") or []
    if not requires:
        return

    existing_apps = await App.find({"workspace_id": workspace_id})
    installs_by_key: Dict[str, List[Tuple[App, str]]] = {}
    for a in existing_apps:
        if a.lifecycle_state != "active":
            continue
        version_str = await effective_app_version(a)
        for key in app_dependency_index_keys(a):
            installs_by_key.setdefault(key, []).append((a, version_str))

    missing: List[Dict[str, Any]] = []
    for dep in requires:
        if not isinstance(dep, dict):
            continue
        dep_key = str(dep.get("key") or "")
        optional = bool(dep.get("optional", False))
        min_version = str(dep.get("min_version") or "0.0.0")
        if not dep_key:
            continue
        candidates = list(installs_by_key.get(dep_key, []))
        if not candidates:
            for alias in (
                dep_key.casefold(),
                slug_manifest_key(dep_key),
            ):
                candidates.extend(installs_by_key.get(alias, []))
        if candidates:
            seen_app_ids: set[str] = set()
            deduped: List[Tuple[App, str]] = []
            for app_node, version_str in candidates:
                if app_node.id in seen_app_ids:
                    continue
                seen_app_ids.add(app_node.id)
                deduped.append((app_node, version_str))
            candidates = deduped
        satisfied = any(version_satisfies_min(v, min_version) for _, v in candidates)
        if satisfied:
            continue
        if optional:
            logger.info(
                "check_requires_apps: soft dep %r missing or below "
                "min_version=%s in workspace %s — proceeding (optional=True)",
                dep_key,
                min_version,
                workspace_id,
            )
            continue
        if candidates:
            missing.append(
                {
                    "key": dep_key,
                    "min_version": min_version,
                    "installed_versions": [v for _, v in candidates],
                    "reason": "version_too_low",
                }
            )
        else:
            missing.append(
                {
                    "key": dep_key,
                    "min_version": min_version,
                    "installed_versions": [],
                    "reason": "not_installed",
                }
            )

    if missing:
        raise AppDependencyError(
            message=(
                f"App install blocked — {len(missing)} hard dependency check(s) "
                f"failed. Install or update dependencies first."
            ),
            details={
                "missing_deps": [m["key"] for m in missing],
                "missing_details": missing,
                "workspace_id": workspace_id,
            },
        )


async def check_cross_app_resolution(
    canonical: Dict[str, Any], workspace_id: str
) -> None:
    """Install-time cross-App resolution gate for relation fields."""
    from app.exceptions import (
        AmbiguousCrossAppTargetError,
        CrossAppTargetNotFoundError,
        CrossWorkspaceTargetRejectedError,
    )

    app_section = canonical.get("app") or {}
    requires_keys = {
        str(d.get("key") or "")
        for d in (app_section.get("requires_apps") or [])
        if isinstance(d, dict)
    }

    tracks = app_section.get("tracks") or []
    for track in tracks:
        if not isinstance(track, dict):
            continue
        for et in track.get("entry_types") or []:
            if not isinstance(et, dict):
                continue
            for field in et.get("fields") or []:
                if not isinstance(field, dict):
                    continue
                if str(field.get("type") or "") != "relation":
                    continue
                rel = field.get("relation") or {}
                if not isinstance(rel, dict):
                    continue
                target_app_key = str(rel.get("target_app") or "").strip()
                if not target_app_key:
                    continue
                resolution = str(rel.get("resolution") or "workspace").strip()
                from app.services.relation_runtime import resolve_target_app

                try:
                    await resolve_target_app(
                        workspace_id=workspace_id,
                        target_app_key=target_app_key,
                        resolution=resolution,
                    )
                except CrossAppTargetNotFoundError:
                    if target_app_key in requires_keys:
                        logger.info(
                            "check_cross_app_resolution: target_app %r is a "
                            "soft requires_apps dep; install proceeds (field "
                            "won't resolve until dep lands)",
                            target_app_key,
                        )
                        continue
                    raise
                except (
                    AmbiguousCrossAppTargetError,
                    CrossWorkspaceTargetRejectedError,
                ):
                    raise


_check_requires_apps = check_requires_apps
_check_cross_app_resolution = check_cross_app_resolution


def apply_schema_defaults(
    settings: Dict[str, Any],
    schema: Dict[str, Any],
) -> Dict[str, Any]:
    """Return ``settings`` with each schema property's ``default`` filled in for
    any key the caller omitted.

    JSON Schema ``default`` is descriptive, not applied by ``jsonschema.validate``
    — so a required property that has a default still fails validation when the
    caller sends ``{}``. Every install entry point that can omit settings (the
    ``seed`` command finalizes with ``{}``; the batch/agent path may pass none;
    the UI seeds client-side but shouldn't have to) must materialize the
    declared defaults before validation, or an app whose required settings ALL
    have defaults (e.g. Content Factory's ``publish_cadence`` / ``target_platforms``)
    can never install without hand-supplied values (June 29 QA #1). Caller
    values always win; only absent keys are filled.
    """
    if not schema:
        return dict(settings or {})
    props = schema.get("properties") or {}
    merged = dict(settings or {})
    for key, spec in props.items():
        if isinstance(spec, dict) and "default" in spec and key not in merged:
            merged[key] = spec["default"]
    return merged


def validate_settings_against_schema(
    settings: Dict[str, Any],
    schema: Dict[str, Any],
) -> None:
    """Validate user-submitted settings against ``settings_schema``."""
    if not schema:
        return
    try:
        import jsonschema  # type: ignore[import-not-found]
    except ImportError:
        logger.warning(
            "validate_settings_against_schema: jsonschema not available — "
            "skipping validation. This is a degraded-mode path; install "
            "``jsonschema`` in production."
        )
        return
    try:
        jsonschema.validate(instance=settings, schema=schema)
    except jsonschema.ValidationError as e:
        raise ContentProfileValidationError(
            message=f"Settings failed schema validation: {e.message}",
            details={
                "path": list(e.absolute_path),
                "schema_path": list(e.absolute_schema_path),
                "validator": e.validator,
            },
        )
    except jsonschema.SchemaError as e:
        raise ContentProfileValidationError(
            message=f"settings_schema itself is invalid: {e.message}",
            details={"schema_path": list(e.absolute_path)},
        )


_validate_settings_against_schema = validate_settings_against_schema


# ---------------------------------------------------------------------------
# Seed planting (idempotent by deterministic seed id per APP-SEEDS-01)
# ---------------------------------------------------------------------------


def manifest_seed_entry_count(canonical: Dict[str, Any]) -> int:
    """Count declared seed entries in a compiled app-scope manifest."""
    count = 0
    for group in (canonical.get("app") or {}).get("seeds") or []:
        if not isinstance(group, dict):
            continue
        entries = group.get("entries") or []
        if isinstance(entries, list):
            count += len(entries)
    return count


def resolve_include_seed_data(
    explicit: Optional[bool] = None,
    *,
    app_node: Optional[Any] = None,
    canonical: Optional[Dict[str, Any]] = None,
) -> bool:
    """Resolve whether install should plant manifest seed entries."""
    if explicit is not None:
        return bool(explicit)
    if app_node is not None:
        meta = getattr(app_node, "metadata", None) or {}
        if isinstance(meta, dict) and INSTALL_INCLUDE_SEED_DATA_KEY in meta:
            return bool(meta[INSTALL_INCLUDE_SEED_DATA_KEY])
    if canonical is not None:
        opts = (canonical.get("app") or {}).get("install_options") or {}
        if isinstance(opts, dict) and "include_seeds_default" in opts:
            return bool(opts["include_seeds_default"])
    return True


def stash_install_include_seed_data(app_node: Any, include_seed_data: bool) -> None:
    """Persist install-time seed preference for awaiting_settings resume."""
    meta = dict(getattr(app_node, "metadata", None) or {})
    meta[INSTALL_INCLUDE_SEED_DATA_KEY] = bool(include_seed_data)
    app_node.metadata = meta


def seed_deterministic_id(
    app_id: str, track_key: str, seed_index: int, seed_key: str = ""
) -> str:
    """Deterministic seed id for idempotency."""
    if seed_key:
        return f"seed:{app_id}:{track_key}:{seed_key}"
    return f"seed:{app_id}:{track_key}:{seed_index}"


def _resolve_seed_refs_in_value(value: Any, seed_key_to_id: Dict[str, str]) -> Any:
    """Replace manifest seed ids (e.g. ``seed_project_contoso``) with entry ids."""
    if isinstance(value, str):
        mapped = seed_key_to_id.get(value.strip())
        return mapped if mapped else value
    if isinstance(value, list):
        return [_resolve_seed_refs_in_value(v, seed_key_to_id) for v in value]
    if isinstance(value, dict):
        return {
            k: _resolve_seed_refs_in_value(v, seed_key_to_id) for k, v in value.items()
        }
    return value


async def _materialize_seed_custom_fields(
    *,
    track: Track,
    type_id: str,
    custom_fields: Dict[str, Any],
    actor_id: str,
    title: str,
    entry: Optional[Entry] = None,
) -> Tuple[Dict[str, Any], List[Dict[str, Any]]]:
    """Run the same validate/materialize path as entry create/update.

    Ensures auto_provision anchors (``details_track``) and typed relation
    edges are wired for manifest seeds — plant_seeds previously wrote raw
    custom_fields and skipped this path.
    """
    from app.models.nodes import EntryType
    from app.services.content_profile_entry_fields import (
        validate_and_materialize_entry_custom_fields,
    )
    from app.services.content_profile_runtime import resolve_track_runtime_profile

    entry_type = await EntryType.get(type_id) if type_id else None
    if entry_type is None:
        return dict(custom_fields or {}), []
    _, runtime_tier, _ = await resolve_track_runtime_profile(track)
    # UI-managed sprint membership mirrors Task.sprint; never persist as a
    # second REFERENCES direction from the sprint node.
    incoming = {k: v for k, v in dict(custom_fields or {}).items() if k != "tasks"}
    return await validate_and_materialize_entry_custom_fields(
        track=track,
        entry_type=entry_type,
        custom_fields=incoming,
        runtime_tier=runtime_tier,
        entry=entry,
        actor_user_id=actor_id,
        actor_kind="human",
        source_entry_title=title,
    )


async def plant_seeds(
    app_node: App,
    canonical: Dict[str, Any],
    actor_id: str,
) -> int:
    """Plant declared seeds on the App's Tracks. Returns count planted."""
    from app.services.content_profile_graph import sync_relation_edges

    seeds = (canonical.get("app") or {}).get("seeds") or []
    if not seeds:
        return 0

    contained_tracks = await app_node.nodes(edge=[CONTAINS], node=["Track"])
    track_by_key: Dict[str, Track] = {}
    for tr in contained_tracks:
        tid = getattr(tr, "template_id", None) or ""
        if tid:
            track_by_key[str(tid).casefold()] = tr
        title_fold = tr.title_fold or tr.title.casefold()
        track_by_key.setdefault(title_fold, tr)

    # Manifest seed id → live Entry.id (earlier groups resolve before
    # later groups that reference them by seed id).
    seed_key_to_id: Dict[str, str] = {}
    planted = 0
    for seed_group in seeds:
        if not isinstance(seed_group, dict):
            continue
        track_key = str(seed_group.get("track") or "").strip()
        if not track_key:
            continue
        target_track = track_by_key.get(track_key.casefold())
        if target_track is None:
            logger.warning(
                "plant_seeds: app %s seed track %r not found among "
                "contained tracks (%s) — skipping",
                app_node.id,
                track_key,
                sorted(track_by_key.keys()),
            )
            continue
        entries = seed_group.get("entries") or []
        if not isinstance(entries, list):
            continue
        for idx, entry_spec in enumerate(entries):
            if not isinstance(entry_spec, dict):
                continue
            seed_key = str(entry_spec.get("id") or "").strip()
            det_id = seed_deterministic_id(app_node.id, track_key, idx, seed_key)
            existing = await Entry.find(
                {
                    "track_id": target_track.id,
                    "custom_fields.__seed_id": det_id,
                }
            )
            if existing:
                type_id = await resolve_seed_entry_type_id(
                    target_track, track_key, canonical, entry_spec
                )
                ent = existing[0]
                if seed_key:
                    seed_key_to_id[seed_key] = ent.id
                if type_id and (not ent.type_id or ent.type_id != type_id):
                    ent.type_id = type_id
                    ent.updated_at = utc_now_iso()
                    await ent.save()
                # Heal install seeds that skipped auto_provision / relation wire.
                # Domain-agnostic: re-materialize when an auto-provision track
                # anchor is missing, or when the seed declares a relation the
                # stored entry never received.
                cf = dict(ent.custom_fields or {})
                seed_cf = dict(entry_spec.get("custom_fields") or {})
                needs_anchor = not str(cf.get("details_track") or "").strip()
                needs_relation = False
                for rel_key, rel_val in seed_cf.items():
                    if rel_val is None or rel_val == "" or rel_val == []:
                        continue
                    if cf.get(rel_key) in (None, "", []):
                        needs_relation = True
                        break
                if (needs_anchor or needs_relation) and type_id:
                    try:
                        merge_cf = dict(entry_spec.get("custom_fields") or {})
                        merge_cf = _resolve_seed_refs_in_value(merge_cf, seed_key_to_id)
                        if not isinstance(merge_cf, dict):
                            merge_cf = {}
                        # Preserve existing fields; only fill gaps.
                        for k, v in cf.items():
                            if k not in merge_cf or merge_cf.get(k) in (None, "", []):
                                merge_cf[k] = v
                        merge_cf["__seed_id"] = det_id
                        validated, relation_refs = (
                            await _materialize_seed_custom_fields(
                                track=target_track,
                                type_id=type_id,
                                custom_fields=merge_cf,
                                actor_id=actor_id,
                                title=str(ent.title or ""),
                                entry=ent,
                            )
                        )
                        ent.custom_fields = validated
                        ent.updated_at = utc_now_iso()
                        await ent.save()
                        await sync_relation_edges(
                            source_entry=ent, relation_refs=relation_refs
                        )
                    except Exception:
                        logger.exception(
                            "plant_seeds: heal failed for existing seed %s",
                            getattr(ent, "id", ""),
                        )
                continue
            type_id = await resolve_seed_entry_type_id(
                target_track,
                track_key,
                canonical,
                entry_spec,
                require=True,
            )
            now = utc_now_iso()
            title = str(entry_spec.get("title") or "")
            body = str(entry_spec.get("body") or "")
            tags = list(entry_spec.get("tags") or [])
            cf = dict(entry_spec.get("custom_fields") or {})
            cf = _resolve_seed_refs_in_value(cf, seed_key_to_id)
            if not isinstance(cf, dict):
                cf = {}
            cf["__seed_id"] = det_id
            try:
                validated, relation_refs = await _materialize_seed_custom_fields(
                    track=target_track,
                    type_id=type_id,
                    custom_fields=cf,
                    actor_id=actor_id,
                    title=title,
                    entry=None,
                )
            except Exception:
                logger.exception(
                    "plant_seeds: materialize failed for %s on track %s; "
                    "planting raw custom_fields",
                    seed_key or title,
                    track_key,
                )
                validated, relation_refs = cf, []
            new_entry = await Entry.create(
                title=title,
                body=body,
                tags=tags,
                custom_fields=validated,
                type_id=type_id,
                track_id=target_track.id,
                author_id=actor_id,
                status="active",
                created_at=now,
                updated_at=now,
            )
            await target_track.connect(new_entry, edge=CONTAINS, added_at=now)
            if relation_refs:
                try:
                    await sync_relation_edges(
                        source_entry=new_entry, relation_refs=relation_refs
                    )
                except Exception:
                    logger.exception(
                        "plant_seeds: relation sync failed for entry %s",
                        new_entry.id,
                    )
            if seed_key:
                seed_key_to_id[seed_key] = new_entry.id
            planted += 1
            # Seeded entries otherwise never reach a bundle's own entry.create
            # hooks (this loop calls Entry.create directly, not the normal
            # create-entry path that dispatches them) — a seeded row would
            # silently skip a side effect (e.g. auto-linking to a lookup
            # record) that the identical entry would get if a user created
            # it by hand. Best-effort/swallowed, same as every other hook
            # dispatch — a seed's own success must never hinge on it.
            try:
                from app.services.hooks.entry_save_runtime import (
                    run_entry_save_hooks,
                )

                await run_entry_save_hooks(
                    entry=new_entry,
                    workspace_id=app_node.workspace_id,
                    actor_id=actor_id,
                    hook_point="entry.create",
                )
            except Exception:  # noqa: BLE001
                logger.exception(
                    "plant_seeds: entry.create hooks failed for seeded entry "
                    "%s (app=%s)",
                    new_entry.id,
                    app_node.id,
                )
    logger.info(
        "plant_seeds: app %s planted %d seed entries",
        app_node.id,
        planted,
    )
    return planted


async def plant_seeds_for_install(
    app_node: App,
    canonical: Dict[str, Any],
    actor_id: str,
    *,
    include_seed_data: bool,
) -> int:
    """Plant manifest seeds (+ package ``seeds/post_install``) when install opts in."""
    if not include_seed_data:
        return 0
    planted = await plant_seeds(app_node, canonical, actor_id)
    from app.services.bundle_post_seed import run_bundle_post_seed

    planted += await run_bundle_post_seed(app_node, actor_id)
    return planted


async def unplant_seeds(app_id: str) -> int:
    """Delete all seed-planted entries for an App. Best-effort compensation."""
    try:
        prefix = f"seed:{app_id}:"
        seeded = await Entry.find({"custom_fields.__seed_id": {"$regex": f"^{prefix}"}})
    except Exception:
        seeded = []
        app_node = await App.get(app_id)
        if app_node:
            tracks = await app_node.nodes(edge=[CONTAINS], node=["Track"])
            for tr in tracks:
                entries = await Entry.find({"track_id": tr.id})
                for e in entries:
                    sid = (e.custom_fields or {}).get("__seed_id", "")
                    if isinstance(sid, str) and sid.startswith(f"seed:{app_id}:"):
                        seeded.append(e)
    from app.services.entry_deletion import delete_entry_fast

    count = 0
    for entry in seeded:
        try:
            await delete_entry_fast(entry)
            count += 1
        except Exception:
            logger.exception(
                "unplant_seeds: delete failed for entry %s", getattr(entry, "id", "")
            )
    return count


_manifest_seed_entry_count = manifest_seed_entry_count
_resolve_include_seed_data = resolve_include_seed_data
_stash_install_include_seed_data = stash_install_include_seed_data
_seed_deterministic_id = seed_deterministic_id
_plant_seeds = plant_seeds
_plant_seeds_for_install = plant_seeds_for_install
_unplant_seeds = unplant_seeds


# ---------------------------------------------------------------------------
# Track materialization helpers
# ---------------------------------------------------------------------------


async def materialize_tracks_for_app(
    app_node: App,
    actor_id: str,
) -> List[Track]:
    """Materialize Tracks declared in the App's manifest (provision_on_create)."""
    from app.services.content_profile_merge import (
        provision_prescribed_tracks_from_app_manifest,
    )

    pre_existing = {t.id for t in await app_node.nodes(edge=[CONTAINS], node=["Track"])}
    await provision_prescribed_tracks_from_app_manifest(app_node, actor_id)
    post: List[Track] = await app_node.nodes(edge=[CONTAINS], node=["Track"])
    return [t for t in post if t.id not in pre_existing]


async def delete_tracks(tracks: List[Track]) -> int:
    """Compensation: delete a list of Tracks. Best-effort."""
    from app.services.app_deletion import delete_track_and_nested_content

    count = 0
    for tr in tracks:
        try:
            await delete_track_and_nested_content(tr)
            count += 1
        except Exception:
            logger.exception(
                "delete_tracks: delete failed for track %s", getattr(tr, "id", "")
            )
    return count


_materialize_tracks_for_app = materialize_tracks_for_app
_delete_tracks = delete_tracks
