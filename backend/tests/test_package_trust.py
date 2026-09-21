"""Library artifact admission checks."""

from types import SimpleNamespace

import pytest

from app.exceptions import PackageArtifactTrustError
from app.services.package_trust import assert_library_artifact_trusted


def test_explicit_signature_failure_blocks_package_admission():
    package = SimpleNamespace(
        id="cp-untrusted",
        metadata={
            "slug": "example",
            "signature_verified": False,
            "signature_reason": "verify_failed",
            "bundle_fingerprint": "deadbeef",
        },
    )

    with pytest.raises(PackageArtifactTrustError) as excinfo:
        assert_library_artifact_trusted(package)  # type: ignore[arg-type]

    assert excinfo.value.status_code == 422
    assert excinfo.value.details["signature_reason"] == "verify_failed"


def test_legacy_package_without_signature_metadata_remains_compatible():
    assert_library_artifact_trusted(
        SimpleNamespace(id="cp-legacy", metadata={})  # type: ignore[arg-type]
    )
