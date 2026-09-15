"""Link preview endpoint for body URL unfurling."""

import asyncio
import html
import re
import socket
import threading
import time
from collections import OrderedDict
from contextlib import contextmanager
from typing import Any, Dict
from urllib.parse import urlparse
from urllib.request import HTTPRedirectHandler, Request, build_opener

from fastapi import Request as FastAPIRequest
from jvspatial.api import endpoint

from app.api.errors import BadRequestError, MissingAuthenticationError
from app.api.utils import resolve_principal_id
from app.services.url_safety import (
    _is_disallowed_ip,
    validate_public_http_url,
    validate_public_http_url_sync,
)

_CACHE_TTL_SECONDS = 300
# Bounded: this is a process-global keyed by caller-supplied URL, so an
# unbounded dict is a memory-growth vector on an authenticated endpoint.
_CACHE_MAX_ENTRIES = 512
_preview_cache: "OrderedDict[str, Dict[str, Any]]" = OrderedDict()


class _ValidatingRedirectHandler(HTTPRedirectHandler):
    """Re-runs the SSRF validator on every redirect target.

    ``urlopen`` follows redirects by default, so validating only the URL the
    caller supplied was not a control: a public URL could answer 302 with
    a redirect target of ``http://169.254.169.254/...`` (cloud instance
    metadata) or any internal host, and the response body came back in the
    preview's title and description. Validating the submitted URL and then
    following redirects blindly is equivalent to not validating at all.
    """

    max_redirections = 5

    def redirect_request(self, req, fp, code, msg, headers, newurl):  # noqa: D102
        # Raises BadRequestError for a non-public target, which aborts the fetch.
        validate_public_http_url_sync(newurl)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


_opener = build_opener(_ValidatingRedirectHandler)


# Serializes the pinned section below. `socket.getaddrinfo` is process-global
# and `_fetch_preview` runs under `asyncio.to_thread`, so without this two
# concurrent previews would race: one request's pin would answer the other
# request's lookups, and the `finally` restore of whichever finished first
# would unpin the one still in flight. Serializing is acceptable here — the
# endpoint is rate-limited and results are cached — and is much easier to
# reason about than a per-thread override. Revisit only if link preview
# becomes hot enough for the lock to matter.
_DNS_PIN_LOCK = threading.Lock()


@contextmanager
def _pinned_dns(url: str):
    """Pin hostname->IP for the duration of a fetch, closing the rebinding gap.

    ``validate_public_http_url`` resolves the host to decide whether it is
    public, then ``urlopen`` resolves it AGAIN when it connects. A hostile
    resolver can answer with a public address for the first lookup and a
    private one for the second (DNS rebinding), so the check and the connection
    end up pointing at different places.

    This resolves once, re-validates every address it got, and holds
    ``socket.getaddrinfo`` to that answer for the request, so the socket
    connects to an address that was actually checked.
    """
    host = (urlparse(url).hostname or "").lower()
    if not host:
        yield
        return

    real_getaddrinfo = socket.getaddrinfo
    try:
        pinned = real_getaddrinfo(host, None, type=socket.SOCK_STREAM)
    except OSError:
        yield
        return

    # Re-check the exact addresses being pinned. The earlier validation
    # resolved separately; this asserts the records we are about to force are
    # themselves public, so pinning can never lock the fetch onto a private
    # address that a racing resolver returned.
    for _fam, _st, _pr, _canon, sa in pinned:
        if sa and isinstance(sa[0], str) and _is_disallowed_ip(sa[0]):
            raise BadRequestError(
                message="Refusing to fetch: host resolves to a private address"
            )

    def _pinning_getaddrinfo(h, port, family=0, type=0, proto=0, flags=0):
        if isinstance(h, str) and h.lower() == host:
            # Re-emit the validated records with the caller's port/family.
            return [
                (fam, socktype, prot, canon, (sa[0], port) + tuple(sa[2:]))
                for fam, socktype, prot, canon, sa in pinned
                if not family or fam == family
            ] or real_getaddrinfo(h, port, family, type, proto, flags)
        return real_getaddrinfo(h, port, family, type, proto, flags)

    with _DNS_PIN_LOCK:
        socket.getaddrinfo = _pinning_getaddrinfo  # type: ignore[assignment]
        try:
            yield
        finally:
            socket.getaddrinfo = real_getaddrinfo  # type: ignore[assignment]


def _extract_meta(content: str, key: str, *, use_property: bool = True) -> str:
    attr = "property" if use_property else "name"
    pattern = (
        rf'<meta[^>]+{attr}=["\']{re.escape(key)}["\'][^>]+content=["\']([^"\']+)["\']'
    )
    m = re.search(pattern, content, flags=re.IGNORECASE)
    if m:
        return html.unescape(m.group(1).strip())
    rev_pattern = (
        rf'<meta[^>]+content=["\']([^"\']+)["\'][^>]+{attr}=["\']{re.escape(key)}["\']'
    )
    m2 = re.search(rev_pattern, content, flags=re.IGNORECASE)
    return html.unescape(m2.group(1).strip()) if m2 else ""


def _extract_title(content: str) -> str:
    m = re.search(
        r"<title[^>]*>(.*?)</title>", content, flags=re.IGNORECASE | re.DOTALL
    )
    if not m:
        return ""
    return html.unescape(re.sub(r"\s+", " ", m.group(1)).strip())


def _fetch_preview(url: str) -> Dict[str, str]:
    req = Request(
        url,
        headers={
            "User-Agent": "IntegralLinkPreview/1.0",
            "Accept": "text/html,application/xhtml+xml",
        },
    )
    with _pinned_dns(url), _opener.open(req, timeout=4) as response:
        content_type = str(response.headers.get("Content-Type") or "")
        if "text/html" not in content_type:
            return {
                "url": url,
                "title": "",
                "description": "",
                "image": "",
                "site_name": "",
            }
        raw = response.read(500_000)
    decoded = raw.decode("utf-8", errors="ignore")
    title = _extract_meta(decoded, "og:title") or _extract_title(decoded)
    description = _extract_meta(decoded, "og:description") or _extract_meta(
        decoded, "description", use_property=False
    )
    image = _extract_meta(decoded, "og:image")
    site_name = _extract_meta(decoded, "og:site_name")
    return {
        "url": url,
        "title": title,
        "description": description,
        "image": image,
        "site_name": site_name,
    }


@endpoint("/link-preview", methods=["GET"], auth=True, tags=["Entries"])
async def get_link_preview(request: FastAPIRequest, url: str) -> Dict[str, Any]:
    """Fetch normalized Open Graph metadata for a URL."""
    user_id = resolve_principal_id(request)
    if not user_id:
        raise MissingAuthenticationError(message="Authentication required")

    raw = str(url or "").strip()
    await validate_public_http_url(raw)
    normalized = urlparse(raw).geturl()
    now = time.time()
    cached = _preview_cache.get(normalized)
    if cached and now - float(cached.get("_cached_at", 0)) < _CACHE_TTL_SECONDS:
        payload = dict(cached)
        payload.pop("_cached_at", None)
        return {"preview": payload}

    try:
        # _fetch_preview is blocking (urllib + DNS + up to 4s of network). Run
        # it off the event loop so one slow or hostile host cannot stall every
        # other request on this worker.
        preview = await asyncio.to_thread(_fetch_preview, normalized)
    except BadRequestError:
        # A redirect hop failed validation — surface the SSRF refusal as-is
        # rather than flattening it into "could not fetch".
        raise
    except Exception as exc:  # pragma: no cover - network-dependent failures
        raise BadRequestError(message=f"Could not fetch link preview: {exc}")

    _preview_cache[normalized] = {**preview, "_cached_at": now}
    _preview_cache.move_to_end(normalized)
    while len(_preview_cache) > _CACHE_MAX_ENTRIES:
        _preview_cache.popitem(last=False)
    return {"preview": preview}
