"""Guardrails for user-supplied URLs (e.g. link attachments) to reduce SSRF risk."""

import asyncio
import ipaddress
import socket
import threading
from contextlib import contextmanager
from typing import Any, Dict, Iterator, List, Optional, Tuple
from urllib.parse import urlparse

import httpx

from app.exceptions import BadRequestError

_BLOCKED_HOSTNAMES = frozenset(
    {
        "localhost",
        "127.0.0.1",
        "::1",
        "0.0.0.0",
        "metadata.google.internal",
        "metadata.goog",
    }
)


def _ip_from_sockaddr(sockaddr: object) -> Optional[str]:
    if not isinstance(sockaddr, tuple) or not sockaddr:
        return None
    first = sockaddr[0]
    if isinstance(first, str):
        return first
    return None


def _is_disallowed_ip(ip: str) -> bool:
    try:
        addr = ipaddress.ip_address(ip)
    except ValueError:
        return False
    return bool(
        addr.is_private
        or addr.is_loopback
        or addr.is_link_local
        or addr.is_multicast
        or addr.is_reserved
        or addr.is_unspecified
    )


def _resolve_host_ips(hostname: str) -> List[str]:
    """Blocking DNS + expand to IP strings (one entry per resolved address)."""
    infos: List[Tuple] = socket.getaddrinfo(
        hostname,
        None,
        type=socket.SOCK_STREAM,
    )
    ips: List[str] = []
    seen: set[str] = set()
    for info in infos:
        ip = _ip_from_sockaddr(info[4])
        if ip and ip not in seen:
            seen.add(ip)
            ips.append(ip)
    return ips


def validate_public_http_url_sync(url: str) -> None:
    """Blocking variant of :func:`validate_public_http_url`.

    Exists because redirect handling has to validate each hop from inside
    ``urllib``'s synchronous callback, where awaiting is not possible. See
    ``app/api/link_preview.py``. Prefer the async wrapper everywhere else --
    this one performs blocking DNS on the calling thread.
    """
    raw = str(url or "").strip()
    parsed = urlparse(raw)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise BadRequestError(message="Attachment URL must be a valid http/https URL")

    host = (parsed.hostname or "").strip().lower()
    if not host:
        raise BadRequestError(message="Attachment URL must include a valid host")

    if host in _BLOCKED_HOSTNAMES:
        raise BadRequestError(message="This host is not allowed for URL attachments")

    if host.endswith(".local") or host.endswith(".localhost"):
        raise BadRequestError(message="This host is not allowed for URL attachments")

    # Literal IP in URL — check before DNS
    if _is_disallowed_ip(host):
        raise BadRequestError(
            message="Attachment URL must not target local or private addresses"
        )

    try:
        ips = _resolve_host_ips(host)
    except socket.gaierror:
        raise BadRequestError(
            message="Could not resolve attachment URL host",
        ) from None
    except OSError as e:
        raise BadRequestError(
            message="Could not validate attachment URL host",
        ) from e

    if not ips:
        raise BadRequestError(message="Could not resolve attachment URL host")

    for ip in ips:
        if _is_disallowed_ip(ip):
            raise BadRequestError(
                message="Attachment URL must not resolve to local or private addresses"
            )


async def validate_public_http_url(url: str) -> None:
    """Ensure URL is http(s) and does not resolve to loopback/private/link-local IPs.

    Raises BadRequestError if the URL is unsafe or cannot be validated.

    NOTE: this validates the URL as given. It does not constrain where the
    *fetch* ends up -- an HTTP client that follows redirects can still be walked
    to an internal address, and DNS may resolve differently between this check
    and the connection (rebinding). Callers that actually fetch must re-validate
    every redirect hop; see ``app/api/link_preview.py``.
    """
    loop = asyncio.get_running_loop()
    await loop.run_in_executor(None, validate_public_http_url_sync, url)


# ---------------------------------------------------------------------------
# Outbound-fetch guardrails (F-2)
#
# ``validate_public_http_url`` above validates a URL *as given*. Anything that
# actually FETCHES needs three more things, or the check is decorative:
#
#   1. every redirect hop re-validated (the MCP SDK builds its httpx client
#      with ``follow_redirects=True`` unconditionally, so a public MCP server
#      could 302 to ``http://169.254.169.254/…`` and the body came back);
#   2. the hostname pinned to the addresses that were actually checked, so a
#      hostile resolver cannot answer public-then-private (DNS rebinding);
#   3. a port allowlist, so a "public" host cannot be walked on :22 / :6379.
#
# These helpers are the shared implementation for those three. ``link_preview``
# has an equivalent urllib-flavoured pair (``_ValidatingRedirectHandler`` /
# ``_pinned_dns``); this is the httpx/async flavour.
# ---------------------------------------------------------------------------

PUBLIC_HTTP_PORTS: frozenset = frozenset({80, 443})
MAX_OUTBOUND_REDIRECTS = 5


def outbound_allowed_ports() -> frozenset:
    """Ports an outbound fetch may target: 80/443 plus operator additions.

    ``INTEGRAL_OUTBOUND_EXTRA_PORTS`` (comma-separated) exists for
    self-hosted deployments that run an internal MCP server on a non-standard
    port; unset (the default) means 80/443 only.
    """
    import os

    raw = (os.environ.get("INTEGRAL_OUTBOUND_EXTRA_PORTS") or "").strip()
    if not raw:
        return PUBLIC_HTTP_PORTS
    extra: set = set()
    for chunk in raw.split(","):
        chunk = chunk.strip()
        if chunk.isdigit():
            extra.add(int(chunk))
    return frozenset(PUBLIC_HTTP_PORTS | extra)


def _check_port(url: str, allowed_ports: Optional[frozenset]) -> None:
    if not allowed_ports:
        return
    parsed = urlparse(str(url or "").strip())
    port = parsed.port
    if port is None:
        port = 443 if parsed.scheme == "https" else 80
    if port not in allowed_ports:
        raise BadRequestError(
            message=(
                f"Outbound requests to port {port} are not allowed "
                f"(permitted: {', '.join(str(p) for p in sorted(allowed_ports))})"
            )
        )


def validate_outbound_http_url_sync(url: str) -> None:
    """Blocking public-URL check plus the outbound port allowlist."""
    validate_public_http_url_sync(url)
    _check_port(url, outbound_allowed_ports())


async def validate_outbound_http_url(url: str) -> None:
    """Async public-URL check plus the outbound port allowlist.

    Use this (not the bare ``validate_public_http_url``) for anything the
    server fetches on a caller's behalf: MCP transports, OAuth discovery,
    token endpoints.
    """
    loop = asyncio.get_running_loop()
    await loop.run_in_executor(None, validate_outbound_http_url_sync, url)


# --- DNS pinning -----------------------------------------------------------
#
# A refcounted, per-host pin table with ONE permanent patch of
# ``socket.getaddrinfo``. link_preview's variant swaps the global function per
# fetch under a lock, which serializes every lookup in the process; that is
# fine for a cached, rate-limited preview endpoint but not for MCP sessions
# that stay open for the length of a tool call. Here, concurrent pins for
# different hosts never collide, and concurrent pins for the SAME host are
# both sets of addresses this module already validated as public — so
# whichever answers, the socket connects somewhere that was checked.

_pin_lock = threading.Lock()
_pinned_hosts: "dict[str, list]" = {}
_real_getaddrinfo = socket.getaddrinfo
_getaddrinfo_patched = False


def _pin_key(host: Any) -> Optional[str]:
    """Normalize a getaddrinfo host argument to the pin-table key.

    anyio encodes the host to **bytes** before calling ``socket.getaddrinfo``
    (``anyio/_core/_sockets.py``), so an ``isinstance(host, str)`` test is
    always False on the httpx path — the pin table was populated and then never
    consulted for any real fetch, leaving the validate→connect TOCTOU window
    (DNS rebinding to a private address) fully open despite the pin.
    """
    if isinstance(host, bytes):
        try:
            host = host.decode("ascii")
        except UnicodeDecodeError:
            return None
    return host.lower() if isinstance(host, str) else None


def _pinning_getaddrinfo(host, port, family=0, type=0, proto=0, flags=0):  # noqa: A002
    key = _pin_key(host)
    records = None
    if key is not None:
        with _pin_lock:
            stack = _pinned_hosts.get(key)
            if stack:
                records = stack[-1]
    if records:
        out = [
            (fam, socktype, prot, canon, (sa[0], port) + tuple(sa[2:]))
            for fam, socktype, prot, canon, sa in records
            if not family or fam == family
        ]
        if out:
            return out
        # Pinned host, but nothing matches the requested family: fail closed.
        # Falling through to the real resolver here would silently re-resolve
        # an address we deliberately pinned, which is the bypass this function
        # exists to prevent.
        raise socket.gaierror(
            socket.EAI_NONAME,
            f"pinned host {key!r} has no validated address for family {family}",
        )
    return _real_getaddrinfo(host, port, family, type, proto, flags)


def _install_pinning_resolver() -> None:
    global _getaddrinfo_patched
    with _pin_lock:
        if _getaddrinfo_patched:
            return
        socket.getaddrinfo = _pinning_getaddrinfo  # type: ignore[assignment]
        _getaddrinfo_patched = True


def _resolve_pinned_records(host: str) -> Optional[List[Tuple]]:
    """Resolve ``host`` and assert every returned address is public."""
    try:
        records = _real_getaddrinfo(host, None, type=socket.SOCK_STREAM)
    except OSError:
        return None
    for record in records:
        sockaddr = record[4]
        ip = _ip_from_sockaddr(sockaddr)
        if ip and _is_disallowed_ip(ip):
            raise BadRequestError(
                message="Refusing to fetch: host resolves to a private address"
            )
    return records


class DnsPinScope:
    """Holds hostname pins for the life of one outbound session.

    ``pin_public_dns`` opens one of these around a fetch. The guarded httpx
    client also pins each redirect target it is asked to follow, so a hop that
    passed validation cannot be re-resolved to a private address before the
    socket is opened.
    """

    def __init__(self) -> None:
        self._pinned: List[Tuple[str, List[Tuple]]] = []

    def pin_url(self, url: str) -> None:
        """Validate and pin the host of ``url`` (no-op when unresolvable)."""
        host = (urlparse(str(url or "")).hostname or "").lower()
        if not host or any(h == host for h, _ in self._pinned):
            return
        records = _resolve_pinned_records(host)
        if not records:
            return
        _install_pinning_resolver()
        with _pin_lock:
            _pinned_hosts.setdefault(host, []).append(records)
        self._pinned.append((host, records))

    def release(self) -> None:
        with _pin_lock:
            for host, records in self._pinned:
                stack = _pinned_hosts.get(host) or []
                for index in range(len(stack) - 1, -1, -1):
                    if stack[index] is records:
                        stack.pop(index)
                        break
                if not stack:
                    _pinned_hosts.pop(host, None)
        self._pinned.clear()


@contextmanager
def pin_public_dns(url: str) -> Iterator[DnsPinScope]:
    """Pin ``url``'s host to validated public addresses for the block."""
    scope = DnsPinScope()
    scope.pin_url(url)
    try:
        yield scope
    finally:
        scope.release()


class GuardedAsyncClient(httpx.AsyncClient):
    """``httpx.AsyncClient`` that re-validates and pins every redirect hop.

    Validating the submitted URL and then following redirects blindly is
    equivalent to not validating at all — which is exactly the shape the MCP
    transports had, because ``create_mcp_http_client`` hard-codes
    ``follow_redirects=True``.
    """

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        kwargs.setdefault("max_redirects", MAX_OUTBOUND_REDIRECTS)
        hooks = dict(kwargs.pop("event_hooks", None) or {})
        response_hooks = list(hooks.get("response") or [])
        response_hooks.append(self._guard_redirect)
        hooks["response"] = response_hooks
        kwargs["event_hooks"] = hooks
        super().__init__(*args, **kwargs)
        self._pin_scope = DnsPinScope()

    async def _guard_redirect(self, response: "httpx.Response") -> None:
        if not response.has_redirect_location:
            return
        location = response.headers.get("location") or ""
        target = str(response.request.url.join(location))
        loop = asyncio.get_running_loop()
        await loop.run_in_executor(None, validate_outbound_http_url_sync, target)
        await loop.run_in_executor(None, self._pin_scope.pin_url, target)

    async def aclose(self) -> None:
        self._pin_scope.release()
        await super().aclose()


def guarded_async_client(
    *,
    headers: Optional[Dict[str, str]] = None,
    timeout: Any = None,
    auth: Any = None,
    follow_redirects: bool = True,
    **kwargs: Any,
) -> GuardedAsyncClient:
    """Build an httpx client whose every redirect hop is re-validated."""
    if headers is not None:
        kwargs["headers"] = headers
    if timeout is not None:
        kwargs["timeout"] = timeout
    if auth is not None:
        kwargs["auth"] = auth
    kwargs["follow_redirects"] = follow_redirects
    return GuardedAsyncClient(**kwargs)
