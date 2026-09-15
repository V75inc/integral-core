"""Connector ``auth_state`` at-rest encryption — keyed and key-less paths (F-7).

``encrypt_auth_state`` has two behaviours, and both are contracts:

- **Key configured** → every credential-bearing value (``is_auth_state_secret_key``,
  applied recursively) becomes ``v1:`` AES-256-GCM ciphertext; the rest of the
  dict stays readable.
- **No key** → it stores plaintext and logs a warning instead of raising, so a
  key-less install keeps its connector mounts. ``decrypt_auth_state`` passes
  plaintext through, so rows written before and after a key is configured both
  read back.

Every test sets or clears the key itself. Whether a developer's .env happens to
carry ``INTEGRAL_CREDENTIAL_ENC_KEY`` (CI has no .env at all) must not decide
the outcome — that is how the at-rest assertion in
``test_mcp_connector_adapter.py`` went unverified.
"""

from __future__ import annotations

import logging

import pytest

from app.agentive.services import connector_registry_node as crn
from app.services import credential_crypto as cc

pytestmark = [pytest.mark.unit, pytest.mark.smoke]

_TEST_KEY = "A" * 43 + "="  # 32 zero bytes, base64 — throwaway, guards nothing
_SECRET = "super-secret-token"
_REFRESH = "refresh-token-value"


def _auth_state() -> dict:
    return {
        "transport": "stdio",
        "command": "/usr/bin/python3",
        "env": {"MCP_SECRET": _SECRET},
        "oauth": {"tokens": {"refresh_token": _REFRESH}},
    }


@pytest.fixture
def no_enc_key(monkeypatch):
    """No key from any source: env, .env-backed settings, or the DEBUG derive."""
    for var in (
        "INTEGRAL_CREDENTIAL_ENC_KEY",
        "INTEGRAL_CREDENTIAL_ENC_KEY_PREVIOUS",
        "INTEGRAL_CREDENTIAL_ALLOW_DEBUG_DERIVE",
    ):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setattr(cc.settings, "INTEGRAL_CREDENTIAL_ENC_KEY", None)
    monkeypatch.setattr(cc.settings, "INTEGRAL_CREDENTIAL_ENC_KEY_PREVIOUS", None)
    monkeypatch.setattr(cc.settings, "DEBUG", False)
    assert not cc.encryption_available()


@pytest.fixture
def enc_key(no_enc_key, monkeypatch):
    """Exactly one key, the throwaway test key — no ambient previous key."""
    monkeypatch.setenv("INTEGRAL_CREDENTIAL_ENC_KEY", _TEST_KEY)
    assert cc.encryption_available()


def test_key_configured_encrypts_every_secret_at_rest(enc_key):
    """Nested secrets become ciphertext; non-secret fields stay readable."""
    stored = crn.encrypt_auth_state(_auth_state())

    assert stored["env"]["MCP_SECRET"].startswith(cc.CIPHER_PREFIX_V1)
    assert stored["oauth"]["tokens"]["refresh_token"].startswith(cc.CIPHER_PREFIX_V1)
    assert _SECRET not in repr(stored)
    assert _REFRESH not in repr(stored)
    # The mount path reads these to spawn the subprocess — never encrypted.
    assert stored["transport"] == "stdio"
    assert stored["command"] == "/usr/bin/python3"

    assert crn.decrypt_auth_state(stored) == _auth_state()


def test_no_key_stores_plaintext_warns_and_reads_back(no_enc_key, caplog):
    """The degraded path is deliberate: plaintext + a warning, never a raise."""
    with caplog.at_level(logging.WARNING, logger=crn.logger.name):
        stored = crn.encrypt_auth_state(_auth_state())

    assert stored == _auth_state()
    warnings = [
        r
        for r in caplog.records
        if r.name == crn.logger.name and r.levelno == logging.WARNING
    ]
    assert any("stored in plaintext" in r.getMessage() for r in warnings), warnings

    assert crn.decrypt_auth_state(stored) == _auth_state()


def test_mixed_row_reads_back_once_a_key_is_configured(no_enc_key, monkeypatch):
    """A secret written key-less and one written after the key was added coexist."""
    legacy = crn.encrypt_auth_state({"env": {"MCP_SECRET": _SECRET}})
    assert legacy["env"]["MCP_SECRET"] == _SECRET

    monkeypatch.setenv("INTEGRAL_CREDENTIAL_ENC_KEY", _TEST_KEY)
    fresh = crn.encrypt_auth_state({"oauth": {"tokens": {"refresh_token": _REFRESH}}})
    assert fresh["oauth"]["tokens"]["refresh_token"].startswith(cc.CIPHER_PREFIX_V1)

    mixed = {**legacy, **fresh}
    assert crn.decrypt_auth_state(mixed) == {
        "env": {"MCP_SECRET": _SECRET},
        "oauth": {"tokens": {"refresh_token": _REFRESH}},
    }


def test_ciphertext_without_its_key_fails_closed(no_enc_key, monkeypatch):
    """Losing the key yields an empty secret — not ciphertext posing as one."""
    monkeypatch.setenv("INTEGRAL_CREDENTIAL_ENC_KEY", _TEST_KEY)
    stored = crn.encrypt_auth_state({"env": {"MCP_SECRET": _SECRET}})
    monkeypatch.delenv("INTEGRAL_CREDENTIAL_ENC_KEY")

    assert crn.decrypt_auth_state(stored) == {"env": {"MCP_SECRET": ""}}
