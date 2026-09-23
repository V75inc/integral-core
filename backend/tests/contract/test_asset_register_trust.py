"""Contract: AC-11 — asset-register bundle signature gate."""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest
from nacl.encoding import Base64Encoder
from nacl.signing import SigningKey

from app.services.operational_model_loader import (
    load_library_operational_models_with_issues,
)
from app.services.operational_model_signature import compute_bundle_payload

REPO = Path(__file__).resolve().parents[3]
ASSET_APP = REPO / "examples" / "asset-register"


def _sign_bundle(bundle_dir: Path, sk: SigningKey) -> None:
    payload = compute_bundle_payload(bundle_dir)
    sig = sk.sign(payload).signature
    (bundle_dir / "signature.bin").write_bytes(sig)


@pytest.mark.contract
def test_asset_register_signed_loads_and_tamper_rejected(tmp_path, monkeypatch):
    assert ASSET_APP.is_dir()
    bundle = tmp_path / "asset-register"
    shutil.copytree(ASSET_APP, bundle)
    sk = SigningKey.generate()
    pub = sk.verify_key.encode(encoder=Base64Encoder).decode()
    _sign_bundle(bundle, sk)
    monkeypatch.setenv("INTEGRAL_OPERATIONAL_MODEL_PUBKEY", pub)

    specs, issues = load_library_operational_models_with_issues(
        package_paths=[tmp_path],
        core_only=False,
        verify_signatures=True,
    )
    assert any(s.slug == "asset-register" for s in specs)
    assert not any(i.code == "signature_verification_failed" for i in issues)

    (bundle / "tools" / "custody.py").write_text("# tampered\n")
    specs2, issues2 = load_library_operational_models_with_issues(
        package_paths=[tmp_path],
        core_only=False,
        verify_signatures=True,
    )
    assert not any(s.slug == "asset-register" for s in specs2)
    assert any(i.code == "signature_verification_failed" for i in issues2)
