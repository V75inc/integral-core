"""Contract: Asset Register builds to a portable deterministic App artifact."""

from __future__ import annotations

import hashlib
import subprocess
import sys
import tarfile
from pathlib import Path

import pytest

from app.services.content_profile_loader import load_library_profiles_with_issues

REPO = Path(__file__).resolve().parents[3]
BUILD_SCRIPT = REPO / "examples" / "asset-register" / "build.py"


def _build(output_dir: Path) -> Path:
    subprocess.run(
        [sys.executable, str(BUILD_SCRIPT), "--out-dir", str(output_dir)],
        check=True,
        capture_output=True,
        text=True,
    )
    archive = next(output_dir.glob("asset-register-*.tar.gz"))
    return archive


@pytest.mark.contract
def test_asset_register_artifact_is_deterministic_and_loadable(tmp_path):
    first = _build(tmp_path / "first")
    second = _build(tmp_path / "second")

    first_digest = hashlib.sha256(first.read_bytes()).hexdigest()
    assert first_digest == hashlib.sha256(second.read_bytes()).hexdigest()
    assert (
        first.with_suffix(first.suffix + ".sha256").read_text().startswith(first_digest)
    )

    extracted = tmp_path / "extensions"
    extracted.mkdir()
    with tarfile.open(first, "r:gz") as bundle:
        names = bundle.getnames()
        assert "asset-register/profile.yaml" in names
        assert "asset-register/tools/custody.py" in names
        assert not any("__pycache__" in name or name.endswith(".pyc") for name in names)
        bundle.extractall(extracted)

    specs, issues = load_library_profiles_with_issues(
        package_paths=[extracted], core_only=False, verify_signatures=False
    )
    assert not issues
    spec = next(spec for spec in specs if spec.slug == "asset-register")
    assert spec.bundle_dir == extracted / "asset-register"
