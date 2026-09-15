"""Unit tests for the auth/public-share rate limiter.

``tests/conftest.py`` sets ``RATE_LIMIT_DISABLED=1`` for the whole suite with
the note "No test exercises the limiter." -- so the only brute-force control in
the system shipped untested. These tests drive the middleware directly rather
than through the app, so they neither need nor disturb that global flag.

The behaviour under test is ``_client_ip``: X-Forwarded-For is attacker-
controlled, and the previous implementation returned its first entry
unconditionally. Any client could therefore rotate the header per request and
get a fresh bucket every time, which silently voided the login, signup and
forgot-password limits.
"""

import time

import pytest
from starlette.applications import Starlette
from starlette.responses import JSONResponse
from starlette.routing import Route
from starlette.testclient import TestClient

from app.middleware.rate_limit import (
    _EVICT_INTERVAL_SECONDS,
    RateLimitMiddleware,
    _client_ip,
    _is_trusted_peer,
    _parse_trusted_proxies,
)

# Part of the per-PR smoke gate (see pyproject [tool.pytest.ini_options] markers).
# CI bills only this subset; the full suite runs locally via `make verify` and nightly.
pytestmark = pytest.mark.smoke


class _FakeClient:
    def __init__(self, host: str) -> None:
        self.host = host


class _FakeRequest:
    """Minimal stand-in: ``_client_ip`` only touches ``client`` and ``headers``."""

    def __init__(self, peer: str, forwarded: str = "") -> None:
        self.client = _FakeClient(peer)
        self.headers = {"x-forwarded-for": forwarded} if forwarded else {}


class TestTrustedProxyParsing:
    def test_parses_ips_and_cidrs_and_drops_junk(self):
        nets = _parse_trusted_proxies("10.0.0.1, 192.168.0.0/16, not-an-ip, ")
        assert len(nets) == 2

    def test_empty_default_trusts_private_and_loopback_only(self):
        assert _is_trusted_peer("127.0.0.1", []) is True
        assert _is_trusted_peer("10.1.2.3", []) is True
        assert _is_trusted_peer("192.168.5.5", []) is True
        assert _is_trusted_peer("8.8.8.8", []) is False

    def test_non_ip_peer_is_never_trusted(self):
        """The ASGI test peer is the literal string "testclient"."""
        assert _is_trusted_peer("testclient", []) is False
        assert _is_trusted_peer("", []) is False

    def test_explicit_allowlist_overrides_private_default(self):
        nets = _parse_trusted_proxies("172.20.0.5")
        assert _is_trusted_peer("172.20.0.5", nets) is True
        # Private, but not on the explicit list.
        assert _is_trusted_peer("10.1.2.3", nets) is False


class TestClientIpResolution:
    def test_untrusted_peer_header_is_ignored(self):
        """The spoofing case: a public peer cannot choose its own bucket."""
        req = _FakeRequest(peer="8.8.8.8", forwarded="1.2.3.4")
        assert _client_ip(req, []) == "8.8.8.8"

    def test_untrusted_peer_cannot_rotate_buckets(self):
        """Rotating the header must not change the resolved identity."""
        seen = {
            _client_ip(_FakeRequest("8.8.8.8", f"10.0.0.{i}"), []) for i in range(1, 25)
        }
        assert seen == {"8.8.8.8"}

    def test_trusted_peer_header_is_honored(self):
        req = _FakeRequest(peer="10.0.0.9", forwarded="93.184.216.34")
        assert _client_ip(req, []) == "93.184.216.34"

    def test_trusted_peer_takes_rightmost_untrusted_entry(self):
        """The leftmost entry is whatever the caller typed; the rightmost is real.

        A client sending ``X-Forwarded-For: 1.2.3.4`` has that value preserved
        and the address the proxy actually saw appended after it.
        """
        req = _FakeRequest(peer="10.0.0.9", forwarded="1.2.3.4, 93.184.216.34")
        assert _client_ip(req, []) == "93.184.216.34"

    def test_chain_of_internal_hops_walks_left_to_real_client(self):
        req = _FakeRequest(peer="10.0.0.9", forwarded="93.184.216.34, 10.0.0.8")
        assert _client_ip(req, []) == "93.184.216.34"

    def test_trusted_peer_with_no_header_falls_back_to_peer(self):
        assert _client_ip(_FakeRequest("10.0.0.9"), []) == "10.0.0.9"


def _app() -> Starlette:
    async def login(request):
        return JSONResponse({"ok": True})

    async def public_get(request):
        return JSONResponse({"ok": True})

    async def public_post(request):
        return JSONResponse({"ok": True})

    app = Starlette(
        routes=[
            Route("/api/auth/login", login, methods=["POST"]),
            Route(
                "/api/public-share/track/tok/entries",
                public_get,
                methods=["GET"],
            ),
            Route(
                "/api/public-share/track/tok/entries",
                public_post,
                methods=["POST"],
            ),
        ]
    )
    app.add_middleware(RateLimitMiddleware)
    return app


@pytest.fixture
def limited_client(monkeypatch):
    from app.config import settings

    monkeypatch.setattr(settings, "RATE_LIMIT_DISABLED", False)
    monkeypatch.setattr(settings, "RATE_LIMIT_LOGIN", "3/60")
    monkeypatch.setattr(settings, "RATE_LIMIT_PUBLIC_SHARE_WRITE", "2/60")
    monkeypatch.setattr(settings, "RATE_LIMIT_PUBLIC_SHARE_READ", "5/60")
    monkeypatch.setattr(settings, "RATE_LIMIT_TRUSTED_PROXIES", "")
    with TestClient(_app()) as c:
        yield c


class TestLimiterEndToEnd:
    def test_login_limit_trips(self, limited_client):
        codes = [limited_client.post("/api/auth/login").status_code for _ in range(5)]
        assert codes[:3] == [200, 200, 200]
        assert 429 in codes
        assert codes[-1] == 429

    def test_rotating_forwarded_for_does_not_evade_the_login_limit(
        self, limited_client
    ):
        """The regression this whole change exists for."""
        codes = [
            limited_client.post(
                "/api/auth/login", headers={"X-Forwarded-For": f"9.9.9.{i}"}
            ).status_code
            for i in range(1, 8)
        ]
        assert 429 in codes, (
            "rotating X-Forwarded-For evaded the login limit: " f"{codes}"
        )

    def test_429_carries_retry_after(self, limited_client):
        for _ in range(4):
            resp = limited_client.post("/api/auth/login")
        assert resp.status_code == 429
        assert int(resp.headers["Retry-After"]) >= 1
        assert resp.json()["error_code"] == "RATE_LIMITED"

    def test_public_share_writes_are_limited(self, limited_client):
        """Anonymous entry create runs re-embed + hooks; it was unbounded."""
        codes = [
            limited_client.post("/api/public-share/track/tok/entries").status_code
            for _ in range(4)
        ]
        assert codes[:2] == [200, 200]
        assert codes[-1] == 429

    def test_public_share_reads_use_a_separate_window(self, limited_client):
        """Exhausting writes must not lock out reading a shared track."""
        for _ in range(4):
            limited_client.post("/api/public-share/track/tok/entries")
        assert (
            limited_client.get("/api/public-share/track/tok/entries").status_code == 200
        )

    def test_unmatched_paths_are_untouched(self, limited_client):
        for _ in range(20):
            assert limited_client.get(
                "/api/public-share/track/tok/entries"
            ).status_code in (
                200,
                429,
            )


class TestBucketEviction:
    def test_stale_buckets_are_swept(self, monkeypatch):
        from app.config import settings

        monkeypatch.setattr(settings, "RATE_LIMIT_DISABLED", False)
        monkeypatch.setattr(settings, "RATE_LIMIT_LOGIN", "100/1")
        mw = RateLimitMiddleware(app=None)

        now = time.monotonic()

        # Seed buckets that are already outside every window.
        stale_at = now - 10_000
        for i in range(50):
            mw._buckets[("/api/auth/login", f"5.5.5.{i}")].append(stale_at)
        assert len(mw._buckets) == 50

        # Force the cadence check to fire. This must be expressed RELATIVE to
        # `now`, not as an absolute 0.0: `time.monotonic()` counts from an
        # arbitrary origin (boot on Linux), so on a freshly-booted CI runner it
        # is only a double-digit number of seconds. `_last_evict = 0.0` then
        # leaves `now - _last_evict` below _EVICT_INTERVAL_SECONDS and the sweep
        # early-returns -- the test passed on any long-uptime dev machine and
        # failed only in CI.
        mw._last_evict = now - _EVICT_INTERVAL_SECONDS - 1
        with mw._lock:
            mw._evict_expired_locked(now)
        assert mw._buckets == {}, "unbounded bucket growth was attacker-driven"
