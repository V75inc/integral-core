"""In-process rate limiting for auth endpoints.

Token-bucket per (client-ip, endpoint-prefix). Suitable for a single-
process FastAPI server behind a reverse proxy. For multi-instance
deployments (Day 12+) replace with Redis or a dedicated rate-limit
sidecar.

Defaults:
  - /api/auth/login          → 10 requests / 60 seconds / IP
  - /api/auth/signup         →  5 requests / 60 seconds / IP
  - /api/auth/forgot-password → 3 requests / 60 seconds / IP
  - /api/auth/reset-password →  5 requests / 60 seconds / IP
  - /api/public-share/** (write) → 20 requests / 60 seconds / IP
  - /api/public-share/** (read)  → 120 requests / 60 seconds / IP
  - /api/mcp/**                  → 120 requests / 60 seconds / IP
  - /api/agentive/**             → 120 requests / 60 seconds / IP
  - /api/chat/**                 →  60 requests / 60 seconds / IP

Override via env:
  RATE_LIMIT_LOGIN, RATE_LIMIT_REGISTER, RATE_LIMIT_FORGOT,
  RATE_LIMIT_RESET, RATE_LIMIT_PUBLIC_SHARE_WRITE,
  RATE_LIMIT_PUBLIC_SHARE_READ, RATE_LIMIT_MCP, RATE_LIMIT_AGENTIVE,
  RATE_LIMIT_CHAT — all in the form "<count>/<seconds>".
  RATE_LIMIT_DISABLED=1 — turns the middleware into a no-op.
  RATE_LIMIT_TRUSTED_PROXIES — CSV of IPs/CIDRs whose X-Forwarded-For may be
  believed. Empty means "private + loopback peers", matching the compose /
  Swarm topology. See ``_client_ip`` for why this is not optional.

  NOTE: buckets are in-process. Multi-worker deployments multiply effective
  limits by worker count until a shared limiter lands.

Client identity is derived in ``_client_ip``: X-Forwarded-For is caller-
supplied and is only read when the immediate peer is a trusted proxy, then
walked right-to-left. Trusting it blindly (the previous behaviour) let any
client rotate the header per request and mint a fresh bucket each time,
nullifying every limit above.
"""

from __future__ import annotations

import ipaddress
import time
from collections import defaultdict, deque
from threading import Lock
from typing import Any, Awaitable, Callable, Deque, Dict, Tuple

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse, Response

from app.config import settings

# Methods that mutate state on the public-share surface.
_WRITE_METHODS = frozenset({"POST", "PUT", "PATCH", "DELETE"})
_READ_METHODS = frozenset({"GET", "HEAD"})

# How often to sweep dead buckets out of the in-process store.
_EVICT_INTERVAL_SECONDS = 300.0


def _parse_rate(spec: str, default: Tuple[int, int]) -> Tuple[int, int]:
    """Parse "<count>/<seconds>" into (count, seconds), falling back to default on error."""
    try:
        count_str, seconds_str = (spec or "").split("/", 1)
        count, seconds = int(count_str), int(seconds_str)
        if count > 0 and seconds > 0:
            return count, seconds
    except Exception:
        pass
    return default


def _parse_trusted_proxies(spec: str) -> list:
    """Parse a CSV of IPs / CIDRs into ip_network objects (invalid entries dropped)."""
    nets = []
    for raw in (spec or "").split(","):
        item = raw.strip()
        if not item:
            continue
        try:
            nets.append(ipaddress.ip_network(item, strict=False))
        except ValueError:
            continue
    return nets


def _is_trusted_peer(host: str, trusted: list) -> bool:
    """True when the immediate peer is allowed to speak for X-Forwarded-For.

    With an explicit allowlist configured, only those networks count. With none
    configured we fall back to "private or loopback", which is the compose /
    Swarm shape: the reverse proxy sits on a private network with the API and
    real clients never reach the socket directly.
    """
    try:
        addr = ipaddress.ip_address(host)
    except ValueError:
        # Not an IP at all (e.g. the "testclient" ASGI peer, a unix socket).
        # Cannot be a trusted proxy.
        return False
    if trusted:
        return any(addr in net for net in trusted)
    return addr.is_private or addr.is_loopback


def _client_ip(request: Request, trusted: list) -> str:
    """Resolve the caller IP for bucketing, treating X-Forwarded-For as hostile.

    The previous implementation returned the **first** X-Forwarded-For entry
    unconditionally. That header is caller-supplied, so any client could rotate
    it per request and mint a fresh bucket every time -- which silently voided
    the login / signup / forgot-password limits, i.e. the only brute-force
    control in the system. The Docker entrypoint runs uvicorn with
    ``--forwarded-allow-ips '*'``, so nothing downstream re-derived it either.

    Two rules now apply:

    1. The header is read **only** when the immediate peer is a trusted proxy.
       An untrusted peer is bucketed by its socket address and its header is
       ignored entirely.
    2. When trusted, the chain is walked right-to-left and the first entry that
       is not itself a trusted proxy wins. Our proxy appends the address it
       actually saw, so the rightmost untrusted entry is the real client; the
       leftmost is whatever the caller typed.
    """
    peer = (request.client.host if request.client else "") or "unknown"
    if not _is_trusted_peer(peer, trusted):
        return peer

    fwd = request.headers.get("x-forwarded-for", "").strip()
    if not fwd:
        return peer
    for raw in reversed(fwd.split(",")):
        candidate = raw.strip()
        if not candidate:
            continue
        if _is_trusted_peer(candidate, trusted):
            # Another hop in our own proxy chain -- keep walking left.
            continue
        return candidate
    return peer


class RateLimitMiddleware(BaseHTTPMiddleware):
    """Sliding-window rate limiter scoped to specific endpoint prefixes."""

    def __init__(self, app: Any) -> None:
        super().__init__(app)
        self._buckets: Dict[Tuple[str, str], Deque[float]] = defaultdict(deque)
        self._lock = Lock()
        self._last_evict = time.monotonic()
        # Read config from the Settings singleton rather than os.getenv at
        # import time. Settings loads .env from absolute paths (see
        # app/config.py), so RATE_LIMIT_DISABLED and the per-endpoint
        # overrides resolve correctly regardless of the server's launch
        # directory, and the disabled flag gets pydantic bool coercion
        # ("1"/"true"/"True"/"yes" all work).
        self._disabled = bool(settings.RATE_LIMIT_DISABLED)
        self._trusted_proxies = _parse_trusted_proxies(
            settings.RATE_LIMIT_TRUSTED_PROXIES
        )
        self._rates: Dict[str, Tuple[int, int]] = {
            "/api/auth/login": _parse_rate(settings.RATE_LIMIT_LOGIN, (10, 60)),
            "/api/auth/signup": _parse_rate(settings.RATE_LIMIT_REGISTER, (5, 60)),
            "/api/auth/forgot-password": _parse_rate(
                settings.RATE_LIMIT_FORGOT, (3, 60)
            ),
            "/api/auth/reset-password": _parse_rate(settings.RATE_LIMIT_RESET, (5, 60)),
            "/api/mcp": _parse_rate(settings.RATE_LIMIT_MCP, (120, 60)),
            "/api/agentive": _parse_rate(settings.RATE_LIMIT_AGENTIVE, (120, 60)),
            "/api/chat": _parse_rate(settings.RATE_LIMIT_CHAT, (60, 60)),
        }
        # Method-aware rules, checked after the exact-prefix table above.
        # ``/api/public-share/*`` is entirely ``auth=False``: an anonymous POST
        # creates an entry, which runs the re-embed (a paid embedding call) and
        # the entry-save hooks, and an unbounded GET lets the share token be
        # brute-forced. Reads get a looser window than writes so that loading a
        # shared track page stays usable.
        self._method_rules: list = [
            (
                "/api/public-share",
                _WRITE_METHODS,
                "public-share:write",
                _parse_rate(settings.RATE_LIMIT_PUBLIC_SHARE_WRITE, (10, 60)),
            ),
            (
                "/api/public-share",
                _READ_METHODS,
                "public-share:read",
                _parse_rate(settings.RATE_LIMIT_PUBLIC_SHARE_READ, (120, 60)),
            ),
        ]

    def _matched_rule(
        self, path: str, method: str
    ) -> Tuple[str, Tuple[int, int]] | None:
        for prefix, rate in self._rates.items():
            if path == prefix or path.startswith(prefix + "/"):
                return prefix, rate
        for prefix, methods, bucket_key, rate in self._method_rules:
            if path == prefix or path.startswith(prefix + "/"):
                if method.upper() in methods:
                    return bucket_key, rate
        return None

    def _evict_expired_locked(self, now: float) -> None:
        """Drop buckets with no live entries. Caller must hold ``self._lock``.

        ``_buckets`` is a ``defaultdict`` keyed by (rule, ip) and nothing ever
        removed from it, so it grew without bound -- and with a spoofable IP
        (see ``_client_ip``) that growth was attacker-driven. Sweeping on a
        cadence keeps it proportional to *live* clients instead of to every
        client ever seen.
        """
        if now - self._last_evict < _EVICT_INTERVAL_SECONDS:
            return
        self._last_evict = now
        longest = max((secs for _, secs in self._rates.values()), default=60)
        for _, _, _, (_, secs) in self._method_rules:
            longest = max(longest, secs)
        cutoff = now - longest
        stale = [
            key
            for key, bucket in self._buckets.items()
            if not bucket or bucket[-1] < cutoff
        ]
        for key in stale:
            del self._buckets[key]

    async def dispatch(
        self,
        request: Request,
        call_next: Callable[[Request], Awaitable[Response]],
    ) -> Response:
        """Apply per-IP rate limiting to matched paths and forward the request."""
        if self._disabled or request.method == "OPTIONS":
            return await call_next(request)

        rule = self._matched_rule(request.url.path, request.method)
        if rule is None:
            return await call_next(request)

        prefix, (count, seconds) = rule
        ip = _client_ip(request, self._trusted_proxies)
        now = time.monotonic()

        # Trim old entries and check capacity inside a single critical
        # section. The bucket is a sliding window over `seconds`.
        with self._lock:
            self._evict_expired_locked(now)
            bucket = self._buckets[(prefix, ip)]
            cutoff = now - seconds
            while bucket and bucket[0] < cutoff:
                bucket.popleft()
            if len(bucket) >= count:
                # Compute Retry-After from the oldest entry in window.
                oldest = bucket[0] if bucket else now
                retry_after = max(1, int(oldest + seconds - now) + 1)
                return JSONResponse(
                    status_code=429,
                    content={
                        "error_code": "RATE_LIMITED",
                        "message": (
                            "Too many requests. Please wait a moment "
                            "before trying again."
                        ),
                    },
                    headers={"Retry-After": str(retry_after)},
                )
            bucket.append(now)

        return await call_next(request)
