"""Library artifact admission checks."""

from pathlib import Path
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


def test_present_bundle_modified_after_validation_is_rejected(tmp_path):
    from app.services.operational_model_loader import compute_bundle_fingerprint

    bundle = Path(tmp_path)
    source = bundle / "operational-model.yaml"
    source.write_text("package: {slug: example}\n", encoding="utf-8")
    fingerprint = compute_bundle_fingerprint(bundle)
    source.write_text("package: {slug: changed}\n", encoding="utf-8")

    with pytest.raises(PackageArtifactTrustError) as excinfo:
        assert_library_artifact_trusted(
            SimpleNamespace(
                id="cp-drifted",
                metadata={
                    "slug": "example",
                    "signature_verified": True,
                    "bundle_fingerprint": fingerprint,
                    "bundle_dir_path": str(bundle),
                },
            )  # type: ignore[arg-type]
        )

    assert excinfo.value.details["expected_bundle_fingerprint"] == fingerprint


@pytest.mark.asyncio
async def test_required_post_install_seed_failure_is_not_suppressed(tmp_path):
    from app.services.bundle_post_seed import run_bundle_post_seed

    bundle = Path(tmp_path)
    seeds = bundle / "seeds"
    seeds.mkdir()
    (seeds / "post_install.py").write_text(
        "async def run(app_node, actor_id):\n    raise RuntimeError('seed failed')\n",
        encoding="utf-8",
    )

    with pytest.raises(RuntimeError, match="Required post-install seed failed"):
        await run_bundle_post_seed(
            SimpleNamespace(id="app-1", source_operational_model_slug="example"),
            "owner-1",
            bundle_dir=str(bundle),
            required=True,
        )
