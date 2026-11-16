"""Link preview endpoint for body URL unfurling."""

import asyncio
import html
import re
import time
from collections import OrderedDict
from typing import Any, Dict
from urllib.parse import urlparse
from urllib.request import HTTPRedirectHandler, ProxyHandler, Request, build_opener

from fastapi import Request as FastAPIRequest
from jvspatial.api import endpoint

from app.api.errors import BadRequestError, MissingAuthenticationError
from app.api.utils import resolve_principal_id
from app.services.link_preview_transport import PublicHTTPHandler, PublicHTTPSHandler
from app.services.url_safety import (
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


# A proxy would resolve/connect outside the checked transport. This endpoint
# deliberately connects directly, independent of environment proxy settings.
_opener = build_opener(
    ProxyHandler({}), PublicHTTPHandler, PublicHTTPSHandler, _ValidatingRedirectHandler
)


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
    validate_public_http_url_sync(url)
    req = Request(
        url,
        headers={
            "User-Agent": "IntegralLinkPreview/1.0",
            "Accept": "text/html,application/xhtml+xml",
        },
    )
    with _opener.open(req, timeout=4) as response:
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
