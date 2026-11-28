"""The stdio spawn environment is an allowlist (S-MCP-RCE, second pass).

The first fix used a denylist of "dangerous" env vars. That is the wrong shape
for something feeding ``npx``: the catalog command is resolved through ``PATH``,
so an attacker-supplied ``auth_state.env`` of ``PATH=/tmp/evil`` ran their
binary under a "vetted" command name. Enumerating the dangerous keys can never
be complete — PATH, NODE_PATH, NPM_CONFIG_*, GIT_CONFIG_* all redirect
execution — so a connector may only contribute keys its own catalog entry
declares.
"""

from __future__ import annotations

import pytest

from app.agentive.connectors.mcp_client import (
    _allowed_spawn_env_keys,
    _build_spawn_env,
)

pytestmark = pytest.mark.smoke

_EXEC_REDIRECTING = {
    "PATH": "/tmp/evil",
    "NODE_PATH": "/tmp/evil",
    "NODE_OPTIONS": "--require=/tmp/x",
    "NPM_CONFIG_REGISTRY": "http://evil.example",
    "NPM_CONFIG_PREFIX": "/tmp/evil",
    "GIT_CONFIG_GLOBAL": "/tmp/evil.gitconfig",
    "GIT_SSH_COMMAND": "/tmp/evil.sh",
    "LD_PRELOAD": "/tmp/evil.so",
    "PYTHONPATH": "/tmp/evil",
}


def test_path_cannot_be_overridden_by_connector_env():
    """The regression: PATH decides which binary `npx` resolves to."""
    base = _build_spawn_env({"transport": "stdio", "catalog_slug": "quickbooks_mcp"})
    poisoned = _build_spawn_env(
        {
            "transport": "stdio",
            "catalog_slug": "quickbooks_mcp",
            "env": {"PATH": "/tmp/evil"},
        }
    )
    assert poisoned.get("PATH") == base.get("PATH")
    assert poisoned.get("PATH") != "/tmp/evil"


def test_execution_redirecting_env_is_dropped():
    """None of the interpreter/package-manager redirection vars survive."""
    env = _build_spawn_env(
        {
            "transport": "stdio",
            "catalog_slug": "quickbooks_mcp",
            "env": dict(_EXEC_REDIRECTING),
        }
    )
    for key, planted in _EXEC_REDIRECTING.items():
        assert env.get(key) != planted, f"{key} reached the spawn environment"


def test_catalog_declared_keys_still_flow():
    """The allowlist must not break the legitimate QuickBooks spawn."""
    env = _build_spawn_env(
        {
            "transport": "stdio",
            "catalog_slug": "quickbooks_mcp",
            "env": {
                "QUICKBOOKS_REFRESH_TOKEN": "refresh-abc",
                "QUICKBOOKS_DISABLE_WRITE": "true",
            },
        }
    )
    assert env["QUICKBOOKS_REFRESH_TOKEN"] == "refresh-abc"
    assert env["QUICKBOOKS_DISABLE_WRITE"] == "true"


def test_undeclared_keys_are_dropped_even_when_harmless():
    """Allowlist, not denylist — an undeclared key does not pass just because
    it is not on any danger list."""
    env = _build_spawn_env(
        {
            "transport": "stdio",
            "catalog_slug": "quickbooks_mcp",
            "env": {"SOME_UNDECLARED_KEY": "x"},
        }
    )
    assert "SOME_UNDECLARED_KEY" not in env


def test_unknown_catalog_slug_contributes_nothing():
    """A forged slug cannot widen the allowlist or inherit another package's keys."""
    allowed = _allowed_spawn_env_keys(
        {"transport": "stdio", "catalog_slug": "not-a-real-slug"}
    )
    assert allowed == set()
    assert "QUICKBOOKS_REFRESH_TOKEN" not in allowed


def test_never_from_connector_wins_over_a_catalog_declaration():
    """Even if a catalog entry named PATH, it must not be contributable."""
    from app.agentive.connectors import mcp_client

    assert "PATH" in mcp_client._NEVER_FROM_CONNECTOR
    assert "PATH" not in _allowed_spawn_env_keys(
        {"transport": "stdio", "catalog_slug": "quickbooks_mcp"}
    )


def test_stored_spawn_env_wins(monkeypatch):
    """A stored spawn env wins. An older snapshot uses the admitted package."""
    monkeypatch.setattr(
        "app.connectors.package_runtime.admitted_manifest",
        lambda package_id: (
            {
                "capabilities": {
                    "mcp": {
                        "env": {"OTHER": {"value": "1"}},
                        "generated_env": [],
                    }
                }
            }
            if package_id == "google_drive"
            else None
        ),
    )
    stored = {
        "id": "acme_stdio",
        "capabilities": {
            "mcp": {
                "env": {
                    "ACME_REGION": {"value": "eu"},
                    "PATH": {"value": "/tmp/evil"},
                },
                "generated_env": ["ACME_TOKEN"],
            }
        },
    }
    auth = {
        "catalog_slug": "google_drive",
        "access_token": "tok",
        "env": {"ACME_TOKEN": "secret"},
    }
    env = _build_spawn_env(auth, manifest=stored)
    assert env["ACME_REGION"] == "eu"
    assert env["ACME_TOKEN"] == "secret"
    assert "OTHER" not in env
    assert env.get("PATH") != "/tmp/evil"
    assert auth["access_token"] == "tok"
    older = {"capabilities": {"mcp": {"transport": "stdio"}}}
    allowed = _allowed_spawn_env_keys(auth, older)
    assert "OTHER" in allowed
    assert "ACME_REGION" not in allowed
    with pytest.raises(ValueError, match="does not declare spawn env"):
        _build_spawn_env(auth, manifest={"capabilities": {"mcp": {"env": ["nope"]}}})
