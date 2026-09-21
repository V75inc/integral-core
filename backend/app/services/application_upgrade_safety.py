"""Preflight guards for applying a package revision to a populated App."""

from __future__ import annotations

from typing import Any, Dict

from app.exceptions import ContentProfileValidationError
from app.models.nodes import ContentProfile
from app.services.content_profile_diff import compute_entry_impact_for_attached
from app.services.content_profile_merge import (
    preview_effective_app_manifest_after_library_merge,
)
from app.services.migrations.reject_gate import detect_unhandled_breaks


async def assert_package_upgrade_migration_safe(
    *,
    attached_profile: ContentProfile,
    library_profile: ContentProfile,
) -> Dict[str, Any]:
    """Refuse an App package update that would strand existing entries.

    The candidate is the *effective* post-merge profile, including preserved
    tenant customizations and package dependencies.  This makes the check
    authoritative for every update route and prevents the preview/commit
    split from reporting a different schema than the one actually applied.
    """

    candidate = await preview_effective_app_manifest_after_library_merge(
        target_manifest=dict(attached_profile.manifest or {}),
        library_cp=library_profile,
    )
    impacts = await compute_entry_impact_for_attached(
        cp=attached_profile,
        candidate_manifest=candidate,
        sample_limit=20,
    )
    unhandled = detect_unhandled_breaks(
        impacts=impacts,
        migrations=list(candidate.get("migrations") or []),
    )
    if unhandled:
        raise ContentProfileValidationError(
            message=(
                "Package upgrade has no declared migration path for existing "
                "records. Add migrations[].ops before applying the upgrade."
            ),
            details={
                "unhandled_breaks": unhandled,
                "entry_impact": impacts,
                "candidate_manifest": candidate,
            },
        )
    return {
        "candidate_manifest": candidate,
        "entry_impact": impacts,
        "unhandled_breaks": [],
    }


async def start_package_upgrade_migrations(
    *,
    attached_profile: ContentProfile,
    safety: Dict[str, Any],
    actor_id: str,
) -> Dict[str, Any]:
    """Start the declared transforms after a safe package update is applied.

    The runner pre-marks affected records before returning and owns the
    asynchronous completion audit.  Calling it only for an actual impact
    keeps a harmless package metadata update from creating migration noise.
    """

    impacts = list(safety.get("entry_impact") or [])
    requires_migration = any(
        int((impact or {}).get("would_need_migration") or 0) > 0
        or int((impact or {}).get("would_fail_validation") or 0) > 0
        for impact in impacts
    )
    if not requires_migration:
        return {
            "executed": False,
            "status": "not_needed",
            "affected_entry_count": 0,
        }

    from app.services.migrations.runner import run_migration_async

    tracker = await run_migration_async(
        published_cp=attached_profile,
        compiled_manifest=dict(safety["candidate_manifest"]),
        actor_id=actor_id,
    )
    return {"executed": True, **tracker}
