"""The DNS pin must actually engage on the code path httpx uses.

``pin_public_dns`` resolves a host, asserts every address is public, and pins
the result so the socket connects to a validated address — closing the
validate-then-connect TOCTOU window that DNS rebinding exploits.

It was inert. anyio encodes the host to **bytes** before calling
``socket.getaddrinfo`` (``anyio/_core/_sockets.py``), so the
``isinstance(host, str)`` key lookup never matched: the pin table was populated
and then never consulted for any real fetch. Every pin in the MCP session,
redirect-guard and OAuth token paths was decorative.
"""

from __future__ import annotations

import socket

import pytest

from app.services import url_safety

pytestmark = pytest.mark.smoke


def test_pin_key_normalizes_the_bytes_host_anyio_passes():
    """The regression: anyio hands getaddrinfo bytes, not str."""
    assert url_safety._pin_key(b"Example.COM") == "example.com"
    assert url_safety._pin_key("Example.COM") == "example.com"
    assert url_safety._pin_key(None) is None


def test_pinned_bytes_host_is_served_from_the_pin(monkeypatch):
    """A bytes host must hit the pin table, not the real resolver."""
    real_calls = []

    def _fake_real(host, port, family=0, type=0, proto=0, flags=0):  # noqa: A002
        real_calls.append(host)
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("203.0.113.9", port))]

    monkeypatch.setattr(url_safety, "_real_getaddrinfo", _fake_real)
    monkeypatch.setattr(
        url_safety,
        "_pinned_hosts",
        {
            "pinned.example": [
                [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("198.51.100.7", 0))]
            ]
        },
    )

    out = url_safety._pinning_getaddrinfo(b"pinned.example", 443)

    assert out and out[0][4][0] == "198.51.100.7", out
    assert real_calls == [], "pinned host must not reach the real resolver"


def test_unpinned_host_still_falls_through(monkeypatch):
    """Hosts we never pinned resolve normally."""
    called = []

    def _fake_real(host, port, family=0, type=0, proto=0, flags=0):  # noqa: A002
        called.append(host)
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("203.0.113.9", port))]

    monkeypatch.setattr(url_safety, "_real_getaddrinfo", _fake_real)
    monkeypatch.setattr(url_safety, "_pinned_hosts", {})

    url_safety._pinning_getaddrinfo(b"unpinned.example", 443)
    assert called == [b"unpinned.example"]


def test_family_mismatch_fails_closed_instead_of_re_resolving(monkeypatch):
    """A pinned host with no address for the requested family must not
    silently re-resolve — that is the bypass the pin exists to prevent."""
    monkeypatch.setattr(
        url_safety,
        "_real_getaddrinfo",
        lambda *a, **k: pytest.fail("must not reach the real resolver"),
    )
    monkeypatch.setattr(
        url_safety,
        "_pinned_hosts",
        {
            "v4only.example": [
                [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("198.51.100.7", 0))]
            ]
        },
    )

    with pytest.raises(socket.gaierror):
        url_safety._pinning_getaddrinfo(b"v4only.example", 443, socket.AF_INET6)


@pytest.mark.asyncio
async def test_token_exchange_refuses_state_with_no_issuer():
    """Missing issuer must fail closed, not waive the same-origin bind.

    Making the bind conditional on a reference being present hands the bypass
    straight back: ``auth_state`` is writable, so an attacker PATCHes an
    ``oauth`` blob carrying a ``token_endpoint`` and no ``issuer``.
    """
    from app.agentive.connectors.mcp_oauth import _guard_token_endpoint
    from app.api.errors import ConnectorAuthError

    with pytest.raises(ConnectorAuthError):
        await _guard_token_endpoint(
            {"token_endpoint": "https://attacker.example/token"}
        )
