"""Catalog env merge + QuickBooks MCP token-store helpers."""

from __future__ import annotations

import pytest

from app.connectors.stdio_env import (
    merge_install_env,
    persist_quickbooks_token_store,
    spawn_env_from_quickbooks_oauth,
    write_dotenv_file,
)


def test_merge_install_env_defaults_then_secrets(monkeypatch):
    monkeypatch.delenv("QUICKBOOKS_CLIENT_ID", raising=False)
    monkeypatch.setattr(
        "app.config.settings.QUICKBOOKS_CLIENT_ID",
        "env-id",
        raising=False,
    )
    entry = {
        "env_defaults": {"QUICKBOOKS_DISABLE_WRITE": "true"},
        "auth": {
            "fields": [
                {"name": "QUICKBOOKS_CLIENT_ID", "required": False},
                {"name": "QUICKBOOKS_DISABLE_WRITE", "required": False},
            ]
        },
    }
    merged = merge_install_env(
        entry,
        {"QUICKBOOKS_DISABLE_WRITE": "false"},
    )
    assert merged["QUICKBOOKS_CLIENT_ID"] == "env-id"
    assert merged["QUICKBOOKS_DISABLE_WRITE"] == "false"


def test_persist_quickbooks_token_store_strips_secrets(tmp_path, monkeypatch):
    monkeypatch.setattr(
        "app.connectors.stdio_env.TOKEN_STORE_ROOT",
        tmp_path,
    )
    path, spawn, persist = persist_quickbooks_token_store(
        workspace_id="ws.abc",
        env={
            "QUICKBOOKS_CLIENT_ID": "cid",
            "QUICKBOOKS_CLIENT_SECRET": "sec",
            "QUICKBOOKS_REFRESH_TOKEN": "rtok",
            "QUICKBOOKS_REALM_ID": "realm",
            "QUICKBOOKS_ENVIRONMENT": "sandbox",
            "QUICKBOOKS_DISABLE_WRITE": "true",
        },
    )
    assert path.is_file()
    assert spawn["QUICKBOOKS_TOKEN_STORE_PATH"] == str(path)
    assert spawn["QUICKBOOKS_REFRESH_TOKEN"] == "rtok"
    assert set(persist) == {
        "QUICKBOOKS_TOKEN_STORE_PATH",
        "QUICKBOOKS_ENVIRONMENT",
    }
    assert persist["QUICKBOOKS_ENVIRONMENT"] == "sandbox"
    assert "QUICKBOOKS_REFRESH_TOKEN" not in persist
    body = path.read_text(encoding="utf-8")
    assert "QUICKBOOKS_REFRESH_TOKEN=rtok" in body


def test_persist_quickbooks_token_store_requires_client(tmp_path, monkeypatch):
    monkeypatch.setattr(
        "app.connectors.stdio_env.TOKEN_STORE_ROOT",
        tmp_path,
    )
    with pytest.raises(ValueError, match="QUICKBOOKS_CLIENT_ID"):
        persist_quickbooks_token_store(
            workspace_id="ws",
            env={
                "QUICKBOOKS_REFRESH_TOKEN": "rtok",
                "QUICKBOOKS_REALM_ID": "realm",
            },
        )


def test_write_dotenv_file_quotes_spaces(tmp_path):
    path = write_dotenv_file(tmp_path / "x.env", {"FOO": "bar baz"})
    assert path.read_text(encoding="utf-8") == 'FOO="bar baz"\n'


def test_spawn_env_from_quickbooks_oauth_maps_tokens(monkeypatch):
    monkeypatch.delenv("QUICKBOOKS_CLIENT_ID", raising=False)
    env = spawn_env_from_quickbooks_oauth(
        auth_state={
            "client_id": "cid",
            "client_secret": "sec",
            "environment": "sandbox",
            "QUICKBOOKS_DISABLE_WRITE": "false",
        },
        refresh_token="rtok",
        realm_id="realm-1",
        env_defaults={
            "QUICKBOOKS_DISABLE_WRITE": "true",
            "QUICKBOOKS_DISABLE_UPDATE": "true",
            "QUICKBOOKS_DISABLE_DELETE": "true",
        },
    )
    assert env["QUICKBOOKS_CLIENT_ID"] == "cid"
    assert env["QUICKBOOKS_REFRESH_TOKEN"] == "rtok"
    assert env["QUICKBOOKS_REALM_ID"] == "realm-1"
    assert env["QUICKBOOKS_DISABLE_WRITE"] == "false"
    assert env["QUICKBOOKS_DISABLE_UPDATE"] == "true"
