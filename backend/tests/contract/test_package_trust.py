"""Contract: package trust — signature gate + trust tier (AC-11, WP-04)."""

from __future__ import annotations

from pathlib import Path

import pytest
from nacl.encoding import Base64Encoder
from nacl.signing import SigningKey

from app.services.content_profile_loader import load_library_profiles_with_issues
from app.services.content_profile_signature import compute_bundle_payload
from app.services.hooks.errors import ToolTrustTierDeniedError
from app.services.hooks.install_hook import register_bundle_on_install
from app.services.hooks.trust import check_operations_permitted, check_tools_permitted


def _make_signed_tools_bundle(tmp_path: Path, sk: SigningKey, *, tamper: bool = False):
    bundle = tmp_path / "trust-bundle"
    bundle.mkdir()
    (bundle / "profile.yaml").write_text(
        "integral_profile_version: 3\n"
        "scope: app\n"
        "package:\n"
        "  slug: trust-bundle\n"
        "  name: Trust Bundle\n"
        "  version: 1.0.0\n"
        "  trust_tier: trusted\n"
        "app:\n"
        "  tools:\n"
        "  - key: echo\n"
        "    handler_ref: tools.echo:run\n"
    )
    tools = bundle / "tools"
    tools.mkdir()
    (tools / "echo.py").write_text("async def run(input, ctx):\n    return {'ok': True}\n")
    payload = compute_bundle_payload(bundle)
    sig = sk.sign(payload).signature
    (bundle / "signature.bin").write_bytes(sig)
    if tamper:
        (tools / "echo.py").write_text("async def run(input, ctx):\n    return {'ok': False}\n")
    return bundle


@pytest.mark.contract
def test_tampered_signed_bundle_excluded_from_catalog(tmp_path, monkeypatch):
    sk = SigningKey.generate()
    pub = sk.verify_key.encode(encoder=Base64Encoder).decode()
    bundle = _make_signed_tools_bundle(tmp_path, sk, tamper=True)
    monkeypatch.setenv("INTEGRAL_PROFILE_PUBKEY", pub)

    specs, issues = load_library_profiles_with_issues(
        package_paths=[tmp_path],
        core_only=False,
        verify_signatures=True,
    )
    assert not any(s.slug == "trust-bundle" for s in specs)
    assert any(i.code == "signature_verification_failed" for i in issues)


@pytest.mark.contract
@pytest.mark.asyncio
async def test_untrusted_tier_rejects_tools_and_operations():
    with pytest.raises(ToolTrustTierDeniedError):
        check_tools_permitted("community", 1, "bad-bundle")
    with pytest.raises(ToolTrustTierDeniedError):
        check_operations_permitted("community", 1, "bad-bundle")


@pytest.mark.contract
@pytest.mark.asyncio
async def test_register_bundle_rejects_untrusted_operations():
    canonical = {
        "package": {"slug": "untrusted-ops", "trust_tier": "community"},
        "app": {
            "operations": [
                {
                    "key": "echo",
                    "kind": "execute",
                    "tool": "echo",
                    "policy_action": "app.read",
                }
            ]
        },
    }
    with pytest.raises(ToolTrustTierDeniedError):
        await register_bundle_on_install(
            workspace_id="ws-trust",
            canonical=canonical,
            app_id="n.App.untrusted",
        )
