"""App extension view host — manifest resolution, assets, handshake (ADR-011)."""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import mimetypes
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from jvspatial.api.exceptions import InsufficientPermissionsError, ResourceNotFoundError

from app.config import settings
from app.exceptions import BadRequestError
from app.models.nodes import App, OperationalModel
from app.services.app_graph import get_app_attached_operational_model
from app.services.operational_model_runtime import compile_canonical_manifest
from app.services.permissions import resolve_role
from app.services.workspace_permissions import can_access_workspace

_HANDSHAKE_TTL_SECONDS = 300
_PROTOCOL = "integral.extension.v1"
_ALLOWED_STATIC_SUFFIXES = frozenset(
    {
        ".html",
        ".htm",
        ".js",
        ".mjs",
        ".css",
        ".json",
        ".map",
        ".svg",
        ".png",
        ".jpg",
        ".jpeg",
        ".gif",
        ".webp",
        ".woff",
        ".woff2",
        ".ttf",
        ".ico",
    }
)


def _b64url_encode(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def _b64url_decode(raw: str) -> bytes:
    pad = "=" * (-len(raw) % 4)
    return base64.urlsafe_b64decode(raw + pad)


def _sign_payload(payload: Dict[str, Any]) -> str:
    body = _b64url_encode(
        json.dumps(payload, separators=(",", ":"), sort_keys=True).encode("utf-8")
    )
    sig = hmac.new(
        settings.SECRET_KEY.encode("utf-8"),
        body.encode("ascii"),
        hashlib.sha256,
    ).hexdigest()
    return f"{body}.{sig}"


def verify_handshake_token(token: str) -> Dict[str, Any]:
    """Validate handshake token; raises BadRequestError on failure."""
    if not token or "." not in token:
        raise BadRequestError(message="invalid handshake token")
    body, sig = token.rsplit(".", 1)
    expected = hmac.new(
        settings.SECRET_KEY.encode("utf-8"),
        body.encode("ascii"),
        hashlib.sha256,
    ).hexdigest()
    if not hmac.compare_digest(expected, sig):
        raise BadRequestError(message="invalid handshake token")
    try:
        payload = json.loads(_b64url_decode(body))
    except (json.JSONDecodeError, ValueError):
        raise BadRequestError(message="invalid handshake token")
    exp = int(payload.get("exp") or 0)
    if exp < int(time.time()):
        raise BadRequestError(message="handshake token expired")
    return payload


def mint_handshake_token(
    *,
    workspace_id: str,
    app_id: str,
    package_version: str,
    view_key: str,
    mount_id: str,
) -> str:
    """Sign a short-lived handshake bound to workspace, app, and mount."""
    payload = {
        "protocol": _PROTOCOL,
        "workspace_id": workspace_id,
        "app_id": app_id,
        "package_version": package_version,
        "view_key": view_key,
        "mount_id": mount_id,
        "exp": int(time.time()) + _HANDSHAKE_TTL_SECONDS,
    }
    return _sign_payload(payload)


async def _ensure_app_access(app: App, user_id: str, workspace_id: str) -> None:
    if getattr(app, "workspace_id", None) != workspace_id:
        raise InsufficientPermissionsError(message="App/workspace mismatch")
    ws_role = await can_access_workspace(user_id, workspace_id)
    if ws_role == "none":
        raise InsufficientPermissionsError(message="Access denied")
    state = str(getattr(app, "lifecycle_state", "active") or "active")
    if state not in ("active", "awaiting_settings"):
        raise BadRequestError(
            message=f"App is not active (state={state})",
            details={"error_code": "app_not_active"},
        )
    role = await resolve_role(user_id, "app", app.id)
    if role is None:
        raise InsufficientPermissionsError(message="Access denied")


async def _resolve_bundle_dir(app: App) -> Optional[Path]:
    app_md = getattr(app, "metadata", None) or {}
    bdp = str(app_md.get("bundle_dir_path") or "").strip()
    if bdp:
        return Path(bdp).resolve()

    cp: Optional[OperationalModel] = None
    lib_id = getattr(app, "installed_from_library_id", None)
    if lib_id:
        cp = await OperationalModel.get(lib_id)
    if cp is None:
        cp = await get_app_attached_operational_model(app)
    if cp is not None:
        cp_md = getattr(cp, "metadata", None) or {}
        bdp = str(cp_md.get("bundle_dir_path") or "").strip()
        if bdp:
            return Path(bdp).resolve()

    slug = str(getattr(app, "installed_package_slug", None) or "").strip()
    if slug:
        from app.services.operational_model_loader import (
            load_library_operational_models_with_issues,
        )

        specs, _ = load_library_operational_models_with_issues(
            core_only=False, verify_signatures=False
        )
        for spec in specs:
            if spec.slug == slug and getattr(spec, "bundle_dir", None):
                return Path(str(spec.bundle_dir)).resolve()
    return None


async def _compiled_app_manifest(app: App) -> Dict[str, Any]:
    # The active ApplicationDefinition is the execution authority for an
    # installed App. Reading the attached OperationalModel first would allow an
    # unactivated authoring edit to alter a live extension surface before its
    # contract revision was reviewed and materialized.
    from app.services.application_definitions import get_active_application_definition

    definition = await get_active_application_definition(app)
    if definition is not None and getattr(definition, "canonical_manifest", None):
        return dict(definition.canonical_manifest)
    cp = await get_app_attached_operational_model(app)
    if cp is None or not getattr(cp, "manifest", None):
        app_md = getattr(app, "metadata", None) or {}
        raw = app_md.get("source_manifest")
        if isinstance(raw, dict) and raw:
            return compile_canonical_manifest(manifest=raw)
        return {}
    return compile_canonical_manifest(manifest=cp.manifest or {})


def _extension_views_from_canonical(canonical: Dict[str, Any]) -> List[Dict[str, Any]]:
    app_node = canonical.get("app") or {}
    raw = app_node.get("extension_views") or []
    return list(raw) if isinstance(raw, list) else []


def _view_descriptor(spec: Dict[str, Any]) -> Dict[str, Any]:
    key = str(spec.get("key") or "").strip()
    entry = str(spec.get("entry") or f"views/{key}/index.html").strip()
    return {
        "key": key,
        "name": str(spec.get("name") or key),
        "description": str(spec.get("description") or ""),
        "entry": entry,
        "scope": str(spec.get("scope") or "track"),
    }


async def list_extension_views(
    *,
    user_id: str,
    workspace_id: str,
    app_id: str,
    mount_id: str,
    view_key: Optional[str] = None,
) -> Dict[str, Any]:
    """List extension view descriptors and mint a bridge handshake."""
    if not workspace_id:
        raise BadRequestError(message="no active workspace")
    app = await App.get(app_id)
    if app is None:
        raise ResourceNotFoundError(message="App not found")
    await _ensure_app_access(app, user_id, workspace_id)

    canonical = await _compiled_app_manifest(app)
    views = [_view_descriptor(v) for v in _extension_views_from_canonical(canonical)]
    if view_key:
        views = [v for v in views if v["key"] == view_key]
        if not views:
            raise ResourceNotFoundError(
                message=f"extension view {view_key!r} not found"
            )

    package_version = (
        str(getattr(app, "installed_package_version", None) or "")
        or str(getattr(app, "version", None) or "")
        or str((canonical.get("package") or {}).get("version") or "")
    )
    token_view = view_key or (views[0]["key"] if views else "")
    handshake = mint_handshake_token(
        workspace_id=workspace_id,
        app_id=app_id,
        package_version=package_version,
        view_key=token_view,
        mount_id=mount_id,
    )
    return {
        "app_id": app_id,
        "package_version": package_version or None,
        "views": views,
        "handshake_token": handshake,
        "protocol": _PROTOCOL,
    }


def _resolve_view_asset(
    bundle_dir: Path,
    view_spec: Dict[str, Any],
    asset_path: str,
) -> Tuple[Path, str]:
    entry = str(view_spec.get("entry") or "").strip()
    view_root = (bundle_dir / entry).resolve().parent
    if not str(view_root).startswith(str(bundle_dir.resolve())):
        raise BadRequestError(message="invalid view asset path")

    rel = (asset_path or "").strip().lstrip("/")
    if not rel:
        rel = Path(entry).name if entry else "index.html"
    candidate = (view_root / rel).resolve()
    if not str(candidate).startswith(str(view_root)):
        raise BadRequestError(message="invalid asset path")
    if not candidate.is_file():
        raise ResourceNotFoundError(message="asset not found")
    suffix = candidate.suffix.lower()
    if suffix and suffix not in _ALLOWED_STATIC_SUFFIXES:
        raise BadRequestError(message="asset type not allowed")
    media_type = mimetypes.guess_type(str(candidate))[0] or "application/octet-stream"
    return candidate, media_type


async def serve_extension_view_asset(
    *,
    user_id: str,
    workspace_id: str,
    app_id: str,
    view_key: str,
    asset_path: str,
) -> Tuple[Path, str]:
    """Resolve and return a package view asset after access checks."""
    if not workspace_id:
        raise BadRequestError(message="no active workspace")
    app = await App.get(app_id)
    if app is None:
        raise ResourceNotFoundError(message="App not found")
    await _ensure_app_access(app, user_id, workspace_id)

    canonical = await _compiled_app_manifest(app)
    views = _extension_views_from_canonical(canonical)
    spec = next((v for v in views if str(v.get("key") or "") == view_key), None)
    if spec is None:
        raise ResourceNotFoundError(message=f"extension view {view_key!r} not found")

    bundle_dir = await _resolve_bundle_dir(app)
    if bundle_dir is None or not bundle_dir.is_dir():
        raise ResourceNotFoundError(message="app package assets unavailable")

    return _resolve_view_asset(bundle_dir, spec, asset_path)


def theme_tokens_for_host() -> Dict[str, Any]:
    """Minimal theme snapshot for iframe bridge (no secrets)."""
    return {
        "colorScheme": "system",
        "fontFamily": "system-ui, sans-serif",
    }
