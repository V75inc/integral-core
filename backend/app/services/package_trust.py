"""Admission checks for library package artifacts."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict

from app.exceptions import PackageArtifactTrustError
from app.models.nodes import OperationalModel


def assert_library_artifact_trusted(library_profile: OperationalModel) -> None:
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
                "library_operational_model_id": library_profile.id,
                "slug": str(metadata.get("slug") or ""),
                "signature_reason": str(metadata.get("signature_reason") or "unknown"),
                "bundle_fingerprint": str(metadata.get("bundle_fingerprint") or ""),
            },
        )

    expected_fingerprint = str(metadata.get("bundle_fingerprint") or "")
    source_dir = str(
        metadata.get("bundle_dir_path") or metadata.get("bundle_dir") or ""
    ).strip()
    if not expected_fingerprint or not source_dir:
        return
    bundle_dir = Path(source_dir)
    if not bundle_dir.is_dir():
        # Catalogs may outlive a local development checkout or be restored on
        # another deployment. The stored verified artifact remains usable; a
        # present source is the only case we can and must reconcile here.
        return
    from app.services.operational_model_loader import compute_bundle_fingerprint

    actual_fingerprint = compute_bundle_fingerprint(bundle_dir)
    if actual_fingerprint != expected_fingerprint:
        raise PackageArtifactTrustError(
            message="Package artifact changed after catalog validation.",
            details={
                "library_operational_model_id": library_profile.id,
                "slug": str(metadata.get("slug") or ""),
                "expected_bundle_fingerprint": expected_fingerprint,
                "actual_bundle_fingerprint": actual_fingerprint,
            },
        )
