"""EntryType resolution for tracks — single query shape for all write paths."""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from app.models.nodes import EntryType, Track
from app.services.content_profile_runtime import slug_manifest_key


async def entry_types_for_track(track_id: str) -> List[EntryType]:
    """Find EntryTypes materialized on a track (supports both query shapes)."""
    found = await EntryType.find({"context.track_id": track_id})
    if not found:
        found = await EntryType.find({"track_id": track_id})
    return list(found or [])


async def resolve_entry_type_id_by_key(track_id: str, entry_type_key: str) -> str:
    """Return EntryType.id for ``entry_type_key`` on ``track_id``, or empty string."""
    et_key = slug_manifest_key(entry_type_key)
    if not et_key:
        return ""
    for et in await entry_types_for_track(track_id):
        fs = et.form_schema or {}
        manifest_key = slug_manifest_key(str(fs.get("_manifest_entry_type_key") or ""))
        if manifest_key and manifest_key == et_key:
            return et.id
        manifest_key = slug_manifest_key(str(getattr(et, "key", None) or et.name or ""))
        if manifest_key == et_key:
            return et.id
        if slug_manifest_key(str(et.name or "")) == et_key:
            return et.id
    return ""


async def resolve_seed_entry_type_id(
    target_track: Track,
    track_key: str,
    canonical: Dict[str, Any],
    entry_spec: Dict[str, Any],
    *,
    require: bool = False,
) -> str:
    """Resolve EntryType id for a manifest seed row on ``target_track``."""
    et_key = slug_manifest_key(str(entry_spec.get("entry_type") or ""))
    if not et_key:
        for tspec in (canonical.get("app") or {}).get("tracks") or []:
            if not isinstance(tspec, dict):
                continue
            if slug_manifest_key(str(tspec.get("key") or "")) != slug_manifest_key(
                track_key
            ):
                continue
            defaults = tspec.get("defaults") or {}
            et_key = slug_manifest_key(str(defaults.get("default_entry_type") or ""))
            if not et_key:
                entry_types = tspec.get("entry_types") or []
                if entry_types and isinstance(entry_types[0], dict):
                    et_key = slug_manifest_key(str(entry_types[0].get("key") or ""))
            break

    if not et_key:
        if require:
            from app.exceptions import AppInstallError

            raise AppInstallError(
                message=(f"Cannot resolve entry_type for seed on track {track_key!r}"),
                details={"track_id": target_track.id, "track_key": track_key},
            )
        return ""

    type_id = await resolve_entry_type_id_by_key(target_track.id, et_key)
    if not type_id:
        # Legacy fallback: EntryTypes materialized before manifest-key
        # embedding carry no _manifest_entry_type_key and their name rarely
        # slugs to the manifest key (e.g. key 'source_material', name
        # 'Source'). Map the key back to the manifest spec's display name
        # and match by that instead of failing the whole install.
        spec_name = _manifest_entry_type_name(canonical, track_key, et_key)
        if spec_name:
            name_slug = slug_manifest_key(spec_name)
            for et in await entry_types_for_track(target_track.id):
                if slug_manifest_key(str(et.name or "")) == name_slug:
                    type_id = et.id
                    break
    if require and not type_id:
        from app.exceptions import AppInstallError

        raise AppInstallError(
            message=(
                f"Entry type {et_key!r} is missing from track "
                f"{getattr(target_track, 'title', '') or track_key!r} — the "
                "track may pre-date this bundle version. Rename or remove "
                "the conflicting track and reinstall."
            ),
            details={"track_id": target_track.id, "entry_type_key": et_key},
        )
    return type_id


def _manifest_entry_type_name(
    canonical: Dict[str, Any], track_key: str, et_key: str
) -> str:
    """Display name declared for ``et_key`` under ``track_key`` in the manifest."""
    for tspec in (canonical.get("app") or {}).get("tracks") or []:
        if not isinstance(tspec, dict):
            continue
        if slug_manifest_key(str(tspec.get("key") or "")) != slug_manifest_key(
            track_key
        ):
            continue
        for espec in tspec.get("entry_types") or []:
            if not isinstance(espec, dict):
                continue
            if slug_manifest_key(str(espec.get("key") or "")) == slug_manifest_key(
                et_key
            ):
                return str(espec.get("name") or "")
    return ""


async def resolve_entry_type_id_for_track(
    track_id: str,
    *,
    type_id: str = "",
    type_key: str = "",
) -> str:
    """Resolve EntryType id from explicit id or manifest key."""
    if type_id:
        return type_id
    if type_key:
        resolved = await resolve_entry_type_id_by_key(track_id, type_key)
        if resolved:
            return resolved
    return await default_entry_type_id_for_track(track_id)


async def default_entry_type_id_for_track(
    track_id: str,
    *,
    canonical: Optional[Dict[str, Any]] = None,
    track_key: Optional[str] = None,
) -> str:
    """Best-effort default EntryType for a track (manifest default or first type)."""
    if canonical and track_key:
        for tspec in (canonical.get("app") or {}).get("tracks") or []:
            if not isinstance(tspec, dict):
                continue
            if slug_manifest_key(str(tspec.get("key") or "")) != slug_manifest_key(
                track_key
            ):
                continue
            defaults = tspec.get("defaults") or {}
            key = str(defaults.get("default_entry_type") or "")
            if key:
                tid = await resolve_entry_type_id_by_key(track_id, key)
                if tid:
                    return tid
            entry_types = tspec.get("entry_types") or []
            if entry_types and isinstance(entry_types[0], dict):
                key = str(entry_types[0].get("key") or "")
                if key:
                    tid = await resolve_entry_type_id_by_key(track_id, key)
                    if tid:
                        return tid
    etypes = await entry_types_for_track(track_id)
    if etypes:
        return etypes[0].id
    return ""
