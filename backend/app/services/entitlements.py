"""F3 Phase One — workspace Entitlement projection (no Stripe yet).

Gates commercial App install; revoke → pause_app so hooks/tools stop while
Core generic reads of App data remain available.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any, Dict, List, Optional, Tuple

from jvspatial.db.database import Database

from app.exceptions import BadRequestError
from app.models.entitlement import Entitlement
from app.models.nodes import App
from app.services.package_paths import resolve_package_class
from app.utils.time import utc_now_iso

logger = logging.getLogger(__name__)

_ACTIVE = "active"
_REVOKED = "revoked"
_EXPIRED = "expired"

_ENTITLEMENT_CAS_LOCKS: Dict[Tuple[int, int, str], asyncio.Lock] = {}


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


def _source_is_manual(source: Optional[str]) -> bool:
    return (source or "").strip().lower() in {"", "manual"}


async def _entitlement_cas_context(
    row: Entitlement,
) -> Tuple[Any, bool, Optional[asyncio.Lock], Any, str]:
    """Return (database, native_atomic, optional_lock, graph_context, collection)."""
    graph_context = await row.get_context()
    database = graph_context.database
    concrete = database
    seen = set()
    while getattr(concrete, "inner", None) is not None and id(concrete) not in seen:
        seen.add(id(concrete))
        concrete = concrete.inner
    native_atomic = (
        type(concrete).find_one_and_update is not Database.find_one_and_update
    )
    lock = None
    if not native_atomic:
        if type(concrete).__module__ not in {"jvspatial.db.jsondb", "jvspatial.memory"}:
            raise RuntimeError(
                "entitlement conditional update unavailable on this database"
            )
        lock = _ENTITLEMENT_CAS_LOCKS.setdefault(
            (id(concrete), id(asyncio.get_running_loop()), str(row.id)),
            asyncio.Lock(),
        )
    collection = graph_context._get_collection_name("o")
    return database, native_atomic, lock, graph_context, collection


async def _cas_update_entitlement_if_source(
    row: Entitlement,
    *,
    expected_source: str,
    set_fields: Dict[str, Any],
) -> Optional[Entitlement]:
    """Update ``row`` only while ``context.source`` still equals ``expected_source``.

    Native stores use ``find_one_and_update``. Single-process stores serialize
    with a per-row lock and re-check source on the Object before ``save``.
    """
    database, native_atomic, lock, _ctx, collection = await _entitlement_cas_context(
        row
    )

    async def _claim_native() -> Optional[Entitlement]:
        query = {"id": row.id, "context.source": expected_source}
        update = {
            "$set": {f"context.{key}": value for key, value in set_fields.items()}
        }
        claimed = await database.find_one_and_update(collection, query, update)
        if claimed is None:
            return None
        refreshed = await Entitlement.get(row.id)
        if refreshed is None:
            return None
        for key, value in set_fields.items():
            setattr(refreshed, key, value)
        return refreshed

    async def _claim_locked() -> Optional[Entitlement]:
        fresh = await Entitlement.get(row.id)
        if fresh is None:
            return None
        if (fresh.source or "") != (expected_source or ""):
            return None
        for key, value in set_fields.items():
            setattr(fresh, key, value)
        await fresh.save()
        return fresh

    if native_atomic:
        return await _claim_native()
    assert lock is not None
    async with lock:
        return await _claim_locked()


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

    from app.api.errors import EntitlementRequiredError

    slug = str(package_meta.get("slug") or "").strip()
    key = entitlement_key_for_package(slug=slug, package_meta=package_meta)
    if not key:
        raise BadRequestError(
            message="commercial_app requires package.slug or package.entitlement_key",
            details={"package": package_meta},
        )
    row = await find_entitlement(workspace_id=workspace_id, entitlement_key=key)
    if row is None or not entitlement_is_active(row):
        raise EntitlementRequiredError(
            message=f"entitlement required for package {slug!r} (key={key!r})",
            details={
                "workspace_id": workspace_id,
                "entitlement_key": key,
                "package_slug": slug,
                "status": getattr(row, "status", None) if row else None,
            },
        )
    return row


async def _pause_apps_for_entitlement(
    *,
    workspace_id: str,
    entitlement_key: str,
    package_slug: str,
    on_loss: str,
    actor_id: str,
) -> List[str]:
    """Pause installed Apps matching the entitlement package slug."""
    paused: List[str] = []
    if (on_loss or "pause") != "pause":
        return paused
    slug = (package_slug or entitlement_key).strip()
    apps = await App.find(
        {
            "context.workspace_id": workspace_id,
            "context.source_operational_model_slug": slug,
        }
    )
    if not apps:
        apps = await App.find(
            {
                "context.workspace_id": workspace_id,
                "context.installed_package_slug": slug,
            }
        )
    from app.services.app_lifecycle import pause_app

    for app in apps:
        if getattr(app, "lifecycle_state", None) != "active":
            continue
        try:
            await pause_app(app_id=app.id, actor_id=actor_id or "system:entitlement")
            paused.append(app.id)
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "entitlement revoke: pause_app failed app=%s: %s",
                app.id,
                exc,
            )
    return paused


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
    source: str = "manual",
    respect_manual: bool = False,
) -> Entitlement:
    """Create or reactivate an entitlement for a workspace.

    ``source`` is ``manual`` for operator grants or a provider id for a
    billing projection. When ``respect_manual`` is set, an existing manual
    row is returned unchanged so provider reconcile cannot clobber it.
    """
    ws = (workspace_id or "").strip()
    key = (entitlement_key or "").strip()
    slug = (package_slug or key).strip()
    origin = (source or "manual").strip() or "manual"
    if not ws or not key:
        raise BadRequestError(message="workspace_id and entitlement_key are required")

    now = utc_now_iso()
    existing = await find_entitlement(workspace_id=ws, entitlement_key=key)
    if existing is None:
        return await Entitlement.create(
            workspace_id=ws,
            entitlement_key=key,
            package_slug=slug,
            status=_ACTIVE,
            source=origin,
            on_loss=on_loss or "pause",
            data_access=data_access or "core_generic_read",
            retention=retention or "retain_until_uninstall",
            expires_at=expires_at,
            created_at=now,
            updated_at=now,
            created_by=actor_id or "",
        )

    if respect_manual and _source_is_manual(existing.source):
        return existing

    if respect_manual:
        # Source-filtered CAS: only overwrite while the row remains non-manual.
        expected_source = existing.source or ""
        claimed = await _cas_update_entitlement_if_source(
            existing,
            expected_source=expected_source,
            set_fields={
                "status": _ACTIVE,
                "package_slug": slug,
                "source": origin,
                "on_loss": on_loss or "pause",
                "data_access": data_access or "core_generic_read",
                "retention": retention or "retain_until_uninstall",
                "expires_at": expires_at,
                "revoked_at": None,
                "updated_at": now,
            },
        )
        if claimed is not None:
            return claimed
        fresh = await find_entitlement(workspace_id=ws, entitlement_key=key)
        if fresh is None:
            return await Entitlement.create(
                workspace_id=ws,
                entitlement_key=key,
                package_slug=slug,
                status=_ACTIVE,
                source=origin,
                on_loss=on_loss or "pause",
                data_access=data_access or "core_generic_read",
                retention=retention or "retain_until_uninstall",
                expires_at=expires_at,
                created_at=now,
                updated_at=now,
                created_by=actor_id or "",
            )
        if _source_is_manual(fresh.source):
            return fresh
        # Concurrent provider update — apply once more against the observed source.
        claimed2 = await _cas_update_entitlement_if_source(
            fresh,
            expected_source=fresh.source or "",
            set_fields={
                "status": _ACTIVE,
                "package_slug": slug,
                "source": origin,
                "on_loss": on_loss or "pause",
                "data_access": data_access or "core_generic_read",
                "retention": retention or "retain_until_uninstall",
                "expires_at": expires_at,
                "revoked_at": None,
                "updated_at": now,
            },
        )
        if claimed2 is not None:
            return claimed2
        final = await find_entitlement(workspace_id=ws, entitlement_key=key)
        return final if final is not None else fresh

    existing.status = _ACTIVE
    existing.package_slug = slug
    existing.source = origin
    existing.on_loss = on_loss or "pause"
    existing.data_access = data_access or "core_generic_read"
    existing.retention = retention or "retain_until_uninstall"
    existing.expires_at = expires_at
    existing.revoked_at = None
    existing.updated_at = now
    await existing.save()
    return existing


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
    if pause_installed:
        paused = await _pause_apps_for_entitlement(
            workspace_id=ws,
            entitlement_key=key,
            package_slug=row.package_slug or key,
            on_loss=row.on_loss or "pause",
            actor_id=actor_id,
        )

    return {
        "entitlement_key": key,
        "workspace_id": ws,
        "status": _REVOKED,
        "paused_app_ids": paused,
        "data_access": row.data_access,
        "retention": row.retention,
    }


async def revoke_provider_entitlement(
    *,
    workspace_id: str,
    entitlement_key: str,
    actor_id: str = "system:billing",
) -> Dict[str, Any]:
    """Revoke a provider-sourced entitlement. Manual rows are left unchanged.

    Source validation and mutation use a conditional update so a concurrent
    manual grant between reads cannot be revoked by provider reconcile.
    """
    ws = (workspace_id or "").strip()
    key = (entitlement_key or "").strip()
    row = await find_entitlement(workspace_id=ws, entitlement_key=key)
    if row is None:
        return {
            "entitlement_key": key,
            "workspace_id": ws,
            "skipped": True,
            "reason": "missing",
        }
    if _source_is_manual(row.source):
        return {
            "entitlement_key": key,
            "workspace_id": ws,
            "skipped": True,
            "reason": "manual",
        }

    now = utc_now_iso()
    expected_source = row.source or ""
    claimed = await _cas_update_entitlement_if_source(
        row,
        expected_source=expected_source,
        set_fields={
            "status": _REVOKED,
            "revoked_at": now,
            "updated_at": now,
        },
    )
    if claimed is None:
        fresh = await find_entitlement(workspace_id=ws, entitlement_key=key)
        if fresh is None:
            return {
                "entitlement_key": key,
                "workspace_id": ws,
                "skipped": True,
                "reason": "missing",
            }
        if _source_is_manual(fresh.source):
            return {
                "entitlement_key": key,
                "workspace_id": ws,
                "skipped": True,
                "reason": "manual",
            }
        # Source changed to a different provider id — do not revoke blindly.
        return {
            "entitlement_key": key,
            "workspace_id": ws,
            "skipped": True,
            "reason": "manual",
        }

    paused = await _pause_apps_for_entitlement(
        workspace_id=ws,
        entitlement_key=key,
        package_slug=claimed.package_slug or key,
        on_loss=claimed.on_loss or "pause",
        actor_id=actor_id or "system:billing",
    )
    return {
        "entitlement_key": key,
        "workspace_id": ws,
        "status": _REVOKED,
        "paused_app_ids": paused,
        "data_access": claimed.data_access,
        "retention": claimed.retention,
        "skipped": False,
    }


async def list_entitlements(*, workspace_id: str) -> List[Entitlement]:
    """List all entitlement rows for a workspace."""
    return list(
        await Entitlement.find({"context.workspace_id": (workspace_id or "").strip()})
    )
