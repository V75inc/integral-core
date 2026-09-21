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
