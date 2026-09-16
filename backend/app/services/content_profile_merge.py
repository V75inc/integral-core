"""Merge library manifests or track templates into attached ContentProfiles."""

from __future__ import annotations

import copy
import logging
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Protocol, Set

from jvspatial.api.exceptions import ResourceNotFoundError

from app.exceptions import BadRequestError, CustomSkillPublicCatalogRejectedError
from app.models.edges import CATALOGS, CONTAINS, DEFINES_TRACK_PROFILE, OWNS
from app.models.nodes import (
    App,
    ContentProfile,
    EntryType,
    Tag,
    Track,
    View,
)
from app.services.app_graph import (
    catalog_track,
    catalog_view_under_track,
    ensure_catalog_edge,
    get_app_attached_content_profile,
    get_or_create_views_registry_for_content_profile,
    get_track_attached_content_profile,
)
from app.services.content_profile_runtime import (
    SCHEMA_VERSION,
    app_manifest_provisioning_enabled,
    compile_canonical_manifest,
    materialize_view_config_from_spec,
    normalize_entry_type_form_schema,
    normalize_view_config,
    seed_taxonomy_for_track,
    slug_manifest_key,
    synchronize_track_view_default_flags,
)
from app.services.permissions import get_user_node

logger = logging.getLogger(__name__)


def _merge_v2_keyed_list(
    target: List[Dict[str, Any]],
    incoming: List[Dict[str, Any]],
    *,
    key_field: str = "key",
) -> List[Dict[str, Any]]:
    """Merge two v2 keyed-list sections with key-based dedup; incoming wins.

    Phase 10 Plan 10-03 — used for skills/agents/seeds/requires_apps merge.
    Preserves target-only entries, replaces collisions with incoming spec,
    appends incoming-only entries in source order.
    """
    by_key: Dict[str, Dict[str, Any]] = {}
    order: List[str] = []
    for spec in list(target or []) + list(incoming or []):
        if not isinstance(spec, dict):
            continue
        k = str(spec.get(key_field) or "")
        if not k:
            continue
        if k not in by_key:
            order.append(k)
        by_key[k] = spec
    return [by_key[k] for k in order]


def _form_schema_from_entry_type_spec(spec: Dict[str, Any]) -> Dict[str, Any]:
    """Merge manifest ``form_schema`` with top-level ``fields`` / ``base_fields`` / ``required_tag_groups``."""
    raw_fs = spec.get("form_schema")
    if isinstance(raw_fs, dict) and raw_fs:
        merged = dict(raw_fs)
        if "fields" not in merged and spec.get("fields") is not None:
            merged["fields"] = list(spec.get("fields") or [])
        if "base_fields" not in merged and spec.get("base_fields") is not None:
            merged["base_fields"] = spec.get("base_fields")
        if (
            "required_tag_groups" not in merged
            and spec.get("required_tag_groups") is not None
        ):
            merged["required_tag_groups"] = list(spec.get("required_tag_groups") or [])
        if "related_views" not in merged and spec.get("related_views") is not None:
            merged["related_views"] = list(spec.get("related_views") or [])
        if "open_as_page" not in merged and spec.get("open_as_page") is not None:
            merged["open_as_page"] = bool(spec.get("open_as_page"))
        if "create_wizard" not in merged and spec.get("create_wizard") is not None:
            merged["create_wizard"] = spec.get("create_wizard")
        if spec.get("key") and "_manifest_entry_type_key" not in merged:
            merged["_manifest_entry_type_key"] = str(spec.get("key") or "")
        return normalize_entry_type_form_schema(merged)
    out: Dict[str, Any] = {"fields": list(spec.get("fields") or [])}
    if spec.get("key"):
        out["_manifest_entry_type_key"] = str(spec.get("key") or "")
    if spec.get("base_fields") is not None:
        out["base_fields"] = spec.get("base_fields")
    if spec.get("required_tag_groups") is not None:
        out["required_tag_groups"] = list(spec.get("required_tag_groups") or [])
    if spec.get("related_views") is not None:
        out["related_views"] = list(spec.get("related_views") or [])
    if spec.get("open_as_page") is not None:
        out["open_as_page"] = bool(spec.get("open_as_page"))
    if spec.get("create_wizard") is not None:
        out["create_wizard"] = spec.get("create_wizard")
    return normalize_entry_type_form_schema(out)


def merge_entry_type_schema_from_spec(
    cur_norm: Dict[str, Any],
    desired_schema: Dict[str, Any],
    *,
    spec_key: str = "",
) -> tuple[Dict[str, Any], bool]:
    """Merge manifest entry-type fields into an existing normalized form_schema.

    Backfills ``_manifest_entry_type_key``, ``base_fields``, and any missing
    custom fields (by ``key``). Existing fields with the same key are shallow-
    merged so enum/column metadata can advance on library update.
    """
    changed = False
    out = dict(cur_norm)
    if spec_key and not out.get("_manifest_entry_type_key"):
        out["_manifest_entry_type_key"] = spec_key
        changed = True
    des_bf = desired_schema.get("base_fields") or {}
    cur_bf = out.get("base_fields") or {}
    if desired_schema.get("base_fields") is not None and cur_bf != des_bf:
        out["base_fields"] = des_bf
        changed = True
    cur_fields = [f for f in list(out.get("fields") or []) if isinstance(f, dict)]
    des_fields = [
        f for f in list(desired_schema.get("fields") or []) if isinstance(f, dict)
    ]
    by_key: Dict[str, Dict[str, Any]] = {}
    order: List[str] = []
    for f in cur_fields:
        fk = str(f.get("key") or "").strip()
        if not fk or fk in by_key:
            continue
        by_key[fk] = dict(f)
        order.append(fk)
    for df in des_fields:
        dk = str(df.get("key") or "").strip()
        if not dk:
            continue
        if dk not in by_key:
            by_key[dk] = dict(df)
            order.append(dk)
            changed = True
            continue
        merged = {**by_key[dk], **df}
        if merged != by_key[dk]:
            by_key[dk] = merged
            changed = True
    if changed:
        out["fields"] = [by_key[k] for k in order if k in by_key]
    return normalize_entry_type_form_schema(out), changed


class _LibraryManifestSource(Protocol):
    """Anything that exposes ``manifest`` for merge (ContentProfile or in-memory shim)."""

    manifest: Dict[str, Any]


def _merge_package_meta(
    target_pkg: Dict[str, Any], incoming_pkg: Dict[str, Any]
) -> Dict[str, Any]:
    merged = {**target_pkg, **incoming_pkg}
    tgt_deps = list(target_pkg.get("dependencies") or [])
    inc_deps = list(incoming_pkg.get("dependencies") or [])
    dep_by_id: Dict[str, Dict[str, Any]] = {}
    for dep in tgt_deps + inc_deps:
        if not isinstance(dep, dict):
            continue
        dep_id = str(dep.get("id") or "")
        if dep_id:
            dep_by_id[dep_id] = dep
    merged["dependencies"] = list(dep_by_id.values())
    tgt_caps = list(target_pkg.get("capabilities") or [])
    inc_caps = list(incoming_pkg.get("capabilities") or [])
    cap_by_type: Dict[str, Dict[str, Any]] = {}
    for cap in tgt_caps + inc_caps:
        if isinstance(cap, str):
            ctype = cap
            cap = {"type": ctype, "config": {}}
        if not isinstance(cap, dict):
            continue
        ctype = str(cap.get("type") or "")
        if ctype:
            cap_by_type[ctype] = cap
    merged["capabilities"] = list(cap_by_type.values())
    return merged


def _append_applied_migrations(
    target_manifest: Dict[str, Any],
    *,
    canonical: Dict[str, Any],
    source_id: str,
) -> Dict[str, Any]:
    out = copy.deepcopy(target_manifest)
    existing = list(out.get("applied_migrations") or [])
    seen = {
        f"{str(m.get('source_id') or '')}:{str(m.get('from_version') or '')}:{str(m.get('to_version') or '')}"
        for m in existing
        if isinstance(m, dict)
    }
    now = datetime.now(timezone.utc).isoformat()
    for mig in list(canonical.get("migrations") or []):
        if not isinstance(mig, dict):
            continue
        key = f"{source_id}:{str(mig.get('from_version') or '')}:{str(mig.get('to_version') or '')}"
        if key in seen:
            continue
        seen.add(key)
        existing.append(
            {
                "source_id": source_id,
                "from_version": str(mig.get("from_version") or ""),
                "to_version": str(mig.get("to_version") or ""),
                "applied_at": now,
            }
        )
    out["applied_migrations"] = existing
    return out


def _merge_track_taxonomy(
    target_taxonomy: Dict[str, Any], incoming_taxonomy: Dict[str, Any]
) -> Dict[str, Any]:
    groups: Dict[str, Dict[str, Any]] = {}
    for raw_group in list(target_taxonomy.get("tag_groups") or []) + list(
        incoming_taxonomy.get("tag_groups") or []
    ):
        if not isinstance(raw_group, dict):
            continue
        gkey = str(raw_group.get("key") or "")
        if not gkey:
            continue
        current = groups.get(gkey, {"key": gkey, "name": raw_group.get("name") or gkey})
        tag_by_key: Dict[str, Dict[str, Any]] = {
            str(t.get("key") or ""): t
            for t in list(current.get("tags") or [])
            if isinstance(t, dict) and str(t.get("key") or "")
        }
        for tag in list(raw_group.get("tags") or []):
            if not isinstance(tag, dict):
                continue
            tkey = str(tag.get("key") or "")
            if not tkey:
                continue
            tag_by_key[tkey] = tag
        current["tags"] = list(tag_by_key.values())
        groups[gkey] = current
    return {"tag_groups": list(groups.values())}


def _merge_track_tier_into_manifest(
    target_manifest: Dict[str, Any], canonical: Dict[str, Any], tier: Dict[str, Any]
) -> Dict[str, Any]:
    target = compile_canonical_manifest(manifest=target_manifest or {})
    target_tier = target.get("track") or {}
    from app.services.content_profile_runtime import _slug

    # The incoming tier's suppress_feed_fallback intent is authoritative —
    # computed up front so the view union below can honor it, not just the
    # final re-compile (see the field below: suppress:true alone doesn't
    # strip an ALREADY-unioned "feed" view, it only skips adding a new one).
    incoming_suppresses_feed = bool(
        tier.get(
            "suppress_feed_fallback", target_tier.get("suppress_feed_fallback", False)
        )
    )

    entry_by_key: Dict[str, Dict[str, Any]] = {}
    for spec in list(target_tier.get("entry_types") or []) + list(
        tier.get("entry_types") or []
    ):
        if not isinstance(spec, dict):
            continue
        key = _slug(str(spec.get("key") or spec.get("name") or ""))
        if key:
            entry_by_key[key] = spec
    target_views = list(target_tier.get("views") or [])
    target_defaults = dict(target_tier.get("defaults") or {})
    if incoming_suppresses_feed:
        # target_tier came from compiling target_manifest fresh (line above)
        # — if that raw manifest itself never declared suppress_feed_fallback
        # (e.g. it predates this fix, or is a generic non-templated
        # bootstrap), compiling it just invented a default Feed view (and
        # pointed defaults.default_view at it) that has no business
        # surviving into a merge whose incoming tier explicitly doesn't
        # want one — dropping the view without also dropping the now-
        # dangling default_view reference would just trade one validation
        # failure for another.
        target_views = [
            v
            for v in target_views
            if str((v or {}).get("view_type") or "").strip().lower() != "feed"
        ]
        if str(target_defaults.get("default_view") or "") == "feed":
            target_defaults.pop("default_view", None)
    view_by_key: Dict[str, Dict[str, Any]] = {}
    for spec in target_views + list(tier.get("views") or []):
        if not isinstance(spec, dict):
            continue
        key = str(spec.get("key") or spec.get("name") or "")
        if key:
            view_by_key[key] = spec
    merged = {
        "content_profile_schema_version": SCHEMA_VERSION,
        "scope": "track",
        "track": {
            "entry_types": list(entry_by_key.values()),
            "views": list(view_by_key.values()),
            "taxonomy": _merge_track_taxonomy(
                target_tier.get("taxonomy") or {}, tier.get("taxonomy") or {}
            ),
            "defaults": {
                **target_defaults,
                **(tier.get("defaults") or {}),
            },
            # The incoming tier's own declared intent wins — a manifest
            # update turning suppression on/off must take effect even if
            # the target's prior compiled state disagreed. Falls back to
            # the target's existing value only when the incoming tier is
            # silent on it (e.g. a merge call that never compiled it, as
            # opposed to one that just doesn't want it suppressed).
            "suppress_feed_fallback": incoming_suppresses_feed,
        },
        "package": _merge_package_meta(
            target.get("package") or {}, canonical.get("package") or {}
        ),
        "migrations": list(target.get("migrations") or [])
        + list(canonical.get("migrations") or []),
    }
    out = compile_canonical_manifest(manifest=merged)
    # compile_canonical_manifest drops non-schema keys. Preserve the
    # space_track_template discriminator so by-reference anchor reuse
    # (_resolve_or_create_template_content_profile) keeps matching.
    prior_mat = (
        target_manifest.get("materialization")
        if isinstance(target_manifest, dict)
        else None
    )
    if isinstance(prior_mat, dict) and prior_mat:
        out["materialization"] = dict(prior_mat)
    return out


async def _resolve_library_dependency_chain(
    library_cp: ContentProfile,
) -> List[ContentProfile]:
    resolved: List[ContentProfile] = []
    visiting: Set[str] = set()
    visited: Set[str] = set()

    async def _lookup(dep_id: str) -> Optional[ContentProfile]:
        dep = await ContentProfile.get(dep_id)
        if dep and getattr(dep, "library_package", False):
            return dep
        packs = await ContentProfile.find({"context.library_package": True})
        want = dep_id.strip().lower()
        for pkg in packs:
            manifest = compile_canonical_manifest(manifest=pkg.manifest or {})
            pm = manifest.get("package") or {}
            candidates = {
                str(pm.get("id") or "").strip().lower(),
                str(pm.get("name") or "").strip().lower(),
                str(pkg.id or "").strip().lower(),
            }
            if want in candidates:
                return pkg
        return None

    async def _visit(cp: ContentProfile) -> None:
        if cp.id in visited:
            return
        if cp.id in visiting:
            raise BadRequestError(message="Circular profile dependency detected")
        visiting.add(cp.id)
        canonical = compile_canonical_manifest(manifest=cp.manifest or {})
        deps = list((canonical.get("package") or {}).get("dependencies") or [])
        for dep in deps:
            if not isinstance(dep, dict):
                continue
            dep_id = str(dep.get("id") or "").strip()
            if not dep_id:
                continue
            dep_cp = await _lookup(dep_id)
            if not dep_cp:
                raise BadRequestError(
                    message=f"Missing dependency '{dep_id}' for content profile '{cp.id}'"
                )
            await _visit(dep_cp)
        visiting.remove(cp.id)
        visited.add(cp.id)
        resolved.append(cp)

    await _visit(library_cp)
    return resolved


async def verify_track_template_in_app(app_id: str, template_id: str) -> ContentProfile:
    """Ensure ``template_id`` is a DEFINES_TRACK_PROFILE child of the App attached CP."""
    sp = await App.get(app_id)
    if not sp:
        raise ResourceNotFoundError(message="App not found")
    sacp = await get_app_attached_content_profile(sp)
    if not sacp:
        raise BadRequestError(message="App has no attached content profile")
    templates = await sacp.nodes(edge=[DEFINES_TRACK_PROFILE], node=["ContentProfile"])
    ids = {t.id for t in templates}
    if template_id not in ids:
        raise BadRequestError(
            message="Template is not defined for this app_node",
        )
    t = await ContentProfile.get(template_id)
    if not t:
        raise ResourceNotFoundError(message="Template not found")
    return t


async def merge_template_content_profile_into_track(
    template_cp: ContentProfile,
    track: Track,
) -> None:
    """Clone CONTAINS EntryType/Tag and cataloged Views from template onto track CP."""
    tcp = await get_track_attached_content_profile(track)
    if not tcp:
        raise BadRequestError(message="Track has no attached content profile")
    now = datetime.now(timezone.utc).isoformat()

    for et in await template_cp.nodes(edge=[CONTAINS], node=["EntryType"]):
        new_et = await EntryType.create(
            name=et.name,
            icon=getattr(et, "icon", None) or "document",
            form_schema=copy.deepcopy(et.form_schema) if et.form_schema else {},
            track_id=track.id,
            is_template=False,
            created_at=now,
            updated_at=now,
        )
        await tcp.connect(new_et, edge=CONTAINS, added_at=now)

    for tag in await template_cp.nodes(edge=[CONTAINS], node=["Tag"]):
        new_tag = await Tag.create(
            name=tag.name,
            color=getattr(tag, "color", None) or "#6B7280",
            track_id=track.id,
            group_key=getattr(tag, "group_key", None),
            aliases=list(getattr(tag, "aliases", None) or []),
            parent_tag_id=getattr(tag, "parent_tag_id", None),
            applies_to_entry_types=list(
                getattr(tag, "applies_to_entry_types", None) or []
            ),
            is_template=False,
            created_at=now,
        )
        await tcp.connect(new_tag, edge=CONTAINS, added_at=now)

    tvreg = await get_or_create_views_registry_for_content_profile(
        template_cp, track=None
    )
    for v in await tvreg.nodes(edge=[CATALOGS], node=["View"]):
        new_v = await View.create(
            name=v.name,
            type=getattr(v, "type", None) or "feed",
            config=normalize_view_config(
                getattr(v, "type", None) or "feed",
                copy.deepcopy(v.config) if v.config else {},
            ),
            track_id=track.id,
            content_profile_id=tcp.id,
            entry_type_keys=list(getattr(v, "entry_type_keys", None) or []),
            default_entry_type_key=str(getattr(v, "default_entry_type_key", "") or ""),
            is_template=False,
            is_default=bool(getattr(v, "is_default", False)),
            hidden=bool(getattr(v, "hidden", False)),
            created_by=getattr(v, "created_by", None) or (track.owner_id or ""),
            created_at=now,
            updated_at=now,
        )
        await catalog_view_under_track(track, new_v)

    canon = compile_canonical_manifest(manifest=dict(template_cp.manifest or {}))
    if canon.get("scope") == "track":
        tier = canon.get("track") or {}
        tcp.manifest = _merge_track_tier_into_manifest(tcp.manifest or {}, canon, tier)
        tcp.updated_at = now
        await tcp.save()

    await synchronize_track_view_default_flags(track)


def _tier_dict(
    manifest: Dict[str, Any], *, for_space: bool, track_type_key: Optional[str] = None
) -> Dict[str, Any]:
    canonical = compile_canonical_manifest(manifest=manifest)
    scope = canonical.get("scope")
    if scope == "track":
        return canonical.get("track") or {}
    if scope == "app":
        app_node = canonical.get("app") or {}
        tracks = list(app_node.get("tracks") or [])
        if not tracks:
            return {}
        if track_type_key:
            for track_spec in tracks:
                if str(track_spec.get("key") or "") == track_type_key:
                    return track_spec
        return tracks[0]
    return {}


async def materialize_app_relations(
    app_cp: ContentProfile,
    canonical: Dict[str, Any],
) -> None:
    """Materialize REFERENCES edges for App-level relations.

    For each relation declared in ``app.relations[]``, resolve the source
    and target track types to actual Tracks within the app_node, then inject a
    ``relation`` field into the matching source EntryType form schemas.
    """
    app_spec = canonical.get("app") or {}
    relations: List[Dict[str, Any]] = list(app_spec.get("relations") or [])
    if not relations:
        return

    # Resolve the owning App from the attached content profile
    app_id = getattr(app_cp, "app_id", None) or ""
    if not app_id:
        return
    app_node = await App.get(app_id)
    if not app_node:
        return

    # Collect all tracks within the app_node, keyed by template_id for lookup
    space_tracks: List[Track] = await app_node.nodes(edge=["CONTAINS"], node=["Track"])
    tracks_by_type_key: Dict[str, Track] = {}
    for t in space_tracks:
        tmpl = str(getattr(t, "template_id", "") or "").strip()
        if tmpl:
            tracks_by_type_key[slug_manifest_key(tmpl)] = t

    now = datetime.now(timezone.utc).isoformat()

    for rel in relations:
        source_key = str(rel.get("source_track_type") or "").strip()
        target_key = str(rel.get("target_track_type") or "").strip()
        rel_key = str(rel.get("key") or "").strip()
        if not source_key or not target_key:
            continue

        source_track = tracks_by_type_key.get(slug_manifest_key(source_key))
        target_track = tracks_by_type_key.get(slug_manifest_key(target_key))
        if not source_track or not target_track:
            continue

        # Get the track-attached content profiles and their EntryTypes
        source_tcp = await get_track_attached_content_profile(source_track)
        target_tcp = await get_track_attached_content_profile(target_track)
        if not source_tcp or not target_tcp:
            continue

        target_entry_types: List[EntryType] = await target_tcp.nodes(
            edge=[CONTAINS], node=["EntryType"]
        )
        # Build list of target entry type keys/names for the relation field
        target_et_keys: List[str] = []
        for tet in target_entry_types:
            et_key = slug_manifest_key(str(getattr(tet, "name", "") or ""))
            if et_key:
                target_et_keys.append(et_key)

        source_entry_types: List[EntryType] = await source_tcp.nodes(
            edge=[CONTAINS], node=["EntryType"]
        )
        field_key = rel_key or slug_manifest_key(f"rel_{source_key}_to_{target_key}")
        field_name = str(rel.get("name") or field_key)

        for src_et in source_entry_types:
            schema = src_et.form_schema or {}
            fields = list(schema.get("fields") or [])
            # Skip if a relation field for this key already exists
            if any(
                str(f.get("key") or "") == field_key
                and str(f.get("type") or "") == "relation"
                for f in fields
                if isinstance(f, dict)
            ):
                continue

            relation_field: Dict[str, Any] = {
                "key": field_key,
                "name": field_name,
                "type": "relation",
                "required": False,
                "readonly": False,
                "default": None,
                "enum": [],
                "relation": {
                    "target_entry_types": target_et_keys,
                    "target_track_types": [slug_manifest_key(target_key)],
                    "allow_cross_track": True,
                    "many": bool(rel.get("many", False)),
                    "inverse_field": rel.get("inverse_field"),
                },
            }
            fields.append(relation_field)
            schema["fields"] = fields
            src_et.form_schema = normalize_entry_type_form_schema(schema)
            src_et.updated_at = now
            await src_et.save()


async def merge_library_manifest_into_content_profile(
    library_cp: _LibraryManifestSource,
    target_cp: ContentProfile,
    track: Optional[Track],
    *,
    for_space: bool = False,
    _skip_dependencies: bool = False,
) -> None:
    """Apply ``library_cp.manifest`` tier into ``target_cp`` (never mutates library)."""
    if not _skip_dependencies and isinstance(library_cp, ContentProfile):
        chain = await _resolve_library_dependency_chain(library_cp)
        for dep in chain[:-1]:
            await merge_library_manifest_into_content_profile(
                dep,
                target_cp,
                track,
                for_space=for_space,
                _skip_dependencies=True,
            )
    manifest = library_cp.manifest or {}
    canonical = compile_canonical_manifest(manifest=manifest)
    source_id = str(getattr(library_cp, "id", "") or "")

    # Phase 10 Plan 10-04 — merge-time mirror of the compile-time public-catalog
    # ``kind: custom`` rejection (Architectural Decision 6 — belt-and-suspenders).
    # The compile-time gate at ``compile_canonical_manifest(is_public_catalog=True)``
    # is the primary defense; this MERGE-time check ensures that even when the
    # library ContentProfile was compiled with ``is_public_catalog=False`` (e.g.
    # imported by a non-marketplace pathway), a manifest carrying
    # ``package.publisher_tier == "public_catalog"`` cannot reach an attached
    # ContentProfile if any declared skill is ``kind: custom``.
    pkg_meta = (canonical.get("package") or {}) if isinstance(canonical, dict) else {}
    publisher_tier = str(pkg_meta.get("publisher_tier") or "").strip().lower()
    if publisher_tier == "public_catalog":
        # Inspect both scope-level skill lists — app.skills (app-scope) and
        # track.skills (track-scope per app_bundles_v1.md §4.3).
        canonical_app = (
            (canonical.get("app") or {}) if isinstance(canonical, dict) else {}
        )
        canonical_track = (
            (canonical.get("track") or {}) if isinstance(canonical, dict) else {}
        )
        candidate_skills: List[Dict[str, Any]] = []
        candidate_skills.extend(list(canonical_app.get("skills") or []))
        candidate_skills.extend(list(canonical_track.get("skills") or []))
        custom_keys = [
            str(sk.get("key") or "")
            for sk in candidate_skills
            if isinstance(sk, dict) and str(sk.get("kind") or "").lower() == "custom"
        ]
        if custom_keys:
            raise CustomSkillPublicCatalogRejectedError(
                message=(
                    "Skills with kind: custom are not permitted in the public "
                    f"catalog (skill keys: {custom_keys}). Use kind: declarative "
                    "or publish via a trusted-partner / first-party deployment. "
                    "See app_bundles_v1.md §11."
                ),
                details={
                    "violating_skill_keys": custom_keys,
                    "publisher_tier": publisher_tier,
                    "source_id": source_id,
                },
            )

    if for_space and canonical.get("scope") == "app" and track is None:
        # App-level libraries merge their full manifest into the attached app_node profile.
        target_manifest = compile_canonical_manifest(manifest=target_cp.manifest or {})
        merged_tracks: Dict[str, Dict[str, Any]] = {}
        for spec in list(
            ((target_manifest.get("app") or {}).get("tracks") or [])
            + ((canonical.get("app") or {}).get("tracks") or [])
        ):
            key = str(spec.get("key") or "")
            if key:
                merged_tracks[key] = spec
        merged_relations = list(
            (target_manifest.get("app") or {}).get("relations") or []
        )
        for rel in list((canonical.get("app") or {}).get("relations") or []):
            key = str(rel.get("key") or "")
            if key and any(str(r.get("key") or "") == key for r in merged_relations):
                continue
            merged_relations.append(rel)
        merged_track_list = list(merged_tracks.values())
        merged_templates: Dict[str, Dict[str, Any]] = {}
        for spec in list(
            ((target_manifest.get("app") or {}).get("track_templates") or [])
            + ((canonical.get("app") or {}).get("track_templates") or [])
        ):
            key = str(spec.get("key") or "")
            if key:
                merged_templates[key] = spec
        merged_template_list = list(merged_templates.values())
        merged_defaults = {
            **((target_manifest.get("app") or {}).get("defaults") or {}),
            **((canonical.get("app") or {}).get("defaults") or {}),
        }
        if merged_track_list and "provision_prescribed_tracks" not in merged_defaults:
            merged_defaults["provision_prescribed_tracks"] = True
        # Phase 10 Plan 10-03 — merge the v2 operational-layer sections
        # (skills/agents/settings_schema/seeds/permissions/requires_apps).
        # Strategy:
        #   - skills, agents, seeds, requires_apps: list merge with
        #     key-based dedup; later (canonical) wins on key collision.
        #   - settings_schema, permissions: shallow dict replacement;
        #     canonical wins entirely (these are app-level singletons).
        # Public-catalog rejection of kind:custom skills lands in Plan 10-04
        # (merge_library_manifest_into_content_profile mirror check).
        canonical_app = canonical.get("app") or {}
        target_app = target_manifest.get("app") or {}
        merged_skills = _merge_v2_keyed_list(
            target_app.get("skills") or [], canonical_app.get("skills") or []
        )
        merged_agents = _merge_v2_keyed_list(
            target_app.get("agents") or [], canonical_app.get("agents") or []
        )
        merged_requires_apps = _merge_v2_keyed_list(
            target_app.get("requires_apps") or [],
            canonical_app.get("requires_apps") or [],
        )
        # seeds list is keyed by ``track`` (one seed group per track).
        merged_seeds = _merge_v2_keyed_list(
            target_app.get("seeds") or [],
            canonical_app.get("seeds") or [],
            key_field="track",
        )
        merged_settings_schema = (
            canonical_app.get("settings_schema")
            if canonical_app.get("settings_schema")
            else (target_app.get("settings_schema") or {})
        )
        merged_permissions = (
            canonical_app.get("permissions")
            if canonical_app.get("permissions")
            else (target_app.get("permissions") or {})
        )

        # Hook + tool bindings are part of the app's operational layer and
        # MUST survive a library merge — without this they were silently
        # dropped from the rebuilt app block, so an attached profile lost its
        # bundle hooks/tools and the hook framework registered zero bindings
        # for the workspace (entry-save hooks like hr_leave_balance never
        # fired). Canonical-wins, mirroring permissions/settings_schema.
        merged_hooks = (
            canonical_app.get("hooks")
            if canonical_app.get("hooks")
            else (target_app.get("hooks") or [])
        )
        merged_tools = (
            canonical_app.get("tools")
            if canonical_app.get("tools")
            else (target_app.get("tools") or [])
        )
        # ADR-006 / I-PC-01 — the staging exemption is read off the ATTACHED
        # manifest at write time, so dropping it here silently re-stages every
        # write the App was exempted for. Same failure mode, and the same
        # remedy, as hooks/tools above.
        merged_unstaged_tracks = (
            canonical_app.get("unstaged_tracks")
            if canonical_app.get("unstaged_tracks")
            else (target_app.get("unstaged_tracks") or [])
        )
        # F0 — operations contract + track aliases must survive merge.
        merged_operations = (
            canonical_app.get("operations")
            if canonical_app.get("operations")
            else (target_app.get("operations") or [])
        )
        merged_track_aliases = (
            canonical_app.get("track_aliases")
            if canonical_app.get("track_aliases")
            else (target_app.get("track_aliases") or [])
        )

        # F2 — App-owned field/view composites must survive merge. Without
        # these, provision_prescribed_tracks recompiles the attached CP and
        # rejects views that reference composite keys (e.g. hello_board).
        merged_field_types = _merge_v2_keyed_list(
            target_manifest.get("field_types") or [],
            canonical.get("field_types") or [],
        )
        merged_view_types = _merge_v2_keyed_list(
            target_manifest.get("view_types") or [],
            canonical.get("view_types") or [],
        )
        merged_plugins = (
            canonical.get("plugins")
            if canonical.get("plugins")
            else (target_manifest.get("plugins") or [])
        )

        target_cp.manifest = {
            "content_profile_schema_version": SCHEMA_VERSION,
            "scope": "app",
            "app": {
                "tracks": merged_track_list,
                "track_templates": merged_template_list,
                "relations": merged_relations,
                "defaults": merged_defaults,
                # v2 operational layer (Plan 10-03 MANIFEST-V2-01)
                "skills": merged_skills,
                "agents": merged_agents,
                "settings_schema": merged_settings_schema,
                "seeds": merged_seeds,
                "permissions": merged_permissions,
                "requires_apps": merged_requires_apps,
                # Operational bindings — see comment above.
                "hooks": merged_hooks,
                "tools": merged_tools,
                # ADR-006 (I-PC-01) — see comment above.
                "unstaged_tracks": merged_unstaged_tracks,
                # F0 extension contract
                "operations": merged_operations,
                "track_aliases": merged_track_aliases,
            },
            "package": canonical.get("package") or {},
            "migrations": canonical.get("migrations") or [],
        }
        if merged_field_types:
            target_cp.manifest["field_types"] = merged_field_types
        if merged_view_types:
            target_cp.manifest["view_types"] = merged_view_types
        if merged_plugins:
            target_cp.manifest["plugins"] = merged_plugins
        target_cp.manifest = _append_applied_migrations(
            target_cp.manifest,
            canonical=canonical,
            source_id=source_id or "space_merge",
        )
        target_cp.updated_at = datetime.now(timezone.utc).isoformat()
        await target_cp.save()
        # Materialize REFERENCES edges for declared app_node relations
        if merged_relations:
            await materialize_app_relations(target_cp, canonical)
        return

    tier = _tier_dict(canonical, for_space=for_space)
    now = datetime.now(timezone.utc).isoformat()

    entry_specs: List[Dict[str, Any]] = list(tier.get("entry_types") or [])
    view_specs: List[Dict[str, Any]] = list(tier.get("views") or [])
    taxonomy = tier.get("taxonomy") or {}
    tag_specs: List[Dict[str, Any]] = []
    for group in list(taxonomy.get("tag_groups") or []):
        gkey = str(group.get("key") or "default")
        for tag in list(group.get("tags") or []):
            tag_specs.append(
                {
                    **tag,
                    "group_key": gkey,
                }
            )

    tid = track.id if track else ""

    pending_entry_type_updates: List[EntryType] = []
    for spec in entry_specs:
        name = spec.get("name") or "Untitled"
        desired_schema = _form_schema_from_entry_type_spec(spec)
        spec_key = str(spec.get("key") or "")
        existing_et: Optional[EntryType] = None
        if tid:
            found = await EntryType.find(
                {"context.name": name, "context.track_id": tid}
            )
            if found:
                existing_et = found[0]
        else:
            for et_node in await target_cp.nodes(edge=[CONTAINS], node=["EntryType"]):
                if isinstance(et_node, EntryType) and str(et_node.name or "") == str(
                    name
                ):
                    existing_et = et_node
                    break
        if existing_et is not None:
            et0 = existing_et
            cur_norm = normalize_entry_type_form_schema(et0.form_schema or {})
            merged_schema, changed = merge_entry_type_schema_from_spec(
                cur_norm,
                desired_schema,
                spec_key=spec_key,
            )
            if changed:
                et0.form_schema = merged_schema
                et0.updated_at = now
                pending_entry_type_updates.append(et0)
            ctx = await target_cp.get_context()
            if not await ctx.find_edges_between(
                target_cp.id, et0.id, edge_class=CONTAINS
            ):
                await target_cp.connect(et0, edge=CONTAINS, added_at=now)
            continue
        et = await EntryType.create(
            name=name,
            icon=spec.get("icon") or "document",
            form_schema=desired_schema,
            track_id=tid,
            is_template=not bool(tid),
            created_at=now,
            updated_at=now,
        )
        await target_cp.connect(et, edge=CONTAINS, added_at=now)

    if pending_entry_type_updates:
        from app.services.graph_hydration import bulk_save_nodes

        await bulk_save_nodes(pending_entry_type_updates)

    for spec in tag_specs:
        name = spec.get("name") or "tag"
        if tid:
            existing = await Tag.find({"context.name": name, "context.track_id": tid})
            if existing:
                continue
        tag = await Tag.create(
            name=name,
            color=spec.get("color") or "#6B7280",
            track_id=tid,
            group_key=spec.get("group_key"),
            aliases=list(spec.get("aliases") or []),
            applies_to_entry_types=list(spec.get("applies_to") or []),
            is_template=not bool(tid),
            created_at=now,
        )
        await target_cp.connect(tag, edge=CONTAINS, added_at=now)

    vreg = await get_or_create_views_registry_for_content_profile(
        target_cp, track=track
    )

    defaults = tier.get("defaults") or {}
    default_view_key = slug_manifest_key(str(defaults.get("default_view") or ""))

    # Index existing views by manifest key + track scope so re-merge can patch
    # config in place without conflating shared template rows with per-track copies.
    existing_views_by_key: Dict[str, View] = {}
    for ev in await vreg.nodes(edge=[CATALOGS], node=["View"]):
        if not isinstance(ev, View):
            continue
        ev_cfg = ev.config or {}
        ev_key = slug_manifest_key(str(ev_cfg.get("_manifest_view_key") or ""))
        if not ev_key:
            continue
        ev_tid = str(getattr(ev, "track_id", "") or "")
        scope_key = f"{ev_key}::{ev_tid}" if ev_tid else f"{ev_key}::__template__"
        if scope_key not in existing_views_by_key:
            existing_views_by_key[scope_key] = ev

    pending_view_updates: List[View] = []
    for spec in view_specs:
        vname = spec.get("name") or "View"
        view_type = spec.get("view_type") or spec.get("type") or "feed"
        view_config = materialize_view_config_from_spec(spec)
        raw_vk = str(spec.get("key") or "").strip()
        spec_key = slug_manifest_key(raw_vk) if raw_vk else ""
        if default_view_key:
            is_def = bool(spec_key) and spec_key == default_view_key
        else:
            is_def = bool(spec.get("is_default", False))
        raw_etk: List[str] = []
        for v in list(spec.get("entry_type_keys") or spec.get("entry_types") or []):
            slug = slug_manifest_key(str(v))
            if slug and slug not in raw_etk:
                raw_etk.append(slug)
        raw_default_etk = slug_manifest_key(str(spec.get("default_entry_type") or ""))
        raw_hidden = bool(spec.get("hidden", False))
        normalized_config = normalize_view_config(view_type, view_config)

        patch_key: Optional[str] = None
        if spec_key:
            if track:
                patch_key = f"{spec_key}::{track.id}"
            else:
                patch_key = f"{spec_key}::__template__"
        if patch_key and patch_key in existing_views_by_key:
            ev = existing_views_by_key[patch_key]
            dirty = False
            if str(ev.name or "") != str(vname):
                ev.name = str(vname)
                dirty = True
            if str(getattr(ev, "type", "") or "feed") != str(view_type):
                ev.type = str(view_type)
                dirty = True
            if (ev.config or {}) != normalized_config:
                ev.config = normalized_config
                dirty = True
            if list(getattr(ev, "entry_type_keys", None) or []) != raw_etk:
                ev.entry_type_keys = raw_etk
                dirty = True
            if str(getattr(ev, "default_entry_type_key", "") or "") != raw_default_etk:
                ev.default_entry_type_key = raw_default_etk
                dirty = True
            if bool(getattr(ev, "is_default", False)) != is_def:
                ev.is_default = is_def
                dirty = True
            if dirty:
                ev.updated_at = now
                pending_view_updates.append(ev)
            continue
        if track:
            new_v = await View.create(
                name=vname,
                type=view_type,
                config=normalized_config,
                track_id=track.id,
                content_profile_id=target_cp.id,
                entry_type_keys=raw_etk,
                default_entry_type_key=raw_default_etk,
                is_default=is_def,
                hidden=raw_hidden,
                created_by=track.owner_id or "",
                created_at=now,
                updated_at=now,
            )
            await catalog_view_under_track(track, new_v)
        else:
            new_v = await View.create(
                name=vname,
                type=view_type,
                config=normalized_config,
                track_id="",
                content_profile_id=target_cp.id,
                entry_type_keys=raw_etk,
                default_entry_type_key=raw_default_etk,
                is_template=True,
                is_default=is_def,
                hidden=raw_hidden,
                created_by="",
                created_at=now,
                updated_at=now,
            )
            await ensure_catalog_edge(vreg, new_v)

    if pending_view_updates:
        from app.services.graph_hydration import bulk_save_nodes

        await bulk_save_nodes(pending_view_updates)

    if track and taxonomy:
        await seed_taxonomy_for_track(
            track=track,
            content_profile=target_cp,
            runtime_tier=tier,
        )

    target_cp.manifest = _merge_track_tier_into_manifest(
        target_cp.manifest or {}, canonical, tier
    )
    target_cp.manifest = _append_applied_migrations(
        target_cp.manifest,
        canonical=canonical,
        source_id=source_id or "track_merge",
    )
    target_cp.updated_at = now
    await target_cp.save()

    if track:
        await synchronize_track_view_default_flags(track)
        from app.services.content_profile_runtime import (
            write_view_entry_type_constraints_from_manifest,
        )

        vreg = await get_or_create_views_registry_for_content_profile(
            target_cp, track=track
        )
        views = await vreg.nodes(edge=[CATALOGS], node=["View"])
        await write_view_entry_type_constraints_from_manifest(track, list(views or []))


class _InMemoryLibraryManifest:
    __slots__ = ("manifest",)

    def __init__(self, manifest: Dict[str, Any]):
        self.manifest = manifest


def _track_spec_to_library_manifest_dict(
    track_spec: Dict[str, Any],
) -> Dict[str, Any]:
    tier = {
        k: v
        for k, v in track_spec.items()
        if k not in ("provision_on_create", "key", "name", "description")
    }
    # App-owned view composites (F2) compile to ``view_type: <composite_key>``
    # plus ``composite.base``. Track-scope recompile has no ``view_types[]``,
    # so resolve to the base primitive here while keeping composite metadata.
    raw_views = list(tier.get("views") or [])
    resolved_views: List[Dict[str, Any]] = []
    for raw in raw_views:
        if not isinstance(raw, dict):
            continue
        view = dict(raw)
        composite = view.get("composite")
        if isinstance(composite, dict):
            base = str(composite.get("base") or "").strip()
            if base:
                view["view_type"] = base
        resolved_views.append(view)
    if resolved_views:
        tier = {**tier, "views": resolved_views}
    return {
        "content_profile_schema_version": SCHEMA_VERSION,
        "scope": "track",
        "track": tier,
    }


async def refresh_app_track_template_materialization(
    app_node: App,
    template_key: str,
) -> Optional[ContentProfile]:
    """Re-merge one ``app.track_templates[]`` spec onto its template ContentProfile.

    Anchor tracks share the per-app template CP by reference; patching views and
    entry types on that CP propagates Planner-style kanban config to every
    anchored sibling track under the App.
    """
    from app.services.content_profile_compile import find_app_track_template_spec_by_key
    from app.services.content_profile_graph import (
        _resolve_or_create_template_content_profile,
    )

    template_key = str(template_key or "").strip()
    if not template_key:
        return None
    app_cp = await get_app_attached_content_profile(app_node)
    if app_cp is None or not app_cp.manifest:
        return None
    canonical = compile_canonical_manifest(manifest=dict(app_cp.manifest or {}))
    template_spec = find_app_track_template_spec_by_key(canonical, template_key)
    if template_spec is None:
        return None
    now = datetime.now(timezone.utc).isoformat()
    template_cp = await _resolve_or_create_template_content_profile(
        app_node=app_node,
        template_key=template_key,
        template_spec=template_spec,
        now=now,
    )
    shim = _InMemoryLibraryManifest(
        _track_spec_to_library_manifest_dict(
            {**template_spec, "key": template_key},
        )
    )
    await merge_library_manifest_into_content_profile(
        shim, template_cp, track=None, for_space=False
    )
    return template_cp


async def refresh_all_app_track_template_materializations(app_node: App) -> int:
    """Re-merge every declared ``app.track_templates[]`` entry for ``app_node``."""
    app_cp = await get_app_attached_content_profile(app_node)
    if app_cp is None or not app_cp.manifest:
        return 0
    canonical = compile_canonical_manifest(manifest=dict(app_cp.manifest or {}))
    templates = list((canonical.get("app") or {}).get("track_templates") or [])
    count = 0
    for raw in templates:
        spec = raw if isinstance(raw, dict) else {}
        key = str(spec.get("key") or "").strip()
        if not key:
            continue
        if await refresh_app_track_template_materialization(app_node, key):
            count += 1
    return count


async def apply_space_track_spec_to_track(
    track: Track, track_spec: Dict[str, Any]
) -> None:
    """Materialize manifest tier (entry types, views, taxonomy) onto a real track."""
    from app.services.app_graph import ensure_track_attached_content_profile

    tcp = await get_track_attached_content_profile(track)
    if not tcp:
        skip_bootstrap = bool(getattr(track, "template_id", None))
        tcp = await ensure_track_attached_content_profile(
            track,
            skip_default_bootstrap=skip_bootstrap,
        )
    shim = _InMemoryLibraryManifest(_track_spec_to_library_manifest_dict(track_spec))
    await merge_library_manifest_into_content_profile(shim, tcp, track, for_space=False)


async def provision_prescribed_tracks_from_app_manifest(
    app_node: App, owner_user_id: str
) -> None:
    """Create tracks from ``app.tracks`` when package flags allow (app_node apply only)."""
    from app.api.validators import validate_track_visibility_workspace

    sacp = await get_app_attached_content_profile(app_node)
    if not sacp or not sacp.manifest:
        return
    canonical = compile_canonical_manifest(manifest=dict(sacp.manifest))
    if not app_manifest_provisioning_enabled(canonical):
        return
    user = await get_user_node(owner_user_id)
    existing = await app_node.nodes(edge=["CONTAINS"], node=["Track"])
    used_template_keys = {
        str(t.template_id) for t in existing if getattr(t, "template_id", None)
    }
    for raw_spec in (canonical.get("app") or {}).get("tracks") or []:
        spec = raw_spec if isinstance(raw_spec, dict) else {}
        if not bool(spec.get("provision_on_create", True)):
            continue
        key = str(spec.get("key") or "")
        if not key:
            continue
        if key in used_template_keys:
            # Track already materialized (reinstall / reuse / update) —
            # re-apply the spec so drifted or legacy nodes reconcile with
            # the manifest. Idempotent: the adopt branches inside
            # apply_space_track_spec_to_track match by name/key and heal
            # pre-manifest-key EntryTypes instead of duplicating them.
            existing_track = next(
                (
                    t
                    for t in existing
                    if str(getattr(t, "template_id", "") or "") == key
                ),
                None,
            )
            if existing_track is not None:
                # Track display-name sync — a manifest rename (e.g. a
                # track prefixed with its app's country name to avoid
                # colliding, workspace-wide, with a same-titled track from
                # a sibling app) previously never reached an already-
                # provisioned Track node; only entry types/views/taxonomy
                # got reconciled via apply_space_track_spec_to_track below,
                # same class of gap as the App-node name/description sync
                # fix in update_app_from_library.
                new_name = str(spec.get("name") or "").strip()
                if new_name and existing_track.title != new_name:
                    existing_track.title = new_name
                    await existing_track.save()
                await apply_space_track_spec_to_track(existing_track, spec)
            continue
        now = datetime.now(timezone.utc).isoformat()
        # Provisioned tracks inherit cascade from the parent App via the
        # resolver; storing the App's literal visibility ("private",
        # "workspace", …) on the Track would (a) be invalid under the
        # new "inherit" | "private" Track visibility enum and (b) make
        # resolve_role treat the Track as a deny gate, killing
        # app_node-collaborator cascade.
        resolved_vis = "inherit"
        # Inherit the parent App's workspace_id (single-parent rule).
        from app.services.workspace_resolver import (
            resolve_workspace_id_for_user_node,
        )

        track_workspace_id = (getattr(app_node, "workspace_id", "") or "") or (
            await resolve_workspace_id_for_user_node(user=user) if user else ""
        )
        await validate_track_visibility_workspace(resolved_vis, track_workspace_id)
        track = await Track.create(
            title=str(spec.get("name") or key),
            owner_id=owner_user_id,
            purpose=str(spec.get("description") or "").strip(),
            icon="",
            visibility=resolved_vis,
            template_id=key,
            workspace_id=track_workspace_id,
            created_at=now,
            updated_at=now,
        )
        if user:
            await user.connect(track, edge=OWNS, role="owner", granted_at=now)
        await catalog_track(track)
        await apply_space_track_spec_to_track(track, spec)

        # NOTE: a prescribed-track spec may declare ``public_share`` in its
        # source manifest, but ``compile_canonical_manifest`` does not carry
        # that key onto the compiled track spec, and ``spec`` here is compiled.
        # There used to be an auto-mint branch on ``spec.get("public_share")``;
        # it was unreachable, and reachable would have been worse than useless:
        # share tokens are hash-only and disclosed once at mint, so a mint with
        # no recipient discards the only usable copy of the token while leaving
        # a link that is neither revoked nor expired. That makes
        # ``_track_has_active_public_share`` report the track as publicly
        # shared — exposing its entries as anonymous relation candidates via a
        # sibling track's share — with no way for the owner to obtain a working
        # URL, since the enable path returns ``token: None`` whenever an active
        # link already exists.
        #
        # If manifest-declared public sharing is wanted, it needs compiler
        # support AND an explicit-enable design; auto-publishing a freshly
        # provisioned track to anonymous callers as an install side effect is
        # not a decision to make silently.
        # See tests/test_provisioned_public_share.py for the guard.
        ctx = await app_node.get_context()
        if not await ctx.find_edges_between(app_node.id, track.id, edge_class=CONTAINS):
            await app_node.connect(track, edge=CONTAINS, added_at=now)
        used_template_keys.add(key)

    from app.services.bundle_post_seed import run_bundle_post_seed

    await run_bundle_post_seed(app_node, owner_user_id)
