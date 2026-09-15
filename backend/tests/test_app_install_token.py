"""HMAC install_token mint + verify regression suite — Plan 10-05 Task 1.

Covers:
- Round-trip mint → verify within TTL returns the payload intact.
- TTL=0 immediately-expired token raises AppInstallTokenExpiredError.
- Tampered signature → AppInstallTokenInvalidError.
- Wrong app_id on verify → AppInstallTokenInvalidError (no oracle for the
  attacker — same error class as signature tamper).
- Malformed token shape → AppInstallTokenInvalidError.

Threat-model references (per 10-05-PLAN.md):
- T-10-05-01 (Tampering) — covered by ``test_token_rejected_with_tampered_signature``.
- T-10-05-02 (Replay) — single-use enforcement is the lifecycle service's
  job (state check) and is covered separately in test_app_lifecycle.py.
  This file only covers the token primitive.
"""

from __future__ import annotations

import time

import pytest

from app.exceptions import (
    AppInstallTokenExpiredError,
    AppInstallTokenInvalidError,
)
from app.services.app_install_token import (
    issue_install_token,
    verify_install_token,
)


def test_token_round_trips_within_ttl():
    app_id = "app-test-123"
    tok = issue_install_token(app_id, ttl_hours=1.0)
    payload = verify_install_token(tok, expected_app_id=app_id)
    assert payload["app_id"] == app_id
    assert payload["expires_at"] > payload["issued_at"]
    assert payload["expires_at"] >= int(time.time())
    assert isinstance(payload["nonce"], str) and len(payload["nonce"]) > 0


def test_token_rejected_after_ttl_expiry():
    app_id = "app-expired"
    # TTL of -1 second → token is already expired when minted.
    tok = issue_install_token(app_id, ttl_hours=-1 / 3600.0)
    with pytest.raises(AppInstallTokenExpiredError) as excinfo:
        verify_install_token(tok, expected_app_id=app_id)
    assert "expired" in str(excinfo.value).lower()


def test_token_rejected_with_tampered_signature():
    app_id = "app-tamper"
    tok = issue_install_token(app_id, ttl_hours=1.0)
    # Flip the FIRST character of the signature portion. The first base64url
    # char encodes 6 high bits, so any change perturbs decoded bytes —
    # avoids the last-char edge case where unused trailing bits could make
    # two different chars decode to the same byte string.
    payload_b64, sig_b64 = tok.split(".")
    flipped_first = "A" if sig_b64[0] != "A" else "B"
    tampered = f"{payload_b64}.{flipped_first}{sig_b64[1:]}"
    with pytest.raises(AppInstallTokenInvalidError):
        verify_install_token(tampered, expected_app_id=app_id)


def test_token_rejected_for_wrong_app_id():
    tok = issue_install_token("app-A", ttl_hours=1.0)
    with pytest.raises(AppInstallTokenInvalidError) as excinfo:
        verify_install_token(tok, expected_app_id="app-B")
    assert "app_id" in str(excinfo.value).lower()


def test_token_rejected_for_malformed_shape():
    # Missing the "." separator.
    with pytest.raises(AppInstallTokenInvalidError):
        verify_install_token("not-a-token", expected_app_id="any")
    # Empty string.
    with pytest.raises(AppInstallTokenInvalidError):
        verify_install_token("", expected_app_id="any")
    # Three parts.
    with pytest.raises(AppInstallTokenInvalidError):
        verify_install_token("a.b.c", expected_app_id="any")


def test_token_rejected_for_base64_garbage():
    # Two parts but neither decodes.
    with pytest.raises(AppInstallTokenInvalidError):
        verify_install_token("!@#$.%^&*", expected_app_id="any")


def test_token_payload_is_deterministic_in_app_id():
    """Two tokens for the same app_id decode to compatible payloads."""
    t1 = issue_install_token("app-X", ttl_hours=1.0)
    t2 = issue_install_token("app-X", ttl_hours=1.0)
    # Nonces differ (16 bytes of secrets.token_bytes), so the tokens differ.
    assert t1 != t2
    p1 = verify_install_token(t1, expected_app_id="app-X")
    p2 = verify_install_token(t2, expected_app_id="app-X")
    assert p1["app_id"] == p2["app_id"] == "app-X"
