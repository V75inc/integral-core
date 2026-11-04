"""Operator-admitted React releases. Native code has frontend-level privilege.

The artifact signature binds publisher, package and the complete declaration.
Public delivery serves only admitted, digest-checked code, never package paths.
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import os
import re
import tempfile
import time
from collections import OrderedDict
from copy import deepcopy
from pathlib import Path
from typing import Any

import jsonschema

from app.api.errors import (
    BadRequestError,
    InsufficientPermissionsError,
    ResourceNotFoundError,
)
from app.config import settings
from app.schemas.ui_components import UIComponentDeclaration

# Public release metadata only, never an access decision or business data.
# Each reuse still obtains fresh App/definition authority AFTER artifact work.
_mount_candidates: OrderedDict[tuple, tuple] = OrderedDict()


class _ActiveManifest(dict):
    definition_revision: str


def release_payload(publisher: str, slug: str, component: dict) -> bytes:
    """Return the canonical signed release envelope, including schema defaults."""
    declaration = UIComponentDeclaration.model_validate(component).model_dump()
    return json.dumps(
        {
            "format": "integral.ui.v1",
            "publisher": publisher,
            "package": slug,
            "component": declaration,
        },
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
    ).encode()


def _root() -> Path:
    return Path(settings.INTEGRAL_UI_ARTIFACT_ROOT).resolve()


def _verify(metadata: dict, content: bytes) -> None:
    component = UIComponentDeclaration.model_validate(metadata["component"])
    if hashlib.sha256(content).hexdigest() != component.digest:
        raise BadRequestError(message="UI artifact digest mismatch")
    if len(content) > 2 * 1024 * 1024:
        raise BadRequestError(message="UI artifact exceeds 2 MiB")
    publisher, slug = metadata["publisher"], metadata["package"]
    # This is an operator configuration, never a manifest or installed-App setting.
    try:
        publishers = json.loads(settings.INTEGRAL_UI_PUBLISHERS)
        grant = publishers.get(publisher, {})
        key = grant.get("public_key")
        allowed = slug in grant.get("packages", [])
        if allowed and key:
            from nacl.signing import VerifyKey

            VerifyKey(base64.b64decode(key, validate=True)).verify(
                release_payload(publisher, slug, component.model_dump()),
                base64.b64decode(metadata.get("signature", ""), validate=True),
            )
            return
    except Exception as exc:
        raise InsufficientPermissionsError(
            message="UI release signature invalid"
        ) from exc
    development = settings.INTEGRAL_UI_DEVELOPMENT_PACKAGES.split(",")
    if settings.DEBUG and slug in development and metadata.get("development") is True:
        return
    raise InsufficientPermissionsError(message="UI publisher is not admitted")


def _admit(bundle: Path, publisher: str, slug: str, declaration: dict) -> str:
    component = UIComponentDeclaration.model_validate(declaration)
    root = _root() / slug / component.digest
    try:
        cached = json.loads((root / f"release-{component.key}.json").read_text())
    except (OSError, ValueError):
        cached = None
    if cached is not None and (
        cached.get("publisher") == publisher
        and cached.get("package") == slug
        and cached.get("component") == component.model_dump()
    ):
        # An admitted immutable release must not depend on mutable package
        # files remaining present or unchanged. Recheck integrity and current
        # operator trust without rereading/republishing its source on every mount.
        try:
            content = (root / "index.mjs").read_bytes()
        except OSError as exc:
            raise ResourceNotFoundError(message="UI artifact is unavailable") from exc
        _verify(cached, content)
        return f"/api/ui-artifacts/{slug}/{component.digest}/index.mjs"
    source = (bundle / component.entry).resolve()
    if not source.is_relative_to(bundle.resolve()):
        raise BadRequestError(message="UI artifact is outside its package")
    if not source.is_file():
        root = _root() / slug / component.digest
        try:
            metadata = json.loads((root / f"release-{component.key}.json").read_text())
            content = (root / "index.mjs").read_bytes()
        except (OSError, ValueError) as exc:
            raise ResourceNotFoundError(message="UI artifact is unavailable") from exc
        if (
            metadata.get("publisher") != publisher
            or metadata.get("package") != slug
            or metadata.get("component") != component.model_dump()
        ):
            raise BadRequestError(
                message="Cached UI release does not match the active declaration"
            )
        _verify(metadata, content)
        return f"/api/ui-artifacts/{slug}/{component.digest}/index.mjs"
    if source.stat().st_size > 2 * 1024 * 1024:
        raise BadRequestError(message="UI artifact exceeds 2 MiB")
    signature = source.with_name(f"{source.stem}.{component.key}.sig")
    metadata = {
        "publisher": publisher,
        "package": slug,
        "component": component.model_dump(),
        "signature": signature.read_text().strip() if signature.is_file() else "",
        "development": not signature.is_file(),
    }
    content = source.read_bytes()
    _verify(metadata, content)
    # Immutable cache: publish the verified bytes, not a path that can change
    # between verification and FileResponse streaming. Metadata is rechecked
    # against current operator trust on every uncached public delivery.
    root = _root() / slug / component.digest
    root.mkdir(parents=True, exist_ok=True)
    for name, data in (
        ("index.mjs", content),
        (f"release-{component.key}.json", json.dumps(metadata).encode()),
    ):
        fd, temporary = tempfile.mkstemp(dir=root)
        try:
            with os.fdopen(fd, "wb") as stream:
                stream.write(data)
            os.replace(temporary, root / name)
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)
    return f"/api/ui-artifacts/{slug}/{component.digest}/index.mjs"


async def _active_manifest(app_id: str, *, workspace_id: str | None = None) -> dict:
    from app.services.access_delegation import current_definition

    # Native execution never falls back to an authoring OperationalModel or
    # repairs a legacy pointer. The named active definition is the authority.
    app, definition = await current_definition(app_id)
    if workspace_id is not None and app["context"]["workspace_id"] != workspace_id:
        raise InsufficientPermissionsError(message="App/workspace mismatch")
    manifest = _ActiveManifest(definition["context"]["canonical_manifest"])
    manifest.definition_revision = str(
        app["context"].get("active_definition_revision") or ""
    )
    package = manifest.get("package") or {}
    if app["context"].get("installed_package_slug") != (
        package.get("slug") or package.get("name")
    ):
        raise BadRequestError(
            message="UI package identity does not match the installed App"
        )
    return manifest


async def _mount_authority(app: Any, user_id: str, workspace_id: str) -> dict:
    from app.services.app_extension_views import _ensure_app_access

    # Both independent proofs remain mandatory at each boundary. Await both
    # and preserve access-error precedence without serializing their I/O.
    access, manifest = await asyncio.gather(
        _ensure_app_access(app, user_id, workspace_id),
        _active_manifest(app.id, workspace_id=workspace_id),
        return_exceptions=True,
    )
    if isinstance(access, BaseException):
        raise access
    if isinstance(manifest, BaseException):
        raise manifest
    return manifest


async def resolve_component(
    *,
    user_id: str,
    workspace_id: str,
    app_id: str,
    key: str,
    placement: str,
    props: dict,
) -> dict:
    """Resolve a declared mount under current App and publisher authority."""
    from app.models.nodes import App
    from app.services.app_extension_views import _resolve_bundle_dir

    cache_key = (
        user_id,
        workspace_id,
        app_id,
        key,
        placement,
        hashlib.sha256(
            json.dumps(props, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest(),
    )
    candidate = _mount_candidates.get(cache_key)
    if candidate is not None:
        touched, package, component, bundle = candidate
        if time.monotonic() - touched > 300:
            _mount_candidates.pop(cache_key, None)
        else:
            slug = str(package.get("slug") or package.get("name") or "")
            enabled = f"{slug}:{key}" in settings.INTEGRAL_UI_ENABLED_COMPONENTS.split(
                ","
            )
            artifact = None
            if enabled:
                artifact = await asyncio.to_thread(
                    _admit, bundle, str(package.get("publisher") or ""), slug, component
                )
            current_app = await App.get(app_id)
            if current_app is None:
                raise ResourceNotFoundError(message="App not found")
            try:
                current = await _mount_authority(current_app, user_id, workspace_id)
            except BaseException:
                _mount_candidates.pop(cache_key, None)
                raise
            declared = next(
                (
                    item
                    for item in current.get("app", {}).get("ui_components", [])
                    if item["key"] == key
                ),
                None,
            )
            if declared is not None:
                declared = UIComponentDeclaration.model_validate(declared).model_dump()
            if declared == component and current.get("package") == package:
                # No earlier access result is reused. All candidate bytes were
                # verified before the current authority/active-release proof.
                _mount_candidates[cache_key] = (
                    time.monotonic(),
                    package,
                    component,
                    bundle,
                )
                _mount_candidates.move_to_end(cache_key)
                result = {
                    "enabled": enabled,
                    "component": component,
                    "definition_revision": getattr(
                        current,
                        "definition_revision",
                        str(getattr(current_app, "active_definition_revision", "")),
                    ),
                }
                if enabled:
                    result["artifact_url"] = artifact
                return result
            _mount_candidates.pop(cache_key, None)
            # A release changed. Perform the ordinary cold resolution once;
            # never recursively retry or return the previous release.

    app = await App.get(app_id)
    if app is None:
        raise ResourceNotFoundError(message="App not found")
    manifest = await _mount_authority(app, user_id, workspace_id)
    declaration = next(
        (
            item
            for item in manifest.get("app", {}).get("ui_components", [])
            if item["key"] == key
        ),
        None,
    )
    if declaration is None:
        raise ResourceNotFoundError(message="UI component is not declared")
    try:
        declaration = UIComponentDeclaration.model_validate(declaration).model_dump()
    except ValueError as exc:
        raise BadRequestError(message="UI component declaration is invalid") from exc
    if placement not in declaration["placements"]:
        raise BadRequestError(message="UI component placement is not supported")
    try:
        jsonschema.Draft202012Validator(declaration["props_schema"]).validate(props)
    except jsonschema.ValidationError as exc:
        raise BadRequestError(message="UI component props are invalid") from exc
    package = manifest.get("package") or {}
    slug, publisher = str(package.get("slug") or package.get("name") or ""), str(
        package.get("publisher") or ""
    )
    installed_slug = str(getattr(app, "installed_package_slug", "") or "")
    if not re.fullmatch(r"[a-z][a-z0-9-]*", slug) or slug != installed_slug:
        raise BadRequestError(
            message="UI package identity does not match the installed App"
        )
    enabled = f"{slug}:{key}" in settings.INTEGRAL_UI_ENABLED_COMPONENTS.split(",")
    result: dict[str, Any] = {"enabled": enabled, "component": declaration}
    if enabled:
        bundle = await _resolve_bundle_dir(app)
        if bundle is None:
            raise ResourceNotFoundError(message="UI package assets unavailable")
        result["artifact_url"] = await asyncio.to_thread(
            _admit, bundle, publisher, slug, declaration
        )
    # Recheck after filesystem work: an earlier admission is not current access.
    current_app = await App.get(app_id)
    if current_app is None:
        raise ResourceNotFoundError(message="App not found")
    current = await _mount_authority(current_app, user_id, workspace_id)
    current_declaration = next(
        (
            item
            for item in current.get("app", {}).get("ui_components", [])
            if item["key"] == key
        ),
        None,
    )
    if current_declaration is not None:
        current_declaration = UIComponentDeclaration.model_validate(
            current_declaration
        ).model_dump()
    if str(getattr(current_app, "installed_package_slug", "") or "") != installed_slug:
        raise BadRequestError(message="App package changed; reopen the component")
    if current_declaration != declaration or current.get("package") != manifest.get(
        "package"
    ):
        raise BadRequestError(message="App definition changed; reopen the component")
    result["definition_revision"] = getattr(
        current,
        "definition_revision",
        str(getattr(current_app, "active_definition_revision", "")),
    )
    if enabled:
        _mount_candidates[cache_key] = (
            time.monotonic(),
            deepcopy(package),
            deepcopy(declaration),
            bundle,
        )
        _mount_candidates.move_to_end(cache_key)
        while len(_mount_candidates) > 256:
            _mount_candidates.popitem(last=False)
    return result


def read_artifact(slug: str, digest: str) -> bytes:
    """Read admitted public code after current trust and integrity checks."""
    if not re.fullmatch(r"[a-z][a-z0-9-]*", slug) or not re.fullmatch(
        r"[0-9a-f]{64}", digest
    ):
        raise ResourceNotFoundError(message="UI artifact not found")
    root = _root() / slug / digest
    try:
        content = (root / "index.mjs").read_bytes()
    except OSError as exc:
        raise ResourceNotFoundError(message="UI artifact not found") from exc
    # One module may export several declared components. Each declaration keeps
    # its own signature; disabling one must not corrupt another's admission.
    for enabled in settings.INTEGRAL_UI_ENABLED_COMPONENTS.split(","):
        package, _, key = enabled.partition(":")
        if package != slug or not re.fullmatch(r"[a-z][a-z0-9_-]{0,79}", key):
            continue
        try:
            metadata = json.loads((root / f"release-{key}.json").read_text())
        except (OSError, ValueError):
            continue
        if (
            metadata.get("package") != slug
            or metadata.get("component", {}).get("digest") != digest
        ):
            raise BadRequestError(message="UI artifact identity mismatch")
        _verify(metadata, content)
        return content
    raise InsufficientPermissionsError(
        message="UI component is disabled or not admitted"
    )
