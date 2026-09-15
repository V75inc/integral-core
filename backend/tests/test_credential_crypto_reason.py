"""The two encryption-key failures must not report the same message.

"Set but not a valid 32-byte key" and "not set at all" send an operator to
completely different fixes. Reporting the first as "not configured" is actively
misleading: the variable IS in their .env, plainly visible, so they go hunting
for an env-loading bug that does not exist.
"""

import base64
import secrets

import pytest

from app.config import settings
from app.services import credential_crypto as cc


@pytest.fixture(autouse=True)
def _clear_env(monkeypatch):
    monkeypatch.delenv("INTEGRAL_CREDENTIAL_ENC_KEY", raising=False)


def _set(monkeypatch, value, *, debug):
    monkeypatch.setattr(settings, "INTEGRAL_CREDENTIAL_ENC_KEY", value or None)
    monkeypatch.setattr(settings, "DEBUG", debug)


def test_absent_key_says_it_is_not_set(monkeypatch):
    _set(monkeypatch, "", debug=False)
    reason = cc.encryption_unavailable_reason()
    assert reason is not None
    assert "is not set" in reason
    assert "openssl rand -base64 32" in reason


def test_invalid_key_does_not_claim_the_variable_is_missing(monkeypatch):
    """The regression: a present-but-malformed value must not read as absent."""
    _set(monkeypatch, "obviously-not-a-32-byte-key", debug=False)
    reason = cc.encryption_unavailable_reason()
    assert reason is not None
    assert "is set but is not a valid 32-byte key" in reason
    # Must NOT tell the operator it is missing — it is right there in their .env.
    assert "is not set" not in reason
    assert "not configured" not in reason


@pytest.mark.parametrize(
    "value",
    [
        base64.b64encode(b"\x11" * 32).decode(),
        (b"\x22" * 32).hex(),
        "x" * 32,
    ],
)
def test_well_formed_keys_are_available(monkeypatch, value):
    _set(monkeypatch, value, debug=False)
    assert cc.encryption_unavailable_reason() is None
    assert cc.encryption_available() is True


def test_a_31_byte_key_is_rejected_as_invalid(monkeypatch):
    """Off-by-one length is the likeliest bad paste; it must report as
    malformed rather than missing."""
    _set(monkeypatch, base64.b64encode(secrets.token_bytes(31)).decode(), debug=False)
    reason = cc.encryption_unavailable_reason()
    assert reason is not None and "not a valid 32-byte key" in reason


def test_dev_mode_requires_explicit_opt_in_to_derive(monkeypatch):
    """DEBUG alone no longer derives a key from SECRET_KEY.

    Deriving silently couples ciphertext to the JWT secret, so a shared DEBUG
    staging box would encrypt credentials under a key that rotates with the
    signing secret. Derivation is now opt-in via
    ``INTEGRAL_CREDENTIAL_ALLOW_DEBUG_DERIVE`` (Full Sweep S7).
    """
    _set(monkeypatch, "", debug=True)
    monkeypatch.setattr(settings, "SECRET_KEY", "dev-secret")
    monkeypatch.delenv("INTEGRAL_CREDENTIAL_ALLOW_DEBUG_DERIVE", raising=False)

    reason = cc.encryption_unavailable_reason()
    assert reason is not None
    assert "is not set" in reason


def test_dev_mode_derives_a_key_when_opted_in(monkeypatch):
    """With the explicit opt-in, DEBUG still derives from SECRET_KEY."""
    _set(monkeypatch, "", debug=True)
    monkeypatch.setattr(settings, "SECRET_KEY", "dev-secret")
    monkeypatch.setenv("INTEGRAL_CREDENTIAL_ALLOW_DEBUG_DERIVE", "1")

    assert cc.encryption_unavailable_reason() is None
