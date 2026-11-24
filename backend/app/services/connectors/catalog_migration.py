"""Convert catalog connectors in place while Core is stopped."""

from copy import deepcopy
from typing import Any

from app.agentive.nodes import Connector
from app.agentive.services.connector_registry_node import decrypt_auth_state
from app.connectors.package_discovery import first_party_package_dir, scan_packages
from app.schemas.agentive.connectors import is_auth_state_secret_key

_FIELDS = (
    "mapping_profile",
    "package_manifest",
    "package_config",
    "package_fingerprint",
    "package_version",
    "package_revision",
    "enabled",
)


def _catalog_configuration(manifest: dict, state: dict) -> dict:
    return {
        field["key"]: deepcopy(state[field["key"]])
        for field in manifest.get("configuration", [])
        if field["type"] != "secret"
        and not is_auth_state_secret_key(field["key"])
        and field["key"] in state
    }


def _plan(row: Connector, packages: dict, fingerprints: dict) -> dict[str, Any]:
    """Compute only management metadata. Leave credentials and edges untouched."""
    if row.mapping_profile == "package" or not row.workspace_id:
        return {}
    stored_auth = row.auth_state or {}
    package_id = str(stored_auth.get("catalog_slug") or row.subclass_slug or "")
    manifest = packages.get(package_id)
    if (
        not manifest
        or manifest.get("host_managed")
        or first_party_package_dir(package_id) is None
    ):
        return {}
    state = decrypt_auth_state(stored_auth, strict=True)
    capabilities = manifest["capabilities"]
    if "sync" not in capabilities and "mcp" not in capabilities:
        return {}
    scope = "workspace" if row.connection_mode == "shared" else "personal"
    if scope not in manifest["binding"]["scopes"]:
        return {}
    stored = deepcopy(row.package_manifest)
    # Existing installed endpoints and action mappings retain precedence.
    snapshot = {**deepcopy(manifest), **stored, "id": package_id}
    enabled = row.enabled
    if "sync" in capabilities:
        enabled = (
            enabled
            or snapshot.get("auth", {}).get("type") != "oauth2"
            or bool(state.get("access_token"))
        )
    elif "mcp" in capabilities:
        enabled = enabled or row.health_status in {"ok", "healthy"}
    return {
        "mapping_profile": "package",
        "package_manifest": snapshot,
        "package_config": _catalog_configuration(snapshot, state),
        "package_fingerprint": row.package_fingerprint or fingerprints[package_id],
        "package_version": row.package_version or manifest["version"],
        "package_revision": row.package_revision + 1,
        "enabled": enabled,
    }


async def migrate(*, reverse: bool = False, dry_run: bool = False) -> int:
    """Migrate or reverse eligible rows, returning the number changed."""
    admission = scan_packages()
    changes: list[tuple[Connector, dict[str, Any], dict[str, Any]]] = []
    # Preflight every row before persisting the first mutation. Each row carries
    # its own rollback record, so an interrupted run can resume.
    for row in await Connector.find():
        saved = row.catalog_migration or {}
        if reverse:
            if saved:
                changes.append((row, saved, {}))
            continue
        if saved:
            continue
        values = _plan(row, admission.packages, admission.fingerprints)
        if values:
            prior = {field: deepcopy(getattr(row, field)) for field in _FIELDS}
            changes.append((row, values, prior))
    if not dry_run:
        for row, values, rollback in changes:
            for field, value in values.items():
                setattr(row, field, value)
            row.catalog_migration = rollback
            await row.save()
    return len(changes)
