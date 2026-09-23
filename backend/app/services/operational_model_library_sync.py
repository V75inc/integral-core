"""Shared disk-to-catalog sync for library Operational Model bundles.

Used by both bootstrap and the admin rescan endpoint so add/update/remove
behavior is consistent.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Awaitable, Callable, Dict, List, Optional, Sequence

from app.models.nodes import App, OperationalModel, Track
from app.services.operational_model_library_seed import upsert_seeded_library_packages
from app.services.operational_model_loader import (
    LibraryProfileSpec,
    ProfileLoadIssue,
    load_library_operational_models_with_issues,
)

# Session-scoped cache: YAML disk walk + parse is expensive when repeated per test.
_CACHED_LIBRARY_SPECS: Optional[
    tuple[Sequence[LibraryProfileSpec], Sequence[ProfileLoadIssue]]
] = None
_CACHED_PROFILES_ROOT: Optional[Any] = None


def load_library_operational_models_with_issues_cached() -> (
    tuple[Sequence[LibraryProfileSpec], Sequence[ProfileLoadIssue]]
):
    """Return library specs parsed once per process (test session).

    Invalidates when ``operational_model_loader._PROFILES_ROOT`` or
    ``INTEGRAL_PACKAGE_PATHS`` / ``INTEGRAL_CORE_ONLY`` change.
    """
    from app.services.operational_model_loader import _PROFILES_ROOT
    from app.services.package_paths import is_core_only_mode, resolve_package_paths

    global _CACHED_LIBRARY_SPECS, _CACHED_PROFILES_ROOT
    cache_key = (
        str(_PROFILES_ROOT),
        tuple(str(p) for p in resolve_package_paths()),
        is_core_only_mode(),
    )
    if _CACHED_LIBRARY_SPECS is None or _CACHED_PROFILES_ROOT != cache_key:
        _CACHED_LIBRARY_SPECS = load_library_operational_models_with_issues()
        _CACHED_PROFILES_ROOT = cache_key
    return _CACHED_LIBRARY_SPECS


def reset_library_operational_models_cache_for_testing() -> None:
    """Clear the session cache (tests that monkeypatch ``_PROFILES_ROOT``)."""
    global _CACHED_LIBRARY_SPECS, _CACHED_PROFILES_ROOT
    _CACHED_LIBRARY_SPECS = None
    _CACHED_PROFILES_ROOT = None


async def _is_library_operational_model_referenced(operational_model_id: str) -> bool:
    tracks = await Track.find({"context.library_merge_source_id": operational_model_id})
    if tracks:
        return True
    apps = await App.find({"context.library_merge_source_id": operational_model_id})
    return bool(apps)


async def _deactivate_removed_seeded_profile(
    cp: OperationalModel,
    *,
    now_iso: str,
    reason: str,
) -> str:
    md = dict(cp.metadata or {})
    md["seed_status"] = "inactive"
    md["seed_removed_at"] = now_iso
    md["seed_removed_reason"] = reason
    cp.metadata = md
    cp.library_package = False
    cp.updated_at = now_iso
    await cp.save()
    return "deactivated"


async def reconcile_removed_seeded_packages(
    *,
    removed_slugs: Sequence[str],
    now_iso: str,
) -> List[Dict[str, Any]]:
    """Deactivate platform-seeded catalog rows no longer present on disk."""
    outcomes: List[Dict[str, Any]] = []
    for slug in removed_slugs:
        rows = await OperationalModel.find(
            {
                "context.library_package": True,
                "context.metadata.slug": slug,
            }
        )
        if rows is None:
            matches: List[OperationalModel] = []
        elif isinstance(rows, list):
            matches = rows
        else:
            matches = [rows]
        for cp in matches:
            if getattr(cp, "workspace_id", None):
                outcomes.append(
                    {
                        "slug": slug,
                        "operational_model_id": cp.id,
                        "status": "skipped_workspace",
                    }
                )
                continue
            referenced = await _is_library_operational_model_referenced(cp.id)
            status = await _deactivate_removed_seeded_profile(
                cp,
                now_iso=now_iso,
                reason="bundle_missing_referenced" if referenced else "bundle_missing",
            )
            outcomes.append(
                {
                    "slug": slug,
                    "operational_model_id": cp.id,
                    "status": status,
                    "referenced": referenced,
                }
            )
    return outcomes


async def sync_library_catalog_from_disk(
    *,
    catalog_registry: Any,
    ensure_catalog_edge: Callable[[Any, Any], Awaitable[None]],
    previous_index: Optional[Dict[str, Dict[str, Any]]] = None,
    now_iso: Optional[str] = None,
    use_cached_specs: bool = False,
) -> Dict[str, Any]:
    """Sync disk bundles into catalog and reconcile removed seeded rows."""
    stamp = now_iso or datetime.now(timezone.utc).isoformat()
    if use_cached_specs:
        specs, issues = load_library_operational_models_with_issues_cached()
    else:
        specs, issues = load_library_operational_models_with_issues()
    now_index: Dict[str, Dict[str, Any]] = {
        s.slug: {"bundle_fingerprint": s.bundle_fingerprint, "version": s.version}
        for s in specs
        if s.slug
    }
    prior = previous_index or {}
    if not prior:
        added = sorted(now_index.keys())
        updated: List[str] = []
        upsert_specs: Sequence[LibraryProfileSpec] = specs
    else:
        added = sorted([slug for slug in now_index if slug not in prior])
        updated = sorted(
            [
                slug
                for slug in now_index
                if slug in prior
                and prior.get(slug, {}).get("bundle_fingerprint")
                != now_index[slug]["bundle_fingerprint"]
            ]
        )
        changed = set(added + updated)
        upsert_specs = [s for s in specs if s.slug in changed]

    removed = sorted([slug for slug in prior if slug not in now_index])

    if upsert_specs:
        await upsert_seeded_library_packages(
            catalog_registry=catalog_registry,
            specs=upsert_specs,
            now_iso=stamp,
            ensure_catalog_edge=ensure_catalog_edge,
        )
    reconciled_removed = await reconcile_removed_seeded_packages(
        removed_slugs=removed,
        now_iso=stamp,
    )
    return {
        "added": added,
        "updated": updated,
        "removed": removed,
        "issues": issues,
        "index": now_index,
        "reconciled_removed": reconciled_removed,
    }
