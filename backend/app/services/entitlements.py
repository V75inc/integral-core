"""F3 Phase One — workspace Entitlement projection (no Stripe yet).

Gates commercial App install; revoke → pause_app so hooks/tools stop while
Core generic reads of App data remain available.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

from app.api.errors import BadRequestError, InsufficientPermissionsError
from app.models.entitlement import Entitlement
from app.models.nodes import App
from app.services.package_paths import resolve_package_class
from app.utils.time import utc_now_iso

logger = logging.getLogger(__name__)

_ACTIVE = "active"
_REVOKED = "revoked"
_EXPIRED = "expired"


def entitlement_key_for_package(
    *,
    slug: str,
    package_meta: Optional[Dict[str, Any]] = None,
) -> str:
    """Resolve entitlement key: package.entitlement_key or package slug."""
    meta = package_meta or {}
    raw = str(meta.get("entitlement_key") or "").strip()
    return raw or str(slug or "").strip()


def package_requires_entitlement(package_meta: Optional[Dict[str, Any]]) -> bool:
    """True when package.class is commercial_app."""
    meta = package_meta or {}
    slug = str(meta.get("slug") or "").strip()
    declared = str(meta.get("class") or "").strip() or None
    return resolve_package_class(slug=slug, declared=declared) == "commercial_app"


async def find_entitlement(
    *,
    workspace_id: str,
    entitlement_key: str,
) -> Optional[Entitlement]:
    """Return the workspace entitlement row for ``entitlement_key``, if any."""
    rows = await Entitlement.find(
        {
            "context.workspace_id": workspace_id,
            "context.entitlement_key": entitlement_key,
        }
    )
    return rows[0] if rows else None


def entitlement_is_active(row: Entitlement, *, now_iso: Optional[str] = None) -> bool:
    """True when status is active and expires_at (if set) is still in the future."""
    if (row.status or "").strip().lower() != _ACTIVE:
        return False
    exp = (row.expires_at or "").strip()
    if not exp:
        return True
    now = now_iso or utc_now_iso()
    return exp >= now


# Back-compat alias used by early call sites.
_is_active = entitlement_is_active


async def require_active_entitlement(
    *,
    workspace_id: str,
    package_meta: Dict[str, Any],
) -> Optional[Entitlement]:
    """For commercial packages, require an active entitlement or raise 403.

    Non-commercial packages return ``None`` (no entitlement needed).
    """
    if not package_requires_entitlement(package_meta):
        return None

    slug = str(package_meta.get("slug") or "").strip()
    key = entitlement_key_for_package(slug=slug, package_meta=package_meta)
    if not key:
        raise BadRequestError(
            message="commercial_app requires package.slug or package.entitlement_key",
            details={"package": package_meta},
        )
    row = await find_entitlement(workspace_id=workspace_id, entitlement_key=key)
    if row is None or not entitlement_is_active(row):
        raise InsufficientPermissionsError(
            message=(
                f"entitlement required for commercial package {slug!r} "
                f"(key={key!r})"
            ),
            details={
                "workspace_id": workspace_id,
                "entitlement_key": key,
                "package_slug": slug,
                "status": getattr(row, "status", None) if row else None,
            },
        )
    return row


async def grant_entitlement(
    *,
    workspace_id: str,
    entitlement_key: str,
    package_slug: str,
    actor_id: str,
    expires_at: Optional[str] = None,
    on_loss: str = "pause",
    data_access: str = "core_generic_read",
    retention: str = "retain_until_uninstall",
) -> Entitlement:
    """Create or reactivate a manual entitlement for a workspace."""
    ws = (workspace_id or "").strip()
    key = (entitlement_key or "").strip()
    slug = (package_slug or key).strip()
    if not ws or not key:
        raise BadRequestError(message="workspace_id and entitlement_key are required")

    now = utc_now_iso()
    existing = await find_entitlement(workspace_id=ws, entitlement_key=key)
    if existing is not None:
        existing.status = _ACTIVE
        existing.package_slug = slug
        existing.source = "manual"
        existing.on_loss = on_loss or "pause"
        existing.data_access = data_access or "core_generic_read"
        existing.retention = retention or "retain_until_uninstall"
        existing.expires_at = expires_at
        existing.revoked_at = None
        existing.updated_at = now
        await existing.save()
        return existing

    return await Entitlement.create(
        workspace_id=ws,
        entitlement_key=key,
        package_slug=slug,
        status=_ACTIVE,
        source="manual",
        on_loss=on_loss or "pause",
        data_access=data_access or "core_generic_read",
        retention=retention or "retain_until_uninstall",
        expires_at=expires_at,
        created_at=now,
        updated_at=now,
        created_by=actor_id or "",
    )


async def revoke_entitlement(
    *,
    workspace_id: str,
    entitlement_key: str,
    actor_id: str,
    pause_installed: bool = True,
) -> Dict[str, Any]:
    """Mark entitlement revoked and pause matching installed Apps (Phase One)."""
    ws = (workspace_id or "").strip()
    key = (entitlement_key or "").strip()
    row = await find_entitlement(workspace_id=ws, entitlement_key=key)
    if row is None:
        raise BadRequestError(
            message=f"Entitlement {key!r} not found for workspace {ws!r}",
            details={"workspace_id": ws, "entitlement_key": key},
        )

    now = utc_now_iso()
    row.status = _REVOKED
    row.revoked_at = now
    row.updated_at = now
    await row.save()

    paused: List[str] = []
    if pause_installed and (row.on_loss or "pause") == "pause":
        slug = (row.package_slug or key).strip()
        apps = await App.find(
            {
                "context.workspace_id": ws,
                "context.source_profile_slug": slug,
            }
        )
        if not apps:
            apps = await App.find(
                {
                    "context.workspace_id": ws,
                    "context.installed_package_slug": slug,
                }
            )
        from app.services.app_lifecycle import pause_app

        for app in apps:
            if getattr(app, "lifecycle_state", None) != "active":
                continue
            try:
                await pause_app(
                    app_id=app.id, actor_id=actor_id or "system:entitlement"
                )
                paused.append(app.id)
            except Exception as exc:  # noqa: BLE001
                logger.warning(
                    "entitlement revoke: pause_app failed app=%s: %s",
                    app.id,
                    exc,
                )

    return {
        "entitlement_key": key,
        "workspace_id": ws,
        "status": _REVOKED,
        "paused_app_ids": paused,
        "data_access": row.data_access,
        "retention": row.retention,
    }


async def list_entitlements(*, workspace_id: str) -> List[Entitlement]:
    """List all entitlement rows for a workspace."""
    return list(
        await Entitlement.find({"context.workspace_id": (workspace_id or "").strip()})
    )
