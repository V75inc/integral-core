"""OAuth CSRF ``state`` tokens are signed with the canonical
``settings.SECRET_KEY`` (``JVSPATIAL_JWT_SECRET_KEY``), never a hard-coded
fallback. Both helpers used ``os.getenv("SECRET_KEY") or "integral-dev-secret"``
— production sets only ``JVSPATIAL_JWT_SECRET_KEY``, so the dev fallback was
the live signing key.
"""

from __future__ import annotations

import hashlib

import pytest

from app.config import settings
from app.services.connectors import gmail_oauth, quickbooks_oauth

_MODULES = (gmail_oauth, quickbooks_oauth)


@pytest.mark.smoke
@pytest.mark.parametrize("mod", _MODULES, ids=["gmail", "quickbooks"])
def test_state_signing_key_derives_from_settings_secret(mod, monkeypatch):
    """State tokens are keyed on settings.SECRET_KEY, not the env fallback."""
    monkeypatch.setattr(settings, "SECRET_KEY", "settings-secret-for-oauth-state-test")
    # A stray SECRET_KEY env var must be ignored — it was the old lookup path.
    monkeypatch.setenv("SECRET_KEY", "unrelated-env-secret")

    expected = hashlib.sha256(b"settings-secret-for-oauth-state-test").digest()
    assert mod._signing_key() == expected

    state = mod._build_state("user-1")
    assert mod.verify_state(state, "user-1") is True
    assert mod.verify_state(state, "user-2") is False

    # Rotating the configured secret invalidates outstanding state tokens.
    monkeypatch.setattr(settings, "SECRET_KEY", "rotated-secret-for-oauth-state-test")
    assert mod.verify_state(state, "user-1") is False


@pytest.mark.smoke
@pytest.mark.parametrize("mod", _MODULES, ids=["gmail", "quickbooks"])
def test_empty_secret_refuses_to_sign(mod, monkeypatch):
    """An empty SECRET_KEY raises instead of falling back to a dev secret."""
    monkeypatch.setattr(settings, "SECRET_KEY", "")
    monkeypatch.setenv("SECRET_KEY", "integral-dev-secret")
    with pytest.raises(RuntimeError):
        mod._build_state("user-1")


@pytest.mark.smoke
def test_mcp_oauth_state_signing_key_derives_from_settings_secret(monkeypatch):
    """MCP state tokens are keyed on settings.SECRET_KEY, not a dev fallback.

    ``mcp_oauth`` kept the ``or "integral-dev-secret"`` fallback after the
    same anti-pattern was removed from gmail_oauth / quickbooks_oauth, so a
    deployment that sets only ``JVSPATIAL_JWT_SECRET_KEY`` signed its MCP
    connector CSRF state with a value published in this repository.
    """
    from app.agentive.connectors import mcp_oauth

    monkeypatch.setattr(settings, "SECRET_KEY", "settings-secret-for-mcp-state-test")
    monkeypatch.setenv("SECRET_KEY", "unrelated-env-secret")

    expected = hashlib.sha256(b"settings-secret-for-mcp-state-test").digest()
    assert mcp_oauth._signing_key() == expected

    state = mcp_oauth.sign_mcp_oauth_state("user-1", "conn-1")
    assert mcp_oauth.verify_mcp_oauth_state(state, "user-1") == "conn-1"
    assert mcp_oauth.verify_mcp_oauth_state(state, "user-2") is None

    monkeypatch.setattr(settings, "SECRET_KEY", "rotated-secret-for-mcp-state-test")
    assert mcp_oauth.verify_mcp_oauth_state(state, "user-1") is None


@pytest.mark.smoke
def test_mcp_oauth_empty_secret_refuses_to_sign(monkeypatch):
    """An empty SECRET_KEY raises instead of falling back to a dev secret."""
    from app.agentive.connectors import mcp_oauth

    monkeypatch.setattr(settings, "SECRET_KEY", "")
    monkeypatch.setenv("SECRET_KEY", "integral-dev-secret")
    with pytest.raises(RuntimeError):
        mcp_oauth.sign_mcp_oauth_state("user-1", "conn-1")
