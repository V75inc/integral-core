"""Admin endpoints for content-profile bundle hot-load + introspection.

Phase C1 (spec §4.6, §9.3, §8.4 I-HOTLOAD-01).

Three admin-only endpoints under ``/api/admin/profiles``:

- ``POST /admin/profiles/rescan`` — re-walk ``backend/app/profiles/``,
  compute the new index, diff against the in-memory ``_LAST_INDEX``, upsert
  added/updated rows via ``upsert_seeded_library_packages``, and return the
  delta. Serialized via ``_RESCAN_LOCK`` per I-HOTLOAD-01 so two concurrent
  admin clicks don't double-upsert.
- ``GET /admin/profiles`` — list every loaded bundle with metadata
  (signature/fingerprint/scope/skill-keys).
- ``GET /admin/profiles/{slug}/installed`` — impact preview: which
  workspaces currently apply this bundle.

Admin gate reads ``"admin"`` from ``request.state.user.roles`` — the jvspatial
JWT-derived shape (``jvspatial.api.auth.models.UserResponse``). The Integral
``User`` Node has no ``is_admin`` field; admin is a jvspatial AuthUser role.
Mirrors the existing platform-admin check in ``app/api/auth.py``.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone
from typing import Any, Dict, List

from jvspatial.api import endpoint
from starlette.requests import Request

from app.api.utils import require_platform_admin, resolve_principal_id
from app.services.change_event import emit_change_event

logger = logging.getLogger(__name__)

# Module-level mutable state. Single-process boot — safe.
# ``_RESCAN_LOCK`` serializes concurrent rescans per I-HOTLOAD-01.
_RESCAN_LOCK = asyncio.Lock()
# slug -> {"bundle_fingerprint": str, "version": str}
_LAST_INDEX: Dict[str, Dict[str, Any]] = {}
_LAST_ISSUES: List[Dict[str, Any]] = []


@endpoint("/admin/profiles/rescan", methods=["POST"], auth=True, tags=["Admin"])
async def rescan_profiles(request: Request) -> Dict[str, Any]:
    """Re-walk the profiles tree and upsert changed bundles.

    Diff strategy:
      - added: slug present now, absent in ``_LAST_INDEX``.
      - updated: slug present in both, but ``bundle_fingerprint`` differs.
      - removed: slug absent now, present in ``_LAST_INDEX``.

    Upserts ContentProfile rows for added + updated via the existing
    ``upsert_seeded_library_packages`` helper — Phase B3 already routes
    fingerprint drift through the same path used at boot, so updated
    bundles get a fresh manifest + ``bundle_fingerprint`` metadata write.

    Returns:
        ``{"added": [...], "updated": [...], "removed": [...],
           "issues": [...], "reconciled_removed": [...]}``
    """
    require_platform_admin(request)
    async with _RESCAN_LOCK:
        # Lazy imports keep the module import path lightweight and avoid
        # circular-import risk at startup (this module is imported by
        # app.api package __init__).
        from app.models.nodes import CONTENT_PROFILES_REGISTRY_ID, ContentProfiles
        from app.services.app_graph import ensure_catalog_edge
        from app.services.content_profile_library_sync import (
            sync_library_catalog_from_disk,
        )

        cps_registry = await ContentProfiles.get(CONTENT_PROFILES_REGISTRY_ID)
        if cps_registry is None:
            logger.warning(
                "rescan: ContentProfiles registry %s not found — skipping upsert",
                CONTENT_PROFILES_REGISTRY_ID,
            )
            return {
                "added": [],
                "updated": [],
                "removed": [],
                "issues": [],
                "reconciled_removed": [],
            }

        report = await sync_library_catalog_from_disk(
            catalog_registry=cps_registry,
            ensure_catalog_edge=ensure_catalog_edge,
            previous_index=dict(_LAST_INDEX),
            now_iso=datetime.now(timezone.utc).isoformat(),
        )
        added = list(report.get("added") or [])
        updated = list(report.get("updated") or [])
        removed = list(report.get("removed") or [])
        reconciled_removed = list(report.get("reconciled_removed") or [])

        # Replace the index in-place after a successful pass. Caller now has
        # an authoritative snapshot; next rescan diffs against this.
        _LAST_INDEX.clear()
        _LAST_INDEX.update(dict(report.get("index") or {}))
        _LAST_ISSUES.clear()
        _LAST_ISSUES.extend(
            [
                {
                    "slug": getattr(issue, "slug", ""),
                    "code": getattr(issue, "code", ""),
                    "message": getattr(issue, "message", ""),
                    "level": getattr(issue, "level", "error"),
                    "profile_path": getattr(issue, "profile_path", ""),
                }
                for issue in list(report.get("issues") or [])
            ]
        )

        # D-05 single emission path. Rescan is a substrate-wide mutation
        # (upserts ContentProfile rows) so it MUST surface a ChangeEvent even
        # when the diff is empty — auditors need a record that an admin ran
        # the rescan.
        actor_id = resolve_principal_id(request) or "admin"
        await emit_change_event(
            actor_kind="human",
            actor_id=actor_id,
            action="content_profile.rescan",
            resource_type="ContentProfile",
            resource_id="library:rescan",
            before=None,
            after=None,
            scope="library:content_profiles",
            details={
                "added": added,
                "updated": updated,
                "removed": removed,
                "issues": _LAST_ISSUES,
                "reconciled_removed": reconciled_removed,
            },
        )

        return {
            "added": added,
            "updated": updated,
            "removed": removed,
            "issues": _LAST_ISSUES,
            "reconciled_removed": reconciled_removed,
        }


@endpoint("/admin/profiles", methods=["GET"], auth=True, tags=["Admin"])
async def list_loaded_profiles(request: Request) -> Dict[str, Any]:
    """List every bundle currently loadable from ``app/profiles/``.

    Read-only — does not mutate the in-memory index. Useful for the
    admin UI to render a "loaded bundles" table before deciding whether
    to trigger a rescan.
    """
    require_platform_admin(request)
    from app.services.content_profile_loader import load_library_profiles_with_issues

    specs, issues = load_library_profiles_with_issues()
    return {
        "profiles": [
            {
                "slug": s.slug,
                "name": s.name,
                "version": s.version,
                "scope": (s.manifest or {}).get("scope", ""),
                "skill_keys": list(s.skill_keys or []),
                "ships_python": bool(s.ships_python),
                "signature_verified": bool(s.signature_verified),
                "signature_reason": s.signature_reason or "",
                "bundle_fingerprint": s.bundle_fingerprint or "",
                "manifest_fingerprint": s.manifest_fingerprint or "",
            }
            for s in specs
        ],
        "issues": [
            {
                "slug": getattr(issue, "slug", ""),
                "code": getattr(issue, "code", ""),
                "message": getattr(issue, "message", ""),
                "level": getattr(issue, "level", "error"),
                "profile_path": getattr(issue, "profile_path", ""),
            }
            for issue in issues
        ],
    }


@endpoint(
    "/admin/profiles/{slug}/installed", methods=["GET"], auth=True, tags=["Admin"]
)
async def list_workspaces_with_bundle(request: Request, slug: str) -> Dict[str, Any]:
    """Impact preview: which workspaces currently apply this bundle.

    Scans ``Workspace.applied_profiles`` for the slug. Read-only.
    """
    require_platform_admin(request)
    from app.models.nodes import Workspace

    ids: List[str] = []
    try:
        rows = await Workspace.find({}) or []
        if not isinstance(rows, list):
            rows = [rows]
        for ws in rows:
            for entry in getattr(ws, "applied_profiles", []) or []:
                if isinstance(entry, dict) and entry.get("slug") == slug:
                    ids.append(ws.id)
                    break
    except Exception:
        logger.exception("scanning workspaces for bundle %s failed", slug)
    return {"slug": slug, "workspace_ids": ids, "count": len(ids)}
