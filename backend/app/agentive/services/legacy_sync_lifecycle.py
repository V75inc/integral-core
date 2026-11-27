"""Lifecycle for scoped first-party catalog sync records awaiting adoption."""

import httpx

from app.agentive.services.connector_packages import probe_sync_health
from app.agentive.services.connector_registry_node import decrypt_auth_state
from app.api.errors import ResourceConflictError, ServiceUnavailableError
from app.connectors.package_discovery import current_admission, first_party_package_dir
from app.services.connectors.catalog_migration import _catalog_configuration

_SYNC_ADAPTERS = frozenset({"gmail", "quickbooks", "github_issues"})


def lifecycle_package(row):
    """Identify supported legacy sync records from their existing binding."""
    if (
        row is None
        or not row.owner
        or not row.workspace_id
        or row.mapping_profile == "package"
    ):
        return None
    slug = row.subclass_slug
    if slug not in _SYNC_ADAPTERS or first_party_package_dir(slug) is None:
        return None
    if (row.auth_state or {}).get("catalog_slug") != slug:
        return None
    return slug


async def set_enabled(row, enabled, authorized_row):
    """Apply a lifecycle transition through the scoped legacy sync contract."""
    slug = lifecycle_package(row)
    if slug is None:
        raise ResourceConflictError("No lifecycle contract for this sync record")
    if enabled:
        manifest = current_admission().packages.get(slug)
        binding = "workspace" if row.connection_mode == "shared" else "personal"
        if not manifest or binding not in manifest["binding"]["scopes"]:
            raise ResourceConflictError(
                "Connector package is unavailable for this binding"
            )
        stored = row.package_manifest or {}
        try:
            snapshot = {**manifest, **stored}
            state = decrypt_auth_state(row.auth_state or {}, strict=True)
            config = _catalog_configuration(
                snapshot, {**state, **(row.package_config or {})}
            )
            await probe_sync_health(row, slug, snapshot, config)
        except (httpx.HTTPError, ValueError) as exc:
            raise ServiceUnavailableError("Connector health probe failed") from exc
    fresh = await authorized_row()
    if lifecycle_package(fresh) != slug or any(
        getattr(fresh, field) != getattr(row, field)
        for field in (
            "updated_at",
            "legacy_sync_paused",
            "legacy_sync_revision",
            "auth_state",
            "package_manifest",
            "package_config",
        )
    ):
        raise ResourceConflictError("Connector changed. Reload and try again.")
    if fresh.legacy_sync_paused == (not enabled):
        return
    fresh.legacy_sync_revision += 1
    fresh.legacy_sync_paused = not enabled
    await fresh.save()
