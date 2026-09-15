"""Voice input needs the microphone and the STT provider origin in every header source.

Three places emit the SPA/API security headers — the backend middleware and
the two nginx templates. A dictation button that works on the Vite dev
server (which sets no headers) and dies in production with ``NotAllowedError``
is exactly the drift these tests exist to catch.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from app.middleware import security_headers as sh

FRONTEND_DIR = Path(__file__).resolve().parents[2] / "frontend"
NGINX_TEMPLATES = ("nginx.conf", "nginx.docker.conf")


def _nginx_header(template: str, header: str) -> str:
    path = FRONTEND_DIR / template
    if not path.is_file():
        pytest.skip(f"{template} not present in this checkout")
    match = re.search(
        rf'add_header {re.escape(header)} "([^"]+)"', path.read_text("utf-8")
    )
    assert match, f"{template} has no {header} header"
    return match.group(1)


def _connect_src_tokens(csp: str) -> list[str]:
    for directive in csp.split(";"):
        parts = directive.split()
        if parts and parts[0] == "connect-src":
            return parts[1:]
    raise AssertionError("CSP has no connect-src directive")


def test_backend_permissions_policy_allows_same_origin_microphone():
    """The API opens the microphone to its own origin and nothing else."""
    assert "microphone=(self)" in sh.PERMISSIONS_POLICY
    assert "camera=()" in sh.PERMISSIONS_POLICY
    assert "geolocation=()" in sh.PERMISSIONS_POLICY


def test_backend_csp_keeps_speech_origin_when_extra_connect_is_set(monkeypatch):
    """CSP_EXTRA_CONNECT adds origins; it never drops the speech origin."""
    monkeypatch.delenv("CSP_OVERRIDE", raising=False)
    monkeypatch.setenv("CSP_EXTRA_CONNECT", "https://api.example.test")
    tokens = _connect_src_tokens(sh._build_csp())
    assert "'self'" in tokens
    assert "https://api.example.test" in tokens
    for origin in sh.SPEECH_CONNECT_ORIGINS.split():
        assert origin in tokens


@pytest.mark.parametrize("template", NGINX_TEMPLATES)
def test_nginx_permissions_policy_matches_backend(template):
    """Each nginx template sends the backend's exact Permissions-Policy."""
    policy = _nginx_header(template, "Permissions-Policy")
    assert "microphone=(self)" in policy
    assert policy == sh.PERMISSIONS_POLICY


@pytest.mark.parametrize("template", NGINX_TEMPLATES)
def test_nginx_connect_src_carries_speech_origin_before_env_extras(template):
    """The speech origin is a literal ahead of ${CSP_EXTRA_CONNECT}.

    A literal before the variable survives an env override that replaces the
    derived default.
    """
    tokens = _connect_src_tokens(_nginx_header(template, "Content-Security-Policy"))
    extras_at = tokens.index("${CSP_EXTRA_CONNECT}")
    for origin in sh.SPEECH_CONNECT_ORIGINS.split():
        assert origin in tokens, f"{template} connect-src is missing {origin}"
        assert tokens.index(origin) < extras_at
