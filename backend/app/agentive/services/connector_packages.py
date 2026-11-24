"""Install and run one admitted declarative connector package.

The installation is a Connector node, owned by the user and attached to the
workspace. Add leaves it disabled. Enable requires a passing health probe.
Writes do not leave the process until a review is approved.
"""

from __future__ import annotations

import asyncio
import json
import re
import secrets
from typing import Any

import httpx

from app.agentive.connectors.mcp_client import call_remote_tool as call_mcp_tool
from app.agentive.nodes import Connector
from app.agentive.services.connector_registry_node import (
    create_connector,
    decrypt_auth_state,
    encode_package_secrets,
)
from app.api.errors import (
    BadRequestError,
    ResourceConflictError,
    ResourceNotFoundError,
    ServiceUnavailableError,
)
from app.connectors.package_discovery import current_admission, rescan
from app.connectors.package_runtime import (
    package_capabilities,
    package_core,
    package_description,
    package_host_managed,
    package_icon,
    package_name,
    package_publisher,
    package_schema,
    package_version,
    perform_request,
    pull_declared_sync,
    select_output,
    webhook_signature_ok,
)
from app.services.permissions import get_user_node

_PACKAGE = "package"
_install_guard = asyncio.Lock()
_install_locks: dict[tuple[str, str], asyncio.Lock] = {}


async def _workspace_install_lock(package_id: str, workspace_id: str) -> asyncio.Lock:
    """Serialize installs of one package in one workspace inside this process."""
    async with _install_guard:
        key = (package_id, workspace_id)
        lock = _install_locks.get(key)
        if lock is None:
            lock = asyncio.Lock()
            _install_locks[key] = lock
        return lock


def _workspace_cardinality(
    manifest: dict[str, Any], package_id: str | None = None
) -> str:
    """Return workspace cardinality. An omitted value means one."""

    def declared(source: Any) -> str | None:
        if not isinstance(source, dict):
            return None
        binding = source.get("binding")
        if not isinstance(binding, dict) or "cardinality" not in binding:
            return None
        cardinality = binding.get("cardinality")
        if not isinstance(cardinality, dict) or "workspace" not in cardinality:
            return None
        value = cardinality.get("workspace")
        if value not in {"one", "many"}:
            raise ValueError("package does not declare a workspace cardinality")
        return value

    found = declared(manifest)
    if found is None and package_id:
        from app.connectors.package_runtime import admitted_manifest

        found = declared(admitted_manifest(package_id) or {})
    return found or "one"


def _binding_scopes(
    manifest: dict[str, Any], package_id: str | None = None
) -> list[str]:
    """Return binding scopes. A stored list wins. A missing list is refused."""

    def declared(source: Any) -> list[str] | None:
        if not isinstance(source, dict):
            return None
        binding = source.get("binding")
        if not isinstance(binding, dict) or "scopes" not in binding:
            return None
        scopes = binding.get("scopes")
        if not isinstance(scopes, list) or not scopes:
            raise ValueError("package does not declare binding scopes")
        seen: list[str] = []
        for item in scopes:
            if item not in {"workspace", "personal"} or item in seen:
                raise ValueError("package does not declare binding scopes")
            seen.append(item)
        return seen

    found = declared(manifest)
    if found is None and package_id:
        from app.connectors.package_runtime import admitted_manifest

        found = declared(admitted_manifest(package_id) or {})
    if found is None:
        raise ValueError("package does not declare binding scopes")
    return found


def installation_package_id(connector: Any) -> str:
    """Keep a legacy adapter dispatch slug independent of its package identity."""
    slug = str(getattr(connector, "subclass_slug", "") or "")
    if slug == "mcp" or getattr(connector, "catalog_migration", None):
        manifest = getattr(connector, "package_manifest", None) or {}
        state = getattr(connector, "auth_state", None) or {}
        return str(manifest.get("id") or state.get("catalog_slug") or slug)
    return slug


async def installations(
    workspace_id: str, actor_id: str | None = None
) -> list[dict[str, Any]]:
    """Return shared installations and the caller's own personal installations."""
    rows = await Connector.find(
        {"context.workspace_id": workspace_id, "context.mapping_profile": _PACKAGE}
    )
    user = await get_user_node(actor_id) if actor_id else None
    identities = {actor_id, getattr(user, "id", None), getattr(user, "user_id", None)}
    return [
        projection(row)
        for row in rows
        if row.connection_mode == "shared" or (row.owner and row.owner in identities)
    ]


async def attach_host_grants(packages: list[dict[str, Any]], workspace_id: str) -> None:
    """Attach a host-managed grant status. Secrets stay on the binding."""
    from app.connectors.package_channels import read_host_grant_for_workspace

    for package in packages:
        if not package.get("host_managed"):
            continue
        grant = await read_host_grant_for_workspace(
            str(package.get("id") or ""), workspace_id
        )
        if grant is not None:
            package["grant"] = grant


def catalogue(scope: str = "workspace") -> dict[str, Any]:
    """Return admitted packages the caller can install in ``scope``."""
    admission = current_admission()
    offered = []
    for package_id, manifest in sorted(admission.packages.items()):
        try:
            scopes = _binding_scopes(manifest)
        except ValueError:
            continue
        if scope not in scopes:
            continue
        try:
            managed = package_host_managed(manifest, package_id)
        except ValueError:
            continue
        try:
            description = package_description(manifest, package_id)
        except ValueError:
            continue
        try:
            name = package_name(manifest, package_id)
        except ValueError:
            continue
        try:
            version = package_version(manifest, package_id)
        except ValueError:
            continue
        try:
            publisher = package_publisher(manifest, package_id)
        except ValueError:
            continue
        try:
            icon = package_icon(manifest, package_id)
        except ValueError:
            continue
        try:
            package_core(manifest, package_id)
        except ValueError:
            continue
        try:
            capabilities = package_capabilities(manifest, package_id)
        except ValueError:
            continue
        try:
            package_schema(manifest, package_id)
        except ValueError:
            continue
        row = {
            "id": package_id,
            "name": name,
            "description": description,
            "version": version,
            "publisher": publisher,
            "capabilities": capabilities,
            "host_managed": managed,
            "fingerprint": admission.fingerprints.get(package_id, ""),
            "configuration": [
                _public_field(field) for field in _visible_fields(manifest, scope)
            ],
        }
        channel = (manifest.get("capabilities") or {}).get("channel") or {}
        auth = manifest.get("auth") or {}
        if auth.get("type") == "oauth2":
            row["auth_type"] = "oauth2"
        if channel.get("mode"):
            row["channel_mode"] = channel["mode"]
        if icon is not None:
            row["icon"] = icon
        offered.append(row)
    return {"packages": offered, "rejected": list(admission.rejected)}


def _manifest(package_id: str) -> dict[str, Any]:
    manifest = current_admission().packages.get(package_id)
    if manifest is None:
        raise ResourceNotFoundError("Connector package is not admitted")
    return manifest


def _public_field(field: dict[str, Any]) -> dict[str, Any]:
    """Return one form field. Defaults and secret values stay out of the catalogue."""
    shown = {
        "key": field["key"],
        "label": field["label"],
        "type": field["type"],
        "required": bool(field.get("required")),
    }
    if field.get("help"):
        shown["help"] = field["help"]
    if field.get("options"):
        shown["options"] = field["options"]
    if field.get("multiple"):
        shown["multiple"] = True
    source = field.get("options_from")
    if isinstance(source, dict) and source.get("action"):
        shown["options_from"] = {
            "action": source.get("action"),
            "items": source.get("items"),
            "value": source.get("value"),
            "label": source.get("label"),
        }
    return shown


def _fields(manifest: dict[str, Any]) -> list[dict[str, Any]]:
    return list(manifest.get("configuration") or [])


_CONFIG_TYPES = frozenset(
    {"string", "text", "url", "secret", "number", "boolean", "select"}
)
_CONFIG_KEY = re.compile(r"^[a-z][a-z0-9_]{0,62}$")


def _configuration_list(value: Any) -> list[dict[str, Any]]:
    """Refuse a configuration list that is not a set of declared fields."""
    if not isinstance(value, list):
        raise ValueError("package does not declare configuration")
    for field in value:
        if not isinstance(field, dict):
            raise ValueError("package does not declare configuration")
        key = field.get("key")
        label = field.get("label")
        kind = field.get("type")
        scope = field.get("scope")
        if (
            not isinstance(key, str)
            or _CONFIG_KEY.fullmatch(key) is None
            or not isinstance(label, str)
            or not label
            or len(label) > 80
            or kind not in _CONFIG_TYPES
            or (scope is not None and scope not in {"workspace", "personal"})
        ):
            raise ValueError("package does not declare configuration")
    return value


def _with_package_configuration(
    manifest: dict[str, Any], package_id: str
) -> dict[str, Any]:
    """Use the snapshot field list, or the admitted package field list."""
    if isinstance(manifest, dict) and "configuration" in manifest:
        _configuration_list(manifest.get("configuration"))
        return manifest
    from app.connectors.package_runtime import admitted_manifest

    admitted = admitted_manifest(package_id) or {}
    if "configuration" not in admitted:
        return manifest if isinstance(manifest, dict) else {}
    copied = dict(manifest or {})
    copied["configuration"] = _configuration_list(admitted.get("configuration"))
    return copied


def _visible_fields(manifest: dict[str, Any], scope: str) -> list[dict[str, Any]]:
    """Return fields that apply to this installation scope."""
    return [
        field
        for field in _fields(manifest)
        if not field.get("scope") or field.get("scope") == scope
    ]


def _split(
    manifest: dict[str, Any],
    values: dict[str, Any],
    *,
    keep_missing_secrets: bool = False,
    scope: str = "workspace",
) -> tuple[dict[str, Any], dict[str, Any]]:
    config: dict[str, Any] = {}
    secrets_out: dict[str, Any] = {}
    known = {field["key"]: field for field in _visible_fields(manifest, scope)}
    if set(values) - set(known):
        raise BadRequestError("Unknown configuration field")
    for key, field in known.items():
        if key not in values or (field["type"] == "secret" and values.get(key) == ""):
            if field["type"] == "secret" and keep_missing_secrets:
                continue
            if field.get("required"):
                raise BadRequestError(f"{key} is required")
            continue
        if field["type"] == "secret" and values.get(key) is None:
            if not keep_missing_secrets:
                raise BadRequestError(f"{key} is required")
            secrets_out[key] = None
            continue
        value = values[key]
        if field["type"] == "secret":
            if not isinstance(value, str) or not value:
                raise BadRequestError(f"{key} must be a secret string")
            secrets_out[key] = value
            continue
        kind = field["type"]
        if kind == "url":
            from app.connectors.package_runtime import require_package_url

            require_package_url(key, value, str(field.get("egress") or "public"))
        elif kind in {"string", "text"}:
            if not isinstance(value, str):
                raise BadRequestError(f"{key} must be text")
        elif kind == "number":
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                raise BadRequestError(f"{key} must be a number")
        elif kind == "boolean":
            if not isinstance(value, bool):
                raise BadRequestError(f"{key} must be true or false")
        elif kind == "select":
            multiple = bool(field.get("multiple"))
            if field.get("options_from"):
                if multiple:
                    if not isinstance(value, list) or not all(
                        isinstance(item, str) and item for item in value
                    ):
                        raise BadRequestError(f"{key} must be a list of choices")
                elif not isinstance(value, str) or not value:
                    raise BadRequestError(f"{key} must be one choice")
            else:
                options = field.get("options")
                allowed = (
                    {
                        option.get("value")
                        for option in options
                        if isinstance(option, dict)
                    }
                    if isinstance(options, list)
                    else set()
                )
                if not allowed:
                    raise BadRequestError(f"{key} must be one of the declared choices")
                if multiple:
                    if not isinstance(value, list) or not all(
                        isinstance(item, str) and item in allowed for item in value
                    ):
                        raise BadRequestError(
                            f"{key} must be a list of declared choices"
                        )
                elif not isinstance(value, str) or value not in allowed:
                    raise BadRequestError(f"{key} must be one of the declared choices")
        config[key] = value
    return config, secrets_out


async def _existing(package_id: str, workspace_id: str) -> list[Connector]:
    rows = await Connector.find({"context.workspace_id": workspace_id})
    return [
        row
        for row in rows
        if installation_package_id(row) == package_id
        and getattr(row, "connection_mode", None) == "shared"
    ]


async def install_package(
    *, actor_id: str, workspace_id: str, package_id: str, values: dict[str, Any]
) -> Connector:
    """Create a disabled installation. Workspace cardinality defaults to one."""
    manifest = _manifest(package_id)
    try:
        managed = package_host_managed(manifest, package_id)
    except ValueError as exc:
        raise BadRequestError("This package does not declare host management") from exc
    if managed:
        raise BadRequestError(
            "This package keeps its existing installations and grants"
        )
    try:
        scopes = _binding_scopes(manifest, package_id)
    except ValueError as exc:
        raise BadRequestError(
            "This package cannot be installed for a workspace"
        ) from exc
    if "workspace" not in scopes:
        raise BadRequestError("This package cannot be installed for a workspace")
    lock = await _workspace_install_lock(package_id, workspace_id)
    async with lock:
        return await _create_installation(
            actor_id=actor_id,
            workspace_id=workspace_id,
            package_id=package_id,
            manifest=manifest,
            values=values,
        )


async def _create_installation(
    *,
    actor_id: str,
    workspace_id: str,
    package_id: str,
    manifest: dict[str, Any],
    values: dict[str, Any],
) -> Connector:
    if _workspace_cardinality(manifest, package_id) != "many" and await _existing(
        package_id, workspace_id
    ):
        raise ResourceConflictError("This package is already installed here")
    config, secret_values = _split(manifest, values)
    connector = await create_connector(
        owner=actor_id,
        kind="custom",
        auth_state=encode_package_secrets(secret_values, set(secret_values)),
        capabilities=sorted(manifest["capabilities"].keys()),
        skip_default_policy="sync" not in manifest["capabilities"],
        workspace_id=workspace_id,
    )
    connector.subclass_slug = package_id
    connector.label = manifest["name"]
    connector.connection_mode = "shared"
    connector.mapping_profile = _PACKAGE

    connector.package_version = manifest["version"]
    connector.package_config = config
    connector.package_fingerprint = current_admission().fingerprints.get(package_id, "")
    connector.package_manifest = _snapshot(manifest)
    connector.enabled = False
    _apply_sync_interval(connector, manifest)
    await connector.save()
    return connector


def projection(connector: Connector) -> dict[str, Any]:
    """Return the Settings view of one installation. Secrets are omitted."""
    return {
        "id": connector.id,
        "package_id": installation_package_id(connector),
        "name": connector.label,
        "version": connector.package_version,
        "revision": int(connector.package_revision or 0),
        "enabled": bool(connector.enabled),
        "health_status": connector.health_status,
        "config": dict(connector.package_config or {}),
        "pending_review": (connector.pending_review or {}).get("status") == "pending",
        "package_available": installation_package_id(connector)
        in current_admission().packages,
        "fingerprint": connector.package_fingerprint or "",
        "admitted_fingerprint": current_admission().fingerprints.get(
            installation_package_id(connector), ""
        ),
        "upgrade_available": _upgrade_available(connector),
        "rollback_available": bool(connector.package_previous),
    }


_SNAPSHOT_KEYS = (
    "id",
    "version",
    "http",
    "capabilities",
    "auth",
    "health",
    "configuration",
    "trust",
)


def _snapshot(manifest: dict[str, Any]) -> dict[str, Any]:
    """Copy the manifest fields an installation runs and upgrades."""
    import copy

    return copy.deepcopy(
        {key: manifest[key] for key in _SNAPSHOT_KEYS if key in manifest}
    )


def _field_keys(manifest: dict[str, Any], *, secret: bool) -> set[str]:
    return {
        field["key"]
        for field in manifest.get("configuration") or []
        if isinstance(field, dict)
        and (field.get("type") == "secret" or field.get("secret") is True) is secret
        and field.get("key")
    }


def _declared_interval(manifest: dict[str, Any]) -> int | None:
    """Return a package sync interval of at least 300 seconds."""
    sync = (manifest.get("capabilities") or {}).get("sync") or {}
    interval = sync.get("interval_seconds")
    if isinstance(interval, bool) or not isinstance(interval, int) or interval < 300:
        return None
    return interval


def installed_sync_interval(connector: Any) -> int | None:
    """Return the interval captured at Add for a package installation."""
    if getattr(connector, "mapping_profile", "") != "package":
        return None
    if getattr(connector, "catalog_migration", None):
        return None
    stored = getattr(connector, "package_manifest", None)
    if not isinstance(stored, dict):
        return None
    return _declared_interval(stored)


def _apply_sync_interval(connector: Connector, manifest: dict[str, Any]) -> None:
    """Copy the package sync interval onto the installation the scheduler reads."""
    interval = _declared_interval(manifest)
    if interval is not None:
        connector.sync_interval_seconds = interval


def _upgrade_available(connector: Connector) -> bool:
    current = current_admission().packages.get(installation_package_id(connector)) or {}
    stored = connector.package_manifest or {}
    return bool(current) and _snapshot(stored) != _snapshot(current)


async def upgrade_package(
    connector_id: str, actor_id: str, workspace_id: str | None = None
) -> Connector:
    """Move an installation onto the admitted manifest. One rollback is kept."""
    from app.agentive.services.connector_registry_node import encrypt_auth_state

    connector = await _owned(connector_id, actor_id, workspace_id)
    _require_admitted(connector)
    current = _manifest(installation_package_id(connector))
    stored = dict(connector.package_manifest or {})
    if stored and _snapshot(stored) == _snapshot(current):
        raise BadRequestError("Installation is already on the admitted package")
    secrets = decrypt_auth_state(connector.auth_state or {})
    removed = _field_keys(stored, secret=True) - _field_keys(current, secret=True)
    removed_secrets = {key: secrets.pop(key) for key in removed if key in secrets}
    kept_config = {
        key: value
        for key, value in dict(connector.package_config or {}).items()
        if key in _field_keys(current, secret=False)
    }
    connector.package_previous = {
        "version": connector.package_version,
        "manifest": stored,
        "config": dict(connector.package_config or {}),
        "fingerprint": connector.package_fingerprint or "",
        "secrets": encrypt_auth_state(
            encode_package_secrets(removed_secrets, set(removed_secrets))
        ),
    }
    connector.package_fingerprint = current_admission().fingerprints.get(
        installation_package_id(connector), ""
    )
    connector.package_manifest = _snapshot(current)
    _apply_sync_interval(connector, current)
    connector.package_version = str(current.get("version") or "")
    connector.package_config = kept_config
    connector.auth_state = encrypt_auth_state(
        encode_package_secrets(
            secrets, _field_keys(connector.package_manifest, secret=True)
        )
    )
    connector.package_revision = int(connector.package_revision or 0) + 1
    connector.enabled = False
    connector.health_status = "unknown"
    await connector.save()
    sync_package_action_tools(connector)
    return connector


async def rollback_package(
    connector_id: str, actor_id: str, workspace_id: str | None = None
) -> Connector:
    """Restore the manifest, configuration, and secrets from the last upgrade."""
    from app.agentive.services.connector_registry_node import encrypt_auth_state

    connector = await _owned(connector_id, actor_id, workspace_id)
    previous = dict(connector.package_previous or {})
    if not previous.get("manifest"):
        raise ResourceConflictError("There is no upgrade to roll back")
    secrets = decrypt_auth_state(connector.auth_state or {})
    secrets.update(decrypt_auth_state(previous.get("secrets") or {}))
    connector.package_manifest = dict(previous.get("manifest") or {})
    _apply_sync_interval(connector, connector.package_manifest)
    connector.package_config = dict(previous.get("config") or {})
    connector.package_version = str(previous.get("version") or "")
    connector.package_fingerprint = str(previous.get("fingerprint") or "")
    connector.auth_state = encrypt_auth_state(
        encode_package_secrets(
            secrets, _field_keys(connector.package_manifest, secret=True)
        )
    )
    connector.package_previous = {}
    connector.package_revision = int(connector.package_revision or 0) + 1
    connector.enabled = False
    connector.health_status = "unknown"
    await connector.save()
    sync_package_action_tools(connector)
    return connector


def _installed_manifest(connector: Connector) -> dict[str, Any]:
    """Reject snapshots of capabilities excluded from the scoped release."""
    admitted = _manifest(installation_package_id(connector))
    stored = getattr(connector, "package_manifest", None)
    if not isinstance(stored, dict) or not stored.get("capabilities"):
        return admitted
    tier = (stored.get("trust") or {}).get("tier")
    if tier not in {None, "declarative", "trusted"} or (
        tier == "trusted" and (admitted.get("trust") or {}).get("tier") != "trusted"
    ):
        raise ResourceNotFoundError("Connector package tier is unavailable")
    return stored


def _require_admitted(connector: Connector) -> None:
    """Refuse use of an installation whose package is no longer admitted."""
    if installation_package_id(connector) not in current_admission().packages:
        raise ResourceNotFoundError("Connector package is not admitted")


async def configure_package(
    connector_id: str,
    actor_id: str,
    values: dict[str, Any],
    workspace_id: str | None = None,
) -> Connector:
    """Replace configuration, store new secrets, and disable until the next probe."""
    connector = await _owned(connector_id, actor_id, workspace_id)
    _require_admitted(connector)
    manifest = _with_package_configuration(
        _installed_manifest(connector), installation_package_id(connector)
    )
    config, secret_values = _split(manifest, values, keep_missing_secrets=True)
    connector.package_config = config
    connector.package_revision = int(connector.package_revision or 0) + 1
    if secret_values:
        from app.agentive.services.connector_registry_node import encrypt_auth_state

        current = decrypt_auth_state(connector.auth_state or {})
        for key, value in secret_values.items():
            if value is None:
                current.pop(key, None)
            else:
                current[key] = value
        connector.auth_state = encrypt_auth_state(
            encode_package_secrets(current, _field_keys(manifest, secret=True))
        )
    connector.enabled = False
    connector.health_status = "unknown"
    await connector.save()
    sync_package_action_tools(connector)
    return connector


async def _owned(
    connector_id: str, actor_id: str, workspace_id: str | None = None
) -> Connector:
    connector = await Connector.get(connector_id)
    user = await get_user_node(actor_id)
    if (
        connector is None
        or user is None
        or connector.mapping_profile != _PACKAGE
        or connector.owner
        not in {actor_id, getattr(user, "id", None), getattr(user, "user_id", None)}
        or (workspace_id is not None and connector.workspace_id != workspace_id)
    ):
        raise ResourceNotFoundError("Connector installation not found")
    from app.agentive.services.profile_management import require_profile_manager

    await require_profile_manager(user.id, connector.workspace_id)
    return connector


def _package_tool_bundle(connector_id: str) -> str:
    return f"connector-package:{connector_id}"


def sync_package_action_tools(connector: Connector) -> None:
    """Register ``<package>.<action>`` tools, or remove them when disabled."""
    import logging
    import re

    from app.services.hooks.errors import HookMisconfiguredError
    from app.services.hooks.registry import (
        register_workspace_tools,
        unregister_bundle_registrations,
    )

    workspace_id = str(getattr(connector, "workspace_id", "") or "")
    connector_id = str(getattr(connector, "id", "") or "")
    if not workspace_id or not connector_id:
        return
    bundle = _package_tool_bundle(connector_id)
    unregister_bundle_registrations(workspace_id, bundle)
    if not getattr(connector, "enabled", False):
        return
    try:
        manifest = _installed_manifest(connector)
    except (ResourceNotFoundError, AttributeError):
        return
    package_id = installation_package_id(connector)
    specs = []
    for action in (manifest.get("capabilities") or {}).get("actions") or []:
        name = str(action.get("name") or "")
        if not re.fullmatch(r"[a-z][a-z0-9_]{1,62}", name):
            continue
        schema = action.get("input")
        if not isinstance(schema, dict):
            schema = {"type": "object"}
        specs.append(
            {
                "key": f"{package_id}.{name}",
                "name": f"{package_id}.{name}",
                "description": str(action.get("description") or name)[:1000],
                "parameters_schema": schema,
                "input_schema": schema,
                "agent_callable": True,
                "side_effects": "write" if action.get("effect") == "write" else "read",
                "_package_installation_id": connector_id,
                "_package_action": name,
            }
        )
    for spec in specs:
        try:
            register_workspace_tools(workspace_id, bundle, [spec])
        except HookMisconfiguredError:
            logging.getLogger(__name__).warning(
                "package action tool %s is already registered", spec["key"]
            )


async def refresh_package_action_tools() -> None:
    """Re-register tools after a rescan. A rejected package loses its tools."""
    import logging

    try:
        rows = await Connector.find({"context.mapping_profile": _PACKAGE})
    except Exception as exc:  # noqa: BLE001
        logging.getLogger(__name__).warning(
            "package action tool refresh failed: %s", exc
        )
        return
    for row in rows:
        sync_package_action_tools(row)


async def rehydrate_package_action_tools() -> None:
    """Register tools for enabled package installations after a restart."""
    import logging

    try:
        rows = await Connector.find(
            {"context.mapping_profile": _PACKAGE, "context.enabled": True}
        )
    except Exception as exc:  # noqa: BLE001
        logging.getLogger(__name__).warning(
            "package action tool rehydrate failed: %s", exc
        )
        return
    for row in rows:
        sync_package_action_tools(row)


def _health_expect_status(
    manifest: dict[str, Any], package_id: str
) -> list[int] | None:
    """Return accepted health statuses from the snapshot, then the package."""

    def declared(source: Any) -> list[int] | None:
        if not isinstance(source, dict) or "health" not in source:
            return None
        health = source.get("health")
        if not isinstance(health, dict):
            raise ValueError("package does not declare health statuses")
        if "expect_status" not in health:
            return None
        raw = health.get("expect_status")
        if (
            not isinstance(raw, list)
            or not raw
            or any(
                isinstance(item, bool)
                or not isinstance(item, int)
                or item < 200
                or item > 399
                for item in raw
            )
        ):
            raise ValueError("package does not declare health statuses")
        return [int(item) for item in raw]

    found = declared(manifest)
    if found is None:
        from app.connectors.package_runtime import admitted_manifest

        found = declared(admitted_manifest(package_id) or {})
    return found


def _health_request(manifest: dict[str, Any], package_id: str) -> dict[str, Any] | None:
    """Return the health request from the snapshot, then the admitted package."""

    def declared(source: Any) -> dict[str, Any] | None:
        if not isinstance(source, dict) or "health" not in source:
            return None
        health = source.get("health")
        if not isinstance(health, dict) or "request" not in health:
            return None
        request = health.get("request")
        if not isinstance(request, dict):
            raise ValueError("package does not declare a health probe")
        method = str(request.get("method") or "").upper()
        path = request.get("path")
        if (
            method not in {"GET", "POST", "PUT", "PATCH", "DELETE"}
            or not isinstance(path, str)
            or not path.startswith("/")
        ):
            raise ValueError("package does not declare a health probe")
        return request

    found = declared(manifest)
    if found is None:
        from app.connectors.package_runtime import admitted_manifest

        found = declared(admitted_manifest(package_id) or {})
    return found


async def set_enabled(
    connector_id: str, actor_id: str, enabled: bool, workspace_id: str | None = None
) -> Connector:
    """Disable immediately, or enable only after the health probe succeeds."""
    connector = await _owned(connector_id, actor_id, workspace_id)

    async def _claim_enable(row: Connector) -> Connector:
        fresh = await Connector.get(row.id)
        if (
            fresh is None
            or fresh.package_revision != row.package_revision
            or getattr(fresh, "updated_at", None) != getattr(row, "updated_at", None)
        ):
            raise ResourceConflictError("Connector changed before it was enabled")
        _require_admitted(fresh)
        fresh.health_status = "healthy"
        fresh.last_error = None
        fresh.enabled = True
        await fresh.save()
        return fresh

    async def _record_probe_failure(row: Connector, message: str) -> None:
        fresh = await Connector.get(row.id)
        if (
            fresh is None
            or fresh.package_revision != row.package_revision
            or getattr(fresh, "updated_at", None) != getattr(row, "updated_at", None)
        ):
            return
        fresh.health_status = "error"
        fresh.last_error = message
        fresh.enabled = False
        await fresh.save()
        sync_package_action_tools(fresh)

    if not enabled:
        connector.enabled = False
        await connector.save()
        fresh = await Connector.get(connector.id)
        if (
            fresh is not None
            and not fresh.enabled
            and fresh.package_revision == connector.package_revision
            and dict(fresh.pending_review or {})
        ):
            fresh.pending_review = {}
            await fresh.save()
            sync_package_action_tools(fresh)
            return fresh
        result = fresh or connector
        sync_package_action_tools(result)
        return result
    _require_admitted(connector)
    manifest = _installed_manifest(connector)
    mcp = (manifest.get("capabilities") or {}).get("mcp")
    if mcp is not None:
        from app.agentive.connectors.mcp_client import probe_mcp_health

        probe = await probe_mcp_health(
            connector.auth_state or {}, connector.package_config or {}, manifest
        )
        if not probe["ok"]:
            await _record_probe_failure(connector, "MCP health probe failed")
            raise ServiceUnavailableError("Connector MCP health probe failed")
        enabled_row = await _claim_enable(connector)
        sync_package_action_tools(enabled_row)
        return enabled_row
    request = _health_request(manifest, installation_package_id(connector))
    if request is None:
        raise BadRequestError("Enable requires a health probe")
    secret_values = decrypt_auth_state(connector.auth_state or {})
    config = dict(connector.package_config or {})
    if (manifest.get("auth") or {}).get("type") == "oauth2":
        if not secret_values.get("access_token"):
            raise BadRequestError("Authorize this connector before enabling it")
        realm_id = secret_values.get("realm_id")
        if realm_id:
            config["realm_id"] = str(realm_id)
        http = manifest.get("http") or {}
        named_host = (http.get("hosts") or {}).get(secret_values.get("environment"))
        if named_host:
            manifest = {**manifest, "http": {**http, "base_url": named_host}}
    statuses = _health_expect_status(manifest, installation_package_id(connector))
    try:
        await perform_request(
            _with_package_configuration(
                _with_package_auth(
                    _with_package_response_limit(
                        _with_package_timeout(
                            _with_package_base(
                                manifest, installation_package_id(connector)
                            ),
                            installation_package_id(connector),
                        ),
                        installation_package_id(connector),
                    ),
                    installation_package_id(connector),
                ),
                installation_package_id(connector),
            ),
            request,
            config,
            secret_values,
            ok_statuses=statuses,
        )
    except (httpx.HTTPError, ValueError) as exc:
        await _record_probe_failure(connector, "health probe failed")
        raise ServiceUnavailableError("Connector health probe failed") from exc
    enabled_row = await _claim_enable(connector)
    sync_package_action_tools(enabled_row)
    return enabled_row


async def remove_package(
    connector_id: str, actor_id: str, workspace_id: str | None = None
) -> None:
    """Disable the installation, clear its secret handles, then delete it.

    A failed delete leaves the disabled row so Remove can be retried. The
    secret handles are already gone, so a later call cannot use them.
    """
    connector = await _owned(connector_id, actor_id, workspace_id)
    connector.enabled = False
    await connector.save()
    sync_package_action_tools(connector)
    connector.auth_state = {}
    connector.package_previous = {}
    connector.pending_review = {}
    await connector.save()
    await connector.delete()


def _with_package_base(manifest: dict[str, Any], package_id: str) -> dict[str, Any]:
    """Use the snapshot base URL, or the admitted package base URL."""
    http = manifest.get("http") if isinstance(manifest, dict) else None
    if isinstance(http, dict) and "base_url" in http:
        raw = http.get("base_url")
        if not isinstance(raw, str) or not raw.strip():
            raise ValueError("package does not declare a base URL")
        base = raw.strip()
    else:
        from app.connectors.package_runtime import admitted_manifest

        admitted = admitted_manifest(package_id) or {}
        admitted_http = admitted.get("http")
        if not isinstance(admitted_http, dict) or "base_url" not in admitted_http:
            raise ValueError("package does not declare a base URL")
        raw = admitted_http.get("base_url")
        if not isinstance(raw, str) or not raw.strip():
            raise ValueError("package does not declare a base URL")
        base = raw.strip()
    if not base.startswith("https://") and "{{" not in base:
        raise ValueError("package does not declare a base URL")
    if isinstance(http, dict) and http.get("base_url") == base:
        return manifest
    copied = dict(manifest)
    copied_http = dict(http or {})
    copied_http["base_url"] = base
    copied["http"] = copied_http
    return copied


def _with_package_timeout(manifest: dict[str, Any], package_id: str) -> dict[str, Any]:
    """Use the snapshot timeout, or the admitted package timeout."""

    def declared(http: Any) -> float | None:
        if not isinstance(http, dict) or "timeout_seconds" not in http:
            return None
        raw = http.get("timeout_seconds")
        if (
            isinstance(raw, bool)
            or not isinstance(raw, (int, float))
            or raw <= 0
            or raw > 30
        ):
            raise ValueError("package does not declare a timeout")
        return float(raw)

    http = manifest.get("http") if isinstance(manifest, dict) else None
    found = declared(http)
    if found is None:
        from app.connectors.package_runtime import admitted_manifest

        admitted = admitted_manifest(package_id) or {}
        admitted_http = admitted.get("http") if isinstance(admitted, dict) else None
        found = declared(admitted_http)
    timeout = 10.0 if found is None else found
    if isinstance(http, dict) and http.get("timeout_seconds") == timeout:
        return manifest
    copied = dict(manifest)
    copied_http = dict(http or {})
    copied_http["timeout_seconds"] = timeout
    copied["http"] = copied_http
    return copied


def _with_package_response_limit(
    manifest: dict[str, Any], package_id: str
) -> dict[str, Any]:
    """Use the snapshot response limit, or the admitted package limit."""

    def declared(http: Any) -> int | None:
        if not isinstance(http, dict) or "max_response_bytes" not in http:
            return None
        raw = http.get("max_response_bytes")
        if (
            isinstance(raw, bool)
            or not isinstance(raw, int)
            or raw < 1024
            or raw > 1048576
        ):
            raise ValueError("package does not declare a response limit")
        return raw

    http = manifest.get("http") if isinstance(manifest, dict) else None
    found = declared(http)
    if found is None:
        from app.connectors.package_runtime import admitted_manifest

        admitted = admitted_manifest(package_id) or {}
        admitted_http = admitted.get("http") if isinstance(admitted, dict) else None
        found = declared(admitted_http)
    limit = 262144 if found is None else found
    if isinstance(http, dict) and http.get("max_response_bytes") == limit:
        return manifest
    copied = dict(manifest)
    copied_http = dict(http or {})
    copied_http["max_response_bytes"] = limit
    copied["http"] = copied_http
    return copied


def _with_package_auth(manifest: dict[str, Any], package_id: str) -> dict[str, Any]:
    """Use the snapshot auth block, or the admitted package auth block."""

    def declared(source: Any) -> dict[str, Any] | None:
        if not isinstance(source, dict) or "auth" not in source:
            return None
        auth = source.get("auth")
        if not isinstance(auth, dict):
            raise ValueError("package does not declare auth")
        kind = auth.get("type") or "none"
        if kind not in {"none", "api_key", "operator", "oauth2", "mcp_oauth"}:
            raise ValueError("package does not declare auth")
        if kind == "api_key":
            inject = auth.get("inject")
            if (
                not isinstance(inject, dict)
                or inject.get("in") != "header"
                or not str(inject.get("name") or "").strip()
                or not str(auth.get("secret") or "").strip()
            ):
                raise ValueError("package does not declare auth")
        if kind == "operator" and not str(auth.get("env") or "").strip():
            raise ValueError("package does not declare auth")
        return auth

    found = declared(manifest)
    if found is None:
        from app.connectors.package_runtime import admitted_manifest

        found = declared(admitted_manifest(package_id) or {})
    if found is None or manifest.get("auth") == found:
        return manifest
    copied = dict(manifest)
    copied["auth"] = found
    return copied


def _declared_sync(manifest: dict[str, Any], package_id: str) -> dict[str, Any] | None:
    """Return a declarative sync block from the snapshot, then the package."""

    def declared(source: Any) -> dict[str, Any] | None:
        if not isinstance(source, dict):
            return None
        capabilities = source.get("capabilities")
        if not isinstance(capabilities, dict) or "sync" not in capabilities:
            return None
        sync = capabilities.get("sync")
        if not isinstance(sync, dict):
            raise ValueError("package does not declare a sync source")
        if "source" not in sync and "map" not in sync:
            return None
        source_block = sync.get("source")
        mapping = sync.get("map")
        if not isinstance(source_block, dict) or not isinstance(mapping, dict):
            raise ValueError("package does not declare a sync source")
        request = source_block.get("request")
        external_id = mapping.get("external_id")
        if not isinstance(request, dict) or not isinstance(external_id, str):
            raise ValueError("package does not declare a sync source")
        method = str(request.get("method") or "").upper()
        path = request.get("path")
        if (
            method != "GET"
            or not isinstance(path, str)
            or not path.startswith("/")
            or not external_id.startswith("$")
        ):
            raise ValueError("package does not declare a sync source")
        return sync

    found = declared(manifest)
    if found is None:
        from app.connectors.package_runtime import admitted_manifest

        found = declared(admitted_manifest(package_id) or {})
    return found


async def _sync_claimed(fresh: Connector) -> dict[str, Any]:
    """Pull after the caller holds the sync lease."""
    manifest = _installed_manifest(fresh)
    sync = _declared_sync(manifest, installation_package_id(fresh))
    if sync is None:
        raise BadRequestError("This package has no declarative sync source")
    secret_values = decrypt_auth_state(fresh.auth_state or {})
    pulled = dict(manifest)
    capabilities = dict(pulled.get("capabilities") or {})
    capabilities["sync"] = sync
    pulled["capabilities"] = capabilities
    rows, cursor = await pull_declared_sync(
        _with_package_configuration(
            _with_package_auth(
                _with_package_response_limit(
                    _with_package_timeout(
                        _with_package_base(pulled, installation_package_id(fresh)),
                        installation_package_id(fresh),
                    ),
                    installation_package_id(fresh),
                ),
                installation_package_id(fresh),
            ),
            installation_package_id(fresh),
        ),
        dict(fresh.package_config or {}),
        secret_values,
        start_cursor=str(fresh.sync_cursor or ""),
    )
    latest = await Connector.get(fresh.id)
    if (
        latest is None
        or not latest.enabled
        or latest.package_revision != fresh.package_revision
    ):
        raise ResourceConflictError("Connector changed during the sync")
    if cursor and cursor != (latest.sync_cursor or ""):
        latest.sync_cursor = cursor
        await latest.save()
    return {"records": rows, "cursor": latest.sync_cursor or ""}


async def synchronize_installation(
    connector_id: str, actor_id: str, workspace_id: str
) -> dict[str, int]:
    """Materialize the bound package through the same runtime as scheduled sync."""
    from app.services.connectors.sync_runtime import (
        adapter_for_connector,
        sync_one_connector,
    )

    connector = await _owned(connector_id, actor_id, workspace_id)
    _require_admitted(connector)
    if not connector.enabled:
        raise BadRequestError("Connector is disabled")
    if adapter_for_connector(connector) is None:
        raise BadRequestError("This package has no Track sync adapter")
    return await sync_one_connector(connector)


async def sync_package(
    connector_id: str, actor_id: str, workspace_id: str | None = None
) -> dict[str, Any]:
    """Pull one declarative sync source. Entry writes stay with the sync runtime."""
    connector = await _owned(connector_id, actor_id, workspace_id)
    fresh = await Connector.get(connector.id)
    if (
        fresh is None
        or not fresh.enabled
        or fresh.package_revision != connector.package_revision
        or installation_package_id(fresh) not in current_admission().packages
    ):
        raise ResourceConflictError("Connector changed before the sync ran")
    from app.services.connectors.sync_lease import claim_sync, release_sync

    lease = await claim_sync(fresh)
    if lease is None:
        raise ResourceConflictError("Connector sync is already running")
    try:
        result = await _sync_claimed(fresh)
    finally:
        await release_sync(lease)
    await _call_receipt(fresh, name="sync", effect="read", status="ok")
    return result


def _package_action(
    manifest: dict[str, Any], package_id: str, action_name: str
) -> dict[str, Any] | None:
    """Return one action from the snapshot, then the admitted package."""

    def declared(source: Any) -> dict[str, Any] | None:
        if not isinstance(source, dict):
            return None
        capabilities = source.get("capabilities")
        if not isinstance(capabilities, dict) or "actions" not in capabilities:
            return None
        actions = capabilities.get("actions")
        if not isinstance(actions, list):
            raise ValueError("package does not declare an action")
        for item in actions:
            if not isinstance(item, dict) or item.get("name") != action_name:
                continue
            effect = item.get("effect")
            request = item.get("request")
            if effect not in {"read", "write"} or not isinstance(request, dict):
                raise ValueError("package does not declare an action")
            method = str(request.get("method") or "").upper()
            path = request.get("path")
            if (
                method not in {"GET", "POST", "PUT", "PATCH", "DELETE"}
                or not isinstance(path, str)
                or not path.startswith("/")
            ):
                raise ValueError("package does not declare an action")
            if effect == "read" and method != "GET":
                raise BadRequestError("A read action must use GET")
            return item
        return None

    found = declared(manifest)
    if found is None:
        from app.connectors.package_runtime import admitted_manifest

        found = declared(admitted_manifest(package_id) or {})
    return found


async def invoke_action(
    connector_id: str,
    actor_id: str,
    action_name: str,
    payload: dict[str, Any],
    workspace_id: str | None = None,
) -> dict[str, Any]:
    """Run a read, or stage a write until approve_review."""
    connector = await _owned(connector_id, actor_id, workspace_id)
    _require_admitted(connector)
    if not connector.enabled:
        raise BadRequestError("Connector is disabled")
    manifest = _installed_manifest(connector)
    action = _package_action(manifest, installation_package_id(connector), action_name)
    if action is None:
        raise ResourceNotFoundError("Action is not declared")
    _require_action_input(action, payload)
    if action["effect"] == "write":
        return await _stage(connector, "action", action_name, payload)
    return await _execute(connector, manifest, action, payload)


def _webhook_skew(manifest: dict[str, Any], package_id: str) -> int:
    """Return the replay window from the snapshot, then the admitted package."""

    def declared(source: Any) -> int | None:
        if not isinstance(source, dict):
            return None
        channel = (source.get("capabilities") or {}).get("channel")
        if not isinstance(channel, dict):
            return None
        ingress = channel.get("ingress")
        if not isinstance(ingress, dict):
            return None
        verify = ingress.get("verify")
        if not isinstance(verify, dict) or "max_skew_seconds" not in verify:
            return None
        raw = verify.get("max_skew_seconds")
        if isinstance(raw, bool) or not isinstance(raw, int) or raw < 30 or raw > 900:
            raise ValueError("package does not declare a replay window")
        return raw

    found = declared(manifest)
    if found is None:
        from app.connectors.package_runtime import admitted_manifest

        found = declared(admitted_manifest(package_id) or {})
    return 300 if found is None else found


def _webhook_timestamp(manifest: dict[str, Any], package_id: str) -> str | None:
    """Return the timestamp path from the snapshot, then the admitted package."""

    def declared(source: Any) -> str | None:
        if not isinstance(source, dict):
            return None
        channel = (source.get("capabilities") or {}).get("channel")
        if not isinstance(channel, dict):
            return None
        ingress = channel.get("ingress")
        if not isinstance(ingress, dict) or "timestamp" not in ingress:
            return None
        raw = ingress.get("timestamp")
        if not isinstance(raw, str) or not raw.startswith("$"):
            raise ValueError("package does not declare a webhook timestamp")
        return raw

    found = declared(manifest)
    if found is None:
        from app.connectors.package_runtime import admitted_manifest

        found = declared(admitted_manifest(package_id) or {})
    return found


def _webhook_event_id(manifest: dict[str, Any], package_id: str) -> str | None:
    """Return the event-id path from the snapshot, then the admitted package."""

    def declared(source: Any) -> str | None:
        if not isinstance(source, dict):
            return None
        channel = (source.get("capabilities") or {}).get("channel")
        if not isinstance(channel, dict):
            return None
        ingress = channel.get("ingress")
        if not isinstance(ingress, dict) or "event_id" not in ingress:
            return None
        raw = ingress.get("event_id")
        if not isinstance(raw, str) or not raw.startswith("$"):
            raise ValueError("package does not declare a webhook event id")
        return raw

    found = declared(manifest)
    if found is None:
        from app.connectors.package_runtime import admitted_manifest

        found = declared(admitted_manifest(package_id) or {})
    return found


def _webhook_sender(manifest: dict[str, Any], package_id: str) -> str:
    """Return the sender path from the snapshot, then the admitted package."""

    def declared(source: Any) -> str | None:
        if not isinstance(source, dict):
            return None
        channel = (source.get("capabilities") or {}).get("channel")
        if not isinstance(channel, dict):
            return None
        ingress = channel.get("ingress")
        if not isinstance(ingress, dict) or "sender" not in ingress:
            return None
        raw = ingress.get("sender")
        if not isinstance(raw, str) or not raw.startswith("$"):
            raise ValueError("package does not declare a webhook sender")
        return raw

    found = declared(manifest)
    if found is None:
        from app.connectors.package_runtime import admitted_manifest

        found = declared(admitted_manifest(package_id) or {})
    if not found:
        raise ValueError("package does not declare a webhook sender")
    return found


def _webhook_text(manifest: dict[str, Any], package_id: str) -> str:
    """Return the message path from the snapshot, then the admitted package."""

    def declared(source: Any) -> str | None:
        if not isinstance(source, dict):
            return None
        channel = (source.get("capabilities") or {}).get("channel")
        if not isinstance(channel, dict):
            return None
        ingress = channel.get("ingress")
        if not isinstance(ingress, dict) or "text" not in ingress:
            return None
        raw = ingress.get("text")
        if not isinstance(raw, str) or not raw.startswith("$"):
            raise ValueError("package does not declare a webhook message")
        return raw

    found = declared(manifest)
    if found is None:
        from app.connectors.package_runtime import admitted_manifest

        found = declared(admitted_manifest(package_id) or {})
    if not found:
        raise ValueError("package does not declare a webhook message")
    return found


def _webhook_verify_header(manifest: dict[str, Any], package_id: str) -> str:
    """Return the signature header from the snapshot, then the admitted package."""

    def declared(source: Any) -> str | None:
        if not isinstance(source, dict):
            return None
        channel = (source.get("capabilities") or {}).get("channel")
        if not isinstance(channel, dict):
            return None
        ingress = channel.get("ingress")
        if not isinstance(ingress, dict):
            return None
        verify = ingress.get("verify")
        if not isinstance(verify, dict) or "header" not in verify:
            return None
        raw = verify.get("header")
        if not isinstance(raw, str) or not raw.strip():
            raise ValueError("package does not declare a webhook header")
        return raw.strip()

    found = declared(manifest)
    if found is None:
        from app.connectors.package_runtime import admitted_manifest

        found = declared(admitted_manifest(package_id) or {})
    if not found:
        raise ValueError("package does not declare a webhook header")
    return found


def _webhook_verify_secret(manifest: dict[str, Any], package_id: str) -> str:
    """Return the secret field name from the snapshot, then the admitted package."""

    def declared(source: Any) -> str | None:
        if not isinstance(source, dict):
            return None
        channel = (source.get("capabilities") or {}).get("channel")
        if not isinstance(channel, dict):
            return None
        ingress = channel.get("ingress")
        if not isinstance(ingress, dict):
            return None
        verify = ingress.get("verify")
        if not isinstance(verify, dict) or "secret" not in verify:
            return None
        raw = verify.get("secret")
        if not isinstance(raw, str) or not raw.strip():
            raise ValueError("package does not declare a webhook secret")
        return raw.strip()

    found = declared(manifest)
    if found is None:
        from app.connectors.package_runtime import admitted_manifest

        found = declared(admitted_manifest(package_id) or {})
    if not found:
        raise ValueError("package does not declare a webhook secret")
    return found


def _webhook_verify_scheme(manifest: dict[str, Any], package_id: str) -> str:
    """Return the signature scheme from the snapshot, then the admitted package."""

    def declared(source: Any) -> str | None:
        if not isinstance(source, dict):
            return None
        channel = (source.get("capabilities") or {}).get("channel")
        if not isinstance(channel, dict):
            return None
        ingress = channel.get("ingress")
        if not isinstance(ingress, dict):
            return None
        verify = ingress.get("verify")
        if not isinstance(verify, dict) or "scheme" not in verify:
            return None
        raw = verify.get("scheme")
        if raw != "hmac_sha256":
            raise ValueError("package does not declare a webhook scheme")
        return raw

    found = declared(manifest)
    if found is None:
        from app.connectors.package_runtime import admitted_manifest

        found = declared(admitted_manifest(package_id) or {})
    if not found:
        raise ValueError("package does not declare a webhook scheme")
    return found


def _webhook_send_request(manifest: dict[str, Any], package_id: str) -> dict[str, Any]:
    """Return the outbound message request from the snapshot, then the package."""

    def declared(source: Any) -> dict[str, Any] | None:
        if not isinstance(source, dict):
            return None
        channel = (source.get("capabilities") or {}).get("channel")
        if not isinstance(channel, dict):
            return None
        send = channel.get("send")
        if not isinstance(send, dict) or "request" not in send:
            return None
        request = send.get("request")
        if not isinstance(request, dict):
            raise ValueError("package does not declare an outbound message")
        method = str(request.get("method") or "").upper()
        path = request.get("path")
        if (
            method not in {"POST", "PUT", "PATCH"}
            or not isinstance(path, str)
            or not path.startswith("/")
        ):
            raise ValueError("package does not declare an outbound message")
        return request

    found = declared(manifest)
    if found is None:
        from app.connectors.package_runtime import admitted_manifest

        found = declared(admitted_manifest(package_id) or {})
    if not found:
        raise ValueError("package does not declare an outbound message")
    return found


def _channel_mode(manifest: dict[str, Any], package_id: str) -> str:
    """Return the channel mode from the snapshot, then the admitted package."""

    def declared(source: Any) -> str | None:
        if not isinstance(source, dict):
            return None
        channel = (source.get("capabilities") or {}).get("channel")
        if not isinstance(channel, dict) or "mode" not in channel:
            return None
        raw = channel.get("mode")
        if raw not in {"webhook", "managed-text-v1"}:
            raise ValueError("package does not declare a channel mode")
        return str(raw)

    found = declared(manifest)
    if found is None:
        from app.connectors.package_runtime import admitted_manifest

        found = declared(admitted_manifest(package_id) or {})
    if not found:
        raise ValueError("package does not declare a channel mode")
    return found


def _replay_window(verify: dict[str, Any]) -> int:
    """Return the webhook skew window. The default is 300 seconds."""
    raw = verify.get("max_skew_seconds", 300)
    if isinstance(raw, bool) or not isinstance(raw, int):
        return 300
    return min(max(raw, 30), 900)


def _require_fresh_webhook(
    payload: Any, ingress: dict[str, Any], verify: dict[str, Any]
) -> None:
    """Reject a declared timestamp that is outside the replay window."""
    import time

    path = ingress.get("timestamp")
    if not path:
        return
    raw = select_output(payload, path)
    try:
        stamp = int(raw)
    except (TypeError, ValueError):
        raise BadRequestError("Webhook timestamp is invalid") from None
    if abs(int(time.time()) - stamp) > _replay_window(verify):
        raise BadRequestError("Webhook timestamp is outside the replay window")


def _identity_matches(row: Any, package_id: str, owner: str, sender: str) -> bool:
    """True when this verified identity is the connector owner's link for the sender."""
    return bool(
        getattr(row, "verified", False)
        and getattr(row, "channel", "") == package_id
        and getattr(row, "user_id", "") == owner
        and getattr(row, "channel_user_id", "") == sender
    )


async def _sender_is_linked(connector: Connector, sender: str) -> bool:
    """Accept a sender only through the owner's verified channel identity."""
    from app.agentive.nodes import ChannelIdentity

    rows = await ChannelIdentity.find(
        {"context.channel_user_id": sender, "context.verified": True}
    )
    package_id = installation_package_id(connector)
    owner = connector.owner or ""
    if not any(_identity_matches(row, package_id, owner, sender) for row in rows):
        return False
    fresh = await Connector.get(connector.id)
    if (
        fresh is None
        or not fresh.enabled
        or fresh.owner != owner
        or installation_package_id(fresh) != package_id
        or installation_package_id(fresh) not in current_admission().packages
    ):
        return False
    return True


async def _start_linked_turn(
    connector: Connector, sender: str, text: str, event_id: str
) -> None:
    """Queue one chat turn for a sender that is still linked to the owner."""
    from app.api.ai_chat import _resolve_agent_for_new_thread
    from app.schemas.agentive.work import ChatTurnSubmissionRequest
    from app.services.chat_providers.registry import get_registry
    from app.services.chat_threads import (
        create_thread,
        get_thread_by_session,
        update_provider_session,
    )
    from app.services.chat_turn_submissions import submit_chat_turn

    if not await _sender_is_linked(connector, sender):
        raise ResourceConflictError("Sender is no longer linked")
    user_id = connector.owner or ""
    workspace_id = getattr(connector, "workspace_id", "") or ""
    if not user_id or not workspace_id:
        raise BadRequestError("Webhook delivery needs an owner and a workspace")
    session_id = f"pkg:{installation_package_id(connector)}:{workspace_id}:{sender}"
    existing = await get_thread_by_session(session_id)
    if (
        existing is not None
        and existing.user_id == user_id
        and existing.workspace_id == workspace_id
        and not getattr(existing, "archived", False)
    ):
        thread = existing
    else:
        provider = get_registry().get("integral_native")
        if provider is None:
            raise ServiceUnavailableError("Chat provider is unavailable")
        thread = await create_thread(
            user_id=user_id,
            provider_id="integral_native",
            workspace_id=workspace_id,
            agent_id=await _resolve_agent_for_new_thread(
                user_id=user_id,
                workspace_id=workspace_id,
                provider=provider,
                body_agent_id="",
            ),
        )
        await update_provider_session(thread, session_id)
    await submit_chat_turn(
        ChatTurnSubmissionRequest(
            principal_id=user_id,
            workspace_id=workspace_id,
            thread_id=thread.id,
            client_request_id=event_id or session_id,
            parts=[{"type": "text", "text": text}],
        )
    )


async def receive_package_webhook(
    connector_id: str, body: bytes, headers
) -> dict[str, Any]:
    """Accept one signed webhook for an enabled channel package."""
    connector = await Connector.get(connector_id)
    if (
        connector is None
        or connector.mapping_profile != _PACKAGE
        or not connector.enabled
    ):
        raise ResourceNotFoundError("Connector installation not found")
    manifest = _installed_manifest(connector)
    channel = (manifest.get("capabilities") or {}).get("channel") or {}
    ingress = dict(channel.get("ingress") or {})
    verify = dict(ingress.get("verify") or {})
    verify["max_skew_seconds"] = _webhook_skew(
        manifest, installation_package_id(connector)
    )
    timestamp = _webhook_timestamp(manifest, installation_package_id(connector))
    if timestamp:
        ingress["timestamp"] = timestamp
    else:
        ingress.pop("timestamp", None)
    event_path = _webhook_event_id(manifest, installation_package_id(connector))
    if event_path:
        ingress["event_id"] = event_path
    else:
        ingress.pop("event_id", None)
    if _channel_mode(manifest, installation_package_id(connector)) != "webhook":
        raise BadRequestError("This package has no signed webhook")
    verify["scheme"] = _webhook_verify_scheme(
        manifest, installation_package_id(connector)
    )
    verify["header"] = _webhook_verify_header(
        manifest, installation_package_id(connector)
    )
    verify["secret"] = _webhook_verify_secret(
        manifest, installation_package_id(connector)
    )
    ingress["sender"] = _webhook_sender(manifest, installation_package_id(connector))
    ingress["text"] = _webhook_text(manifest, installation_package_id(connector))
    secret = str(
        decrypt_auth_state(connector.auth_state or {}).get(verify["secret"]) or ""
    )
    header = headers.get(verify["header"]) or ""
    if not secret or not webhook_signature_ok(body, header, secret):
        raise BadRequestError("Webhook signature is invalid")
    try:
        payload = json.loads(body)
    except json.JSONDecodeError as exc:
        raise BadRequestError("Webhook body must be JSON") from exc
    _require_fresh_webhook(payload, ingress, verify)
    event_id = select_output(payload, ingress.get("event_id"))
    if event_id and connector.sync_cursor == str(event_id):
        raise ResourceConflictError("Webhook event was already accepted")
    sender = str(select_output(payload, ingress.get("sender")) or "")
    text = str(select_output(payload, ingress.get("text")) or "")
    linked = False
    if sender and text.strip():
        linked = await _sender_is_linked(connector, sender)
    if linked:
        await _start_linked_turn(connector, sender, text.strip(), str(event_id or ""))
    if event_id:
        connector.sync_cursor = str(event_id)
        await connector.save()
    await _call_receipt(connector, name="webhook", effect="read", status="ok")
    return {"accepted": True, "linked": linked, "turn_started": linked}


async def send_package_message(
    connector_id: str,
    actor_id: str,
    payload: dict[str, Any],
    workspace_id: str | None = None,
) -> dict[str, Any]:
    """Send one outbound webhook message after a fresh authorization check."""
    connector = await _owned(connector_id, actor_id, workspace_id)
    fresh = await Connector.get(connector.id)
    if (
        fresh is None
        or not fresh.enabled
        or fresh.package_revision != connector.package_revision
        or installation_package_id(fresh) not in current_admission().packages
    ):
        raise ResourceConflictError("Connector changed before the message was sent")
    manifest = _installed_manifest(fresh)
    if _channel_mode(manifest, installation_package_id(fresh)) != "webhook":
        raise BadRequestError("This package has no outbound message")
    request = _webhook_send_request(manifest, installation_package_id(fresh))
    body = await perform_request(
        _with_package_configuration(
            _with_package_auth(
                _with_package_response_limit(
                    _with_package_timeout(
                        _with_package_base(manifest, installation_package_id(fresh)),
                        installation_package_id(fresh),
                    ),
                    installation_package_id(fresh),
                ),
                installation_package_id(fresh),
            ),
            installation_package_id(fresh),
        ),
        request,
        dict(fresh.package_config or {}),
        decrypt_auth_state(fresh.auth_state or {}),
        payload,
    )
    await _call_receipt(fresh, name="send", effect="write", status="ok")
    return {"status": "ok", "output": body}


async def invoke_mcp_tool(
    connector_id: str,
    actor_id: str,
    tool_name: str,
    payload: dict[str, Any],
    workspace_id: str | None = None,
) -> dict[str, Any]:
    """Run a manifest read-only MCP tool. Every other tool waits for review."""
    connector = await _owned(connector_id, actor_id, workspace_id)
    _require_admitted(connector)
    if not connector.enabled:
        raise BadRequestError("Connector is disabled")
    manifest = _installed_manifest(connector)
    mcp = (manifest.get("capabilities") or {}).get("mcp")
    if not isinstance(mcp, dict):
        raise BadRequestError("This package has no MCP server")
    if tool_name not in set(mcp.get("read_only_tools") or []):
        return await _stage(connector, "mcp", tool_name, payload)
    fresh = await Connector.get(connector.id)
    if (
        fresh is None
        or not fresh.enabled
        or fresh.package_revision != connector.package_revision
        or installation_package_id(fresh) not in current_admission().packages
    ):
        raise ResourceConflictError("Connector changed before the tool ran")
    current_manifest = _installed_manifest(fresh)
    current_mcp = (current_manifest.get("capabilities") or {}).get("mcp")
    if not isinstance(current_mcp, dict) or tool_name not in set(
        current_mcp.get("read_only_tools") or []
    ):
        return await _stage(fresh, "mcp", tool_name, payload)
    output = await call_mcp_tool(
        fresh.auth_state or {},
        tool_name,
        payload,
        config=dict(fresh.package_config or {}),
        manifest=current_manifest,
    )
    await _call_receipt(fresh, name=tool_name, effect="read", status="ok")
    return {"status": "ok", "output": output}


async def _stage(
    connector: Connector, kind: str, name: str, payload: dict[str, Any]
) -> dict[str, Any]:
    review_id = secrets.token_hex(8)
    connector.pending_review = {
        "id": review_id,
        "kind": kind,
        "action": name,
        "input": payload,
        "status": "pending",
    }
    await connector.save()
    await _call_receipt(connector, name=name, effect="write", status="review")
    return {"status": "review", "review_id": review_id}


async def approve_review(
    connector_id: str, actor_id: str, review_id: str, workspace_id: str | None = None
) -> dict[str, Any]:
    """Execute a staged write after the pending review id matches."""
    connector = await _owned(connector_id, actor_id, workspace_id)
    pending = dict(connector.pending_review or {})
    if pending.get("status") != "pending" or pending.get("id") != review_id:
        raise ResourceConflictError("Review is no longer pending")
    manifest = _installed_manifest(connector)
    kind = pending.get("kind") or "action"
    connector.pending_review = {}
    await connector.save()
    try:
        if kind == "mcp":
            fresh = await Connector.get(connector.id)
            if (
                fresh is None
                or not fresh.enabled
                or fresh.package_revision != connector.package_revision
                or installation_package_id(fresh) not in current_admission().packages
            ):
                raise ResourceConflictError("Connector changed before the tool ran")
            current_manifest = _installed_manifest(fresh)
            current_mcp = (current_manifest.get("capabilities") or {}).get("mcp")
            if not isinstance(current_mcp, dict):
                raise ResourceConflictError("MCP server is no longer declared")
            output = await call_mcp_tool(
                fresh.auth_state or {},
                pending["action"],
                dict(pending.get("input") or {}),
                config=dict(fresh.package_config or {}),
                manifest=current_manifest,
            )
            result = {"status": "ok", "output": output}
        else:
            action = next(
                item
                for item in manifest["capabilities"].get("actions") or []
                if item["name"] == pending["action"]
            )
            result = await _execute(
                connector, manifest, action, dict(pending.get("input") or {})
            )
    except Exception:
        fresh = await Connector.get(connector.id)
        if fresh is not None and not dict(fresh.pending_review or {}):
            fresh.pending_review = pending
            await fresh.save()
        raise
    if kind == "mcp":
        await _call_receipt(
            connector, name=str(pending["action"]), effect="write", status="ok"
        )
    return result


async def _execute(
    connector: Connector,
    manifest: dict[str, Any],
    action: dict[str, Any],
    payload: dict[str, Any],
) -> dict[str, Any]:
    """Run the request only if the installation and action are still current."""
    fresh = await Connector.get(connector.id)
    if (
        fresh is None
        or not fresh.enabled
        or fresh.package_revision != connector.package_revision
        or installation_package_id(fresh) not in current_admission().packages
    ):
        raise ResourceConflictError("Connector changed before the action ran")
    current = _installed_manifest(fresh)
    current_action = _package_action(
        current, installation_package_id(fresh), str(action.get("name") or "")
    )
    if current_action is None or current_action.get("effect") != action.get("effect"):
        raise ResourceConflictError("Action is no longer declared")
    if current_action.get("effect") != "write":
        method = str((current_action.get("request") or {}).get("method") or "")
        if method.upper() != "GET":
            raise BadRequestError("A read action must use GET")
    _require_action_input(current_action, payload)
    secret_values = decrypt_auth_state(fresh.auth_state or {})
    body = await perform_request(
        _with_package_configuration(
            _with_package_auth(
                _with_package_response_limit(
                    _with_package_timeout(
                        _with_package_base(current, installation_package_id(fresh)),
                        installation_package_id(fresh),
                    ),
                    installation_package_id(fresh),
                ),
                installation_package_id(fresh),
            ),
            installation_package_id(fresh),
        ),
        current_action["request"],
        dict(fresh.package_config or {}),
        secret_values,
        payload,
    )
    selected = select_output(body, (current_action.get("output") or {}).get("select"))
    await _call_receipt(
        fresh,
        name=str(current_action.get("name") or ""),
        effect=_call_effect(current_action),
        status="ok",
    )
    return {"status": "ok", "output": _limit_items(selected, current_action)}


def _require_action_input(action: dict[str, Any], payload: dict[str, Any]) -> None:
    """Reject arguments that do not match the action's JSON Schema."""
    from jsonschema import Draft202012Validator
    from jsonschema.exceptions import ValidationError

    schema = action.get("input")
    if not isinstance(schema, dict):
        raise BadRequestError("Action input is not declared")
    try:
        Draft202012Validator(schema).validate(payload)
    except ValidationError as exc:
        path = ".".join(str(part) for part in exc.absolute_path)
        message = "Action input does not match the schema"
        if path:
            message = f"{message}: {path}"
        raise BadRequestError(message) from exc


def _limit_items(value: Any, action: dict[str, Any]) -> Any:
    """Cap a list result. The default is 50 and the maximum is 200."""
    raw = (action.get("output") or {}).get("max_items", 50)
    limit = raw if isinstance(raw, int) and not isinstance(raw, bool) else 50
    limit = min(max(limit, 1), 200)
    if isinstance(value, list) and len(value) > limit:
        return value[:limit]
    return value


def _call_effect(action: dict[str, Any]) -> str:
    """Keep the receipt effect to the two declared values."""
    return "write" if action.get("effect") == "write" else "read"


async def _call_receipt(
    connector: Connector, *, name: str, effect: str, status: str
) -> None:
    """Record a package call. The receipt has no input, output, or secrets."""
    from app.services.change_event import emit_change_event

    await emit_change_event(
        actor_kind="human",
        actor_id=getattr(connector, "owner", "") or "",
        action="tool.invoke",
        resource_type="Connector",
        resource_id=connector.id,
        before=None,
        after=None,
        scope=f"user:{getattr(connector, 'owner', '') or ''}",
        details={
            "package_id": installation_package_id(connector),
            "action": str(name)[:80],
            "effect": effect,
            "status": status,
        },
        _workspace_id=getattr(connector, "workspace_id", None) or None,
    )


def scan_now(roots=None):
    """Re-read connector roots into the process admission cache."""
    return rescan(roots)
