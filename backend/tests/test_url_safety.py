"""Tests for URL attachment SSRF guardrails."""

import socket

import pytest

from app.exceptions import BadRequestError
from app.services.url_safety import validate_public_http_url


@pytest.mark.asyncio
async def test_rejects_literal_loopback_ipv4():
    with pytest.raises(BadRequestError):
        await validate_public_http_url("http://127.0.0.1/internal")


@pytest.mark.asyncio
async def test_rejects_private_literal_ipv4():
    with pytest.raises(BadRequestError):
        await validate_public_http_url("http://192.168.0.1/share")


@pytest.mark.asyncio
async def test_rejects_blocked_hostname_localhost():
    with pytest.raises(BadRequestError):
        await validate_public_http_url("http://localhost/path")


@pytest.mark.asyncio
async def test_accepts_when_dns_resolves_to_public(monkeypatch):
    def fake_getaddrinfo(host, port, family=0, type=0, proto=0, flags=0):
        assert host == "example.com"
        return [
            (
                socket.AF_INET,
                socket.SOCK_STREAM,
                6,
                "",
                ("93.184.216.34", 0),
            )
        ]

    monkeypatch.setattr(socket, "getaddrinfo", fake_getaddrinfo)
    await validate_public_http_url("https://example.com/doc")


@pytest.mark.asyncio
async def test_rejects_when_dns_resolves_to_loopback(monkeypatch):
    def fake_getaddrinfo(host, port, family=0, type=0, proto=0, flags=0):
        return [
            (
                socket.AF_INET,
                socket.SOCK_STREAM,
                6,
                "",
                ("127.0.0.2", 0),
            )
        ]

    monkeypatch.setattr(socket, "getaddrinfo", fake_getaddrinfo)
    with pytest.raises(BadRequestError):
        await validate_public_http_url("https://evil.example/res")
