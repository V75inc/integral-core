"""Generic upsert for catalogued library ContentProfile packages (modular seeding).

Domain-specific manifests live under ``app/profiles/`` as YAML files, loaded by
``content_profile_loader.load_library_profiles``. This module only compares
canonical manifests and creates/updates ``ContentProfile`` rows — no
package-specific branching.
"""

from __future__ import annotations

import json
import logging
from typing import Any, Awaitable, Callable, Dict, Sequence

from app.models.nodes import ContentProfile
from app.services.content_profile_loader import (  # noqa: F401 — backward-compat alias
    LibraryProfileSpec as SeededLibraryPackageSpec,
)

logger = logging.getLogger(__name__)


def canonical_manifest_fingerprint(manifest: Dict[str, Any]) -> str:
    """Stable string for equality of manifests after compile (ignores benign drift)."""
    from app.services.content_profile_runtime import compile_canonical_manifest

    canonical = compile_canonical_manifest(manifest=dict(manifest or {}))
    return json.dumps(canonical, sort_keys=True, default=str)


def canonical_manifests_differ(stored: Dict[str, Any], desired: Dict[str, Any]) -> bool:
    """Return True when stored and desired manifests differ after canonicalization.

    ``stored`` may have been written under an older server version whose
    registries (view types, wizard step kinds, ...) no longer match — e.g. a
    plugin removed or renamed a view type a stored library manifest still
    references. Recompiling ``stored`` must never raise: an uncaught
    exception here propagates all the way out of
    ``upsert_seeded_library_packages``'s loop (this function's only caller),
    aborting the sync for every OTHER package too — found live when a
    bundle's manifest was edited to stop using a plugin view type that got
    deleted in the same change; the stale stored manifest referencing the
    removed type permanently wedged the whole catalog sync on every server
    restart, silently (the caller's caller, ``ensure_integral_app_graph``,
    only logs and swallows). A compile failure on ``stored`` just means it
    is obviously stale, so treat it as "differs" and let this one package
    fall through to a normal overwrite.
    ``desired`` (the fresh on-disk source of truth) is NOT guarded this way
    — if that fails to compile, it is a real authoring error in the current
    bundle and should surface loudly, not be swallowed.
    """
    try:
        stored_fingerprint = canonical_manifest_fingerprint(stored)
    except Exception:
        logger.warning(
            "canonical_manifest_fingerprint failed for a stored library "
            "manifest (likely referencing a removed/renamed view type or "
            "wizard step kind) — treating it as stale and forcing an update",
            exc_info=True,
        )
        return True
    return stored_fingerprint != canonical_manifest_fingerprint(desired)


async def upsert_seeded_library_packages(
    *,
    catalog_registry: Any,
    specs: Sequence[SeededLibraryPackageSpec],
    now_iso: str,
    ensure_catalog_edge: Callable[[Any, Any], Awaitable[None]],
) -> None:
    """Create or refresh each spec under the ContentProfiles registry."""
    for spec in specs:
        await _upsert_one(
            catalog_registry=catalog_registry,
            spec=spec,
            now_iso=now_iso,
            ensure_catalog_edge=ensure_catalog_edge,
        )


async def _upsert_one(
    *,
    catalog_registry: Any,
    spec: SeededLibraryPackageSpec,
    now_iso: str,
    ensure_catalog_edge: Callable[[Any, Any], Awaitable[None]],
) -> None:
    slug = str(getattr(spec, "slug", "") or "").strip()
    raw = []
    if slug:
        raw = await ContentProfile.find(
            {
                "context.library_package": True,
                "context.metadata.slug": slug,
            }
        )
    if not raw:
        raw = await ContentProfile.find(
            {
                "context.library_package": True,
                "context.name": spec.name,
            }
        )
    if raw is None:
        found: list[Any] = []
    elif isinstance(raw, (list, tuple)):
        found = list(raw)
    else:
        found = [raw]

    # Phase B (B3) — capture bundle-derived metadata so hot-load can detect
    # file edits via fingerprint drift even when the canonical manifest is
    # equivalent. ``getattr`` fallbacks keep legacy callers (pre-B2 specs)
    # working without raising.
    metadata_patch: Dict[str, Any] = {
        "bundle_fingerprint": getattr(spec, "bundle_fingerprint", "") or "",
        "manifest_fingerprint": getattr(spec, "manifest_fingerprint", "") or "",
        "signature_verified": bool(getattr(spec, "signature_verified", True)),
        "signature_reason": getattr(spec, "signature_reason", "") or "",
        "ships_python": bool(getattr(spec, "ships_python", False)),
        "slug": slug,
        "bundle_dir_path": str(getattr(spec, "bundle_dir", "") or ""),
        "seed_status": "active",
        "package_class": getattr(spec, "package_class", "") or "community_app",
    }

    if not found:
        lib_cp = await ContentProfile.create(
            name=spec.name,
            version=spec.version,
            manifest=spec.manifest,
            scope=spec.scope,
            library_package=spec.library_package,
            description=spec.description,
            created_at=now_iso,
            updated_at=now_iso,
            metadata=metadata_patch,
        )
        await ensure_catalog_edge(catalog_registry, lib_cp)
        logger.info("Seeded library content profile %r", spec.name)
        return

    lib_cp = found[0]
    needs_update = canonical_manifests_differ(lib_cp.manifest or {}, spec.manifest)
    md = dict(lib_cp.metadata or {})
    # Bundle-fingerprint drift triggers an update even when the canonical
    # manifest is equivalent — covers the file-edit-without-manifest-change
    # case that hot-load needs to detect.
    if md.get("bundle_fingerprint") != metadata_patch["bundle_fingerprint"]:
        needs_update = True
    if needs_update:
        lib_cp.manifest = spec.manifest
        lib_cp.name = spec.name
        lib_cp.version = spec.version
        lib_cp.description = spec.description
        lib_cp.updated_at = now_iso
        md.update(metadata_patch)
        lib_cp.metadata = md
        await lib_cp.save()
        logger.info(
            "Updated library content profile %r to version %s",
            spec.name,
            spec.version,
        )
    await ensure_catalog_edge(catalog_registry, lib_cp)
