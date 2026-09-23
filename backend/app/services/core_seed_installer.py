"""Generic core-seed package installer (F0 / I-EXT-02).

Substrate-owned defaults (``agent-scratch``) MUST be
declared as ``core_package`` manifests and installed through this module —
not via hard-coded slug branches scattered across services.

Callers still own product-specific timing (signup vs first agent connect)
and caches; this module resolves the library row and invokes ``install_app``.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, Optional

from app.models.nodes import OperationalModel
from app.services.app_lifecycle import install_app
from app.services.package_paths import CORE_SEED_SLUGS, resolve_package_class

logger = logging.getLogger(__name__)


async def resolve_core_package_library(
    *,
    slug: str,
    fallback_name: Optional[str] = None,
) -> Optional[OperationalModel]:
    """Find the seeded library OperationalModel for a core_package slug."""
    if slug not in CORE_SEED_SLUGS:
        logger.warning("resolve_core_package_library called for non-core slug %s", slug)
    try:
        by_slug = await OperationalModel.find(
            {
                "context.library_package": True,
                "context.metadata.slug": slug,
            }
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("core package library lookup failed for %s: %s", slug, exc)
        return None

    rows = _as_list(by_slug)
    if rows:
        return rows[0]

    if fallback_name:
        try:
            by_name = await OperationalModel.find(
                {
                    "context.library_package": True,
                    "context.name": fallback_name,
                }
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "core package name fallback failed for %s: %s", fallback_name, exc
            )
            return None
        rows = _as_list(by_name)
        if rows:
            return rows[0]
    return None


async def install_core_package(
    *,
    slug: str,
    workspace_id: str,
    actor_id: str,
    fallback_name: Optional[str] = None,
    settings: Optional[Dict[str, Any]] = None,
) -> Optional[str]:
    """Install a core_package into ``workspace_id``. Returns app_id or None.

    Never raises into callers — signup / agent connect must not fail because
    a seed App did not install.
    """
    try:
        library_cp = await resolve_core_package_library(
            slug=slug, fallback_name=fallback_name
        )
        if library_cp is None:
            logger.info("core package %s not in library yet — deferring install", slug)
            return None

        md = dict(getattr(library_cp, "metadata", None) or {})
        declared = str(md.get("package_class") or "")
        pkg_class = resolve_package_class(slug=slug, declared=declared or None)
        if pkg_class != "core_package" and slug not in CORE_SEED_SLUGS:
            logger.error(
                "refusing to install %s via core installer (class=%s)",
                slug,
                pkg_class,
            )
            return None

        result = await install_app(
            library_cp_id=library_cp.id,
            workspace_id=workspace_id,
            actor_id=actor_id,
            settings=settings if settings is not None else {},
        )
        app_id = (
            result.get("app_id")
            if isinstance(result, dict)
            else getattr(result, "id", None)
        )
        return str(app_id) if app_id else None
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "install_core_package(%s) failed for workspace %s: %s",
            slug,
            workspace_id,
            exc,
            exc_info=True,
        )
        return None


def _as_list(raw: Any) -> list:
    if raw is None:
        return []
    if isinstance(raw, list):
        return raw
    return [raw]
