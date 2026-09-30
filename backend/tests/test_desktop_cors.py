"""Packaged desktop shell CORS (``INTEGRAL_DESKTOP_CORS``).

Chromium sends ``Origin: null`` for ``file://`` pages, which matches no
entry in an explicit CORS allow-list — the bundled Electron renderer (see
``desktop/``) gets every REST call rejected unless the deployment opts in.
Covers the resolver matrix plus the live Starlette preflight behaviour, so
a jvspatial middleware change that breaks opaque-origin matching fails here
instead of in the packaged app.
"""

from __future__ import annotations

import pytest

from app.config import DESKTOP_FILE_ORIGIN, Settings, resolve_cors_origins

pytestmark = [pytest.mark.unit, pytest.mark.smoke]

_BASE = ["http://localhost:9006"]


def test_flag_off_leaves_origins_untouched():
    assert resolve_cors_origins(_BASE, desktop_cors=False) == _BASE


def test_flag_on_appends_opaque_file_origin():
    assert resolve_cors_origins(_BASE, desktop_cors=True) == [
        *_BASE,
        DESKTOP_FILE_ORIGIN,
    ]


def test_flag_on_never_duplicates():
    once = resolve_cors_origins(_BASE, desktop_cors=True)
    assert resolve_cors_origins(once, desktop_cors=True).count(DESKTOP_FILE_ORIGIN) == 1


def test_settings_flag_defaults_off(monkeypatch):
    monkeypatch.delenv("INTEGRAL_DESKTOP_CORS", raising=False)
    assert Settings(_env_file=None).INTEGRAL_DESKTOP_CORS is False


def test_settings_flag_parses_env(monkeypatch):
    monkeypatch.setenv("INTEGRAL_DESKTOP_CORS", "1")
    assert Settings(_env_file=None).INTEGRAL_DESKTOP_CORS is True


async def _preflight_allow_origin(allow_origins: list[str]) -> str | None:
    """Drive a raw ASGI OPTIONS preflight from ``Origin: null``."""
    from fastapi.middleware.cors import CORSMiddleware

    async def downstream(scope, receive, send):
        await send(
            {
                "type": "http.response.start",
                "status": 200,
                "headers": [(b"content-type", b"application/json")],
            }
        )
        await send({"type": "http.response.body", "body": b"{}"})

    app = CORSMiddleware(
        downstream,
        allow_origins=allow_origins,
        allow_methods=["*"],
        allow_headers=["*"],
        allow_credentials=True,
    )
    scope = {
        "type": "http",
        "http_version": "1.1",
        "method": "OPTIONS",
        "scheme": "http",
        "path": "/api/users/me",
        "query_string": b"",
        "root_path": "",
        "headers": [
            (b"origin", b"null"),
            (b"access-control-request-method", b"GET"),
            (b"access-control-request-headers", b"authorization"),
        ],
        "client": ("testclient", 50000),
        "server": ("testserver", 80),
    }

    async def receive():
        return {"type": "http.request", "body": b"", "more_body": False}

    sent: list[dict] = []

    async def send(message):
        sent.append(message)

    await app(scope, receive, send)
    for message in sent:
        if message["type"] == "http.response.start":
            headers = {k.decode(): v.decode() for k, v in message.get("headers", [])}
            return headers.get("access-control-allow-origin")
    raise AssertionError("CORS middleware sent no response start")


async def test_preflight_from_shell_allowed_when_flag_on():
    origins = resolve_cors_origins(_BASE, desktop_cors=True)
    assert await _preflight_allow_origin(origins) == DESKTOP_FILE_ORIGIN


async def test_preflight_from_shell_rejected_when_flag_off():
    origins = resolve_cors_origins(_BASE, desktop_cors=False)
    assert await _preflight_allow_origin(origins) is None
