"""Read-only package restore checks and strict import storage accounting."""

import asyncio
from copy import deepcopy
from typing import Any

from app.api.errors import BadRequestError
from app.schemas.portable_transfer import PortableApp


async def preflight_packaged_apps(
    apps: list[PortableApp], libraries: dict[str, Any], *, workspace_id: str
) -> None:
    """Check the complete selection before creating the first destination App."""
    from app.services.app_install import (
        apply_schema_defaults,
        bundle_identity_key,
        check_requires_apps,
        find_all_bundle_installs,
        validate_settings_against_schema,
        version_satisfies_min,
    )
    from app.services.entitlements import require_active_entitlement
    from app.services.operational_model_compile import compile_canonical_manifest
    from app.services.operational_model_runtime import slug_manifest_key

    selected: set[str] = set()
    available: dict[str, str] = {}
    for spec in apps:
        if not spec.package_slug:
            continue
        library = libraries[spec.package_slug]
        canonical = await asyncio.to_thread(
            compile_canonical_manifest, manifest=library.manifest
        )
        package = canonical.get("package") or {}
        identity = bundle_identity_key(spec.package_slug, package, library.name)
        if identity in selected or await find_all_bundle_installs(
            workspace_id, identity, library.id
        ):
            raise BadRequestError(
                message="Package import requires a fresh App destination"
            )
        selected.add(identity)
        body = canonical.get("app") or {}
        if body.get("protected_state"):
            # No restore-validation contract exists yet. Archive contents cannot
            # act as an App operation permit, including when records omit fields.
            raise BadRequestError(
                message="This App requires App-owned restore validation, which is not available"
            )
        schema = body.get("settings_schema") or {}
        validate_settings_against_schema(
            apply_schema_defaults(spec.settings, schema), schema
        )
        await require_active_entitlement(
            workspace_id=workspace_id,
            package_meta={**package, "slug": spec.package_slug},
        )
        external = deepcopy(canonical)
        external["app"]["requires_apps"] = []
        for dep in body.get("requires_apps", []):
            key = str(dep.get("key") or "")
            version = next(
                (
                    available[alias]
                    for alias in (key, key.casefold(), slug_manifest_key(key))
                    if alias in available
                ),
                None,
            )
            if version is None or not version_satisfies_min(
                version, str(dep.get("min_version") or "0.0.0")
            ):
                external["app"]["requires_apps"].append(dep)
        await check_requires_apps(external, workspace_id)
        for name in (spec.name, spec.package_slug):
            for alias in (name, name.casefold(), slug_manifest_key(name)):
                available[alias] = str(package.get("version") or "0.0.0")


async def charge_import_storage(workspace_id: str, amount: int) -> None:
    """Charge retained bytes with a quota guard; a failed charge fails restore."""
    if amount <= 0:
        return
    from jvspatial.core.context import get_default_context

    from app.config import settings
    from app.services.workspace_storage_usage import get_usage

    usage = await get_usage(workspace_id)
    if usage is None:
        raise BadRequestError(message="Import workspace is unavailable")
    query: dict[str, Any] = {"id": workspace_id, "entity": "Workspace"}
    if settings.STORAGE_QUOTA_ENFORCE and usage.quota_bytes > 0:
        query["context.storage_bytes_used"] = {"$lte": usage.quota_bytes - amount}
    context = get_default_context()
    await context._evict_from_cache(workspace_id)
    row = await context.database.find_one_and_update(
        "node", query, {"$inc": {"context.storage_bytes_used": amount}}
    )
    if row is None:
        raise BadRequestError(message="Import storage quota changed; retry the import")
