"""Admission checks for library package artifacts."""

from __future__ import annotations

from typing import Any, Dict

from app.exceptions import PackageArtifactTrustError
from app.models.nodes import ContentProfile


def assert_library_artifact_trusted(library_profile: ContentProfile) -> None:
    """Fail closed for an artifact the loader has explicitly rejected.

    Legacy catalog rows pre-date signature metadata and remain installable for
    compatibility. A current loader result with ``signature_verified=False``
    is an explicit security verdict and may never be treated as a package
    source by install, merge, or apply paths.
    """

    metadata: Dict[str, Any] = dict(getattr(library_profile, "metadata", None) or {})
    if metadata.get("signature_verified") is False:
        raise PackageArtifactTrustError(
            message="Package artifact signature verification failed.",
            details={
                "library_content_profile_id": library_profile.id,
                "slug": str(metadata.get("slug") or ""),
                "signature_reason": str(metadata.get("signature_reason") or "unknown"),
                "bundle_fingerprint": str(metadata.get("bundle_fingerprint") or ""),
            },
        )
