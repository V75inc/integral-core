import hashlib
import os
from pathlib import Path

import pytest
from nacl.encoding import Base64Encoder
from nacl.signing import SigningKey


@pytest.fixture
def keypair():
    sk = SigningKey.generate()
    return sk, sk.verify_key.encode(encoder=Base64Encoder).decode()


def _make_bundle(tmp_path: Path, ships_python: bool):
    bundle = tmp_path / "bundle-x"
    bundle.mkdir()
    (bundle / "profile.yaml").write_text(
        "integral_profile_version: 3\nscope: track\npackage:\n  slug: bundle-x\n  name: B\n  version: 1.0.0\n"
    )
    if ships_python:
        (bundle / "skills").mkdir()
        s = bundle / "skills" / "k"
        s.mkdir()
        (s / "SKILL.md").write_text("---\nname: k\n---\nbody")
        (s / "scripts").mkdir()
        (s / "scripts" / "tool.py").write_text(
            "def get_tool_definition():\n    return {}\nasync def execute(args):\n    return None\n"
        )
    return bundle


def test_dev_mode_returns_verified_with_reason(tmp_path):
    from app.services.content_profile_signature import verify_bundle_signature

    bundle = _make_bundle(tmp_path, ships_python=True)
    r = verify_bundle_signature(bundle, pubkey_b64=None)
    assert r.verified is True
    assert r.reason == "dev_mode"


def test_prod_missing_signature_with_python_fails(tmp_path, keypair):
    from app.services.content_profile_signature import verify_bundle_signature

    _, pub = keypair
    bundle = _make_bundle(tmp_path, ships_python=True)
    r = verify_bundle_signature(bundle, pubkey_b64=pub)
    assert r.verified is False
    assert "signature.bin" in r.reason


def test_prod_missing_signature_without_python_passes(tmp_path, keypair):
    from app.services.content_profile_signature import verify_bundle_signature

    _, pub = keypair
    bundle = _make_bundle(tmp_path, ships_python=False)
    r = verify_bundle_signature(bundle, pubkey_b64=pub)
    assert r.verified is True


def test_valid_signature_passes(tmp_path, keypair):
    from app.services.content_profile_signature import (
        compute_bundle_payload,
        verify_bundle_signature,
    )

    sk, pub = keypair
    bundle = _make_bundle(tmp_path, ships_python=True)
    payload = compute_bundle_payload(bundle)
    sig = sk.sign(payload).signature
    (bundle / "signature.bin").write_bytes(sig)
    r = verify_bundle_signature(bundle, pubkey_b64=pub)
    assert r.verified is True


def test_tampered_signature_fails(tmp_path, keypair):
    from app.services.content_profile_signature import (
        compute_bundle_payload,
        verify_bundle_signature,
    )

    sk, pub = keypair
    bundle = _make_bundle(tmp_path, ships_python=True)
    payload = compute_bundle_payload(bundle)
    sig = sk.sign(payload).signature
    (bundle / "signature.bin").write_bytes(sig)
    # tamper after signing
    (bundle / "skills" / "k" / "SKILL.md").write_text("---\nname: k\n---\nNEWBODY")
    r = verify_bundle_signature(bundle, pubkey_b64=pub)
    assert r.verified is False
