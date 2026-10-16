"""Generic public-share form metadata from track ``public_share.extensions``."""

from __future__ import annotations

from typing import Any, Dict, FrozenSet, Optional, Set, Tuple

from app.models.nodes import Entry, Track
from app.services.track_public_share import declared_public_share


def _split_csv(raw: str) -> Set[str]:
    return {p.strip() for p in str(raw or "").split(",") if p.strip()}


async def template_provider_slug_from_track(track: Track) -> Optional[str]:
    """Read neutral ``template_provider_slug`` from public_share.extensions."""
    from app.services.documents.entry_template_store import (
        normalize_template_provider_slug,
    )

    ext = await public_share_extensions(track)
    return normalize_template_provider_slug(ext.get("template_provider_slug"))


async def public_share_extensions(track: Track) -> Dict[str, str]:
    """Return the published extension declarations for this track."""
    declared = await declared_public_share(track) or {}
    ext = declared.get("extensions") or {}
    if not isinstance(ext, dict):
        return {}
    return {str(k): str(v or "") for k, v in ext.items()}


async def authenticated_only_entry_type_keys(track: Track) -> FrozenSet[str]:
    """Return declared entry types that require an authenticated reader."""
    ext = await public_share_extensions(track)
    return frozenset(_split_csv(ext.get("authenticated_only_entry_type_keys", "")))


async def server_managed_field_keys(
    track: Track, *, entry_type_slug: str
) -> FrozenSet[str]:
    """Return declared server-managed field keys for one entry type."""
    ext = await public_share_extensions(track)
    key = f"server_managed_field_keys_{entry_type_slug}"
    return frozenset(_split_csv(ext.get(key, "")))


def partition_server_managed_custom_fields(
    *,
    entry_type_slug: str,
    merged_custom_fields: Dict[str, Any],
    allowed_keys: set[str],
    server_managed_keys: Set[str],
) -> Tuple[Dict[str, Any], Dict[str, Any]]:
    """Pop server-managed keys absent from the published schema before validate."""
    if not server_managed_keys:
        return dict(merged_custom_fields or {}), {}

    editable = dict(merged_custom_fields or {})
    preserved: Dict[str, Any] = {}
    for key in list(editable.keys()):
        if key in server_managed_keys and key not in allowed_keys:
            preserved[key] = editable.pop(key)
    return editable, preserved


async def partition_server_managed_for_track(
    *,
    track: Track,
    entry_type_slug: str,
    merged_custom_fields: Dict[str, Any],
    allowed_keys: set[str],
) -> Tuple[Dict[str, Any], Dict[str, Any]]:
    """Separate persisted server-managed values before schema validation."""
    managed = await server_managed_field_keys(track, entry_type_slug=entry_type_slug)
    return partition_server_managed_custom_fields(
        entry_type_slug=entry_type_slug,
        merged_custom_fields=merged_custom_fields,
        allowed_keys=allowed_keys,
        server_managed_keys=set(managed),
    )


async def reject_authenticated_only_public_entry(
    entry: Entry, track: Track, *, entry_type_slug: str
) -> None:
    """Block public-share access for entry types declared auth-only in extensions."""
    only = await authenticated_only_entry_type_keys(track)
    if entry_type_slug in only:
        from app.api.errors import InsufficientPermissionsError

        raise InsufficientPermissionsError(
            message=(
                "This form requires sign-in. Open the link from your assigned "
                "form prompt after logging in."
            )
        )
