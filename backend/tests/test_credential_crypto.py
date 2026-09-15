"""Tests for credential encryption helpers."""

import base64

import pytest

from app.services.credential_crypto import (
    decrypt_secret_from_storage,
    encrypt_secret_for_storage,
    encryption_available,
)


@pytest.fixture
def enc_key(monkeypatch):
    """Provide a deterministic 32-byte credential encryption key via env."""
    key = base64.urlsafe_b64encode(b"0" * 32).decode().rstrip("=")
    monkeypatch.setenv("INTEGRAL_CREDENTIAL_ENC_KEY", key)
    monkeypatch.setenv("DEBUG", "0")
    return key


def test_encrypt_decrypt_roundtrip(enc_key):
    """Round-trip encrypt/decrypt returns the original secret with a v1 prefix."""
    assert encryption_available() is True
    ct = encrypt_secret_for_storage("sk-test-secret-key")
    assert ct.startswith("v1:")
    assert decrypt_secret_from_storage(ct) == "sk-test-secret-key"


def test_encrypt_requires_key(monkeypatch):
    """Encryption fails closed when no key is configured (no DEBUG fallback)."""
    # _current_key() reads the cached settings object, not live env — patch the
    # settings attributes directly so neither an explicit key nor the DEBUG-derived
    # dev key is available, then assert the hard requirement is enforced.
    import app.services.credential_crypto as cc

    monkeypatch.delenv("INTEGRAL_CREDENTIAL_ENC_KEY", raising=False)
    monkeypatch.setattr(cc.settings, "INTEGRAL_CREDENTIAL_ENC_KEY", "", raising=False)
    monkeypatch.setattr(cc.settings, "DEBUG", False, raising=False)
    assert cc.encryption_available() is False
    with pytest.raises(RuntimeError):
        encrypt_secret_for_storage("sk-test")


def test_encrypt_decrypt_with_aad(enc_key):
    """AAD-bound ciphertext decrypts only with the matching AAD; else fails closed."""
    ct = encrypt_secret_for_storage("sk-aad-secret", aad="user-123")
    assert decrypt_secret_from_storage(ct, aad="user-123") == "sk-aad-secret"
    # Wrong AAD must fail closed (returns "" — GCM tag mismatch).
    assert decrypt_secret_from_storage(ct, aad="user-999") == ""
    # Missing AAD on a bound ciphertext must also fail closed.
    assert decrypt_secret_from_storage(ct) == ""
