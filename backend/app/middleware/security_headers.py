"""Production security headers — CSP, HSTS, X-Frame-Options, etc.

Headers are only added to responses (not requests). The middleware is
non-invasive: it never modifies the response body, just appends headers
that strengthen the browser's default security posture.

Set ``ENABLE_HSTS=1`` only when serving over HTTPS at a real hostname;
HSTS on http://localhost causes the browser to remember the host as
HTTPS-only and breaks dev. Defaults to off.

CSP is intentionally permissive for v1 — it allows the React bundle's
``unsafe-inline`` for style attributes (we use a lot of CSS variable
inline styles for track accent colors) and ``data:`` images for avatars
and inline SVGs. Tighten progressively after launch.
"""

from __future__ import annotations

import os

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response

# Browser-direct speech-to-text origins (space-separated CSP sources).
SPEECH_CONNECT_ORIGINS = "https://api.openai.com"

# ``microphone=(self)`` lets the chat composer's dictation button capture audio
# on our own origin; every other powerful feature stays disabled.
PERMISSIONS_POLICY = (
    "accelerometer=(), camera=(), geolocation=(), gyroscope=(), "
    "magnetometer=(), microphone=(self), payment=(), usb=()"
)


def _build_csp() -> str:
    """Return the Content-Security-Policy directive value.

    Override fully via ``CSP_OVERRIDE`` env var if a deployment needs a
    custom directive (e.g. to allow Sentry, Plausible, or a CDN origin).
    """
    override = (os.getenv("CSP_OVERRIDE") or "").strip()
    if override:
        return override

    extra_connect = (os.getenv("CSP_EXTRA_CONNECT") or "").strip()
    extra_img = (os.getenv("CSP_EXTRA_IMG") or "").strip()
    # Speech-to-text: the browser opens the realtime transcription session
    # with the provider directly (SDP exchange over fetch), authenticated by a
    # short-lived client secret the backend mints. The origin is a literal so
    # an env override can only ADD to it. Keep in sync with both nginx
    # templates — a CSP override (CSP_OVERRIDE) must carry it too.
    connect_src = f"'self' {SPEECH_CONNECT_ORIGINS}"
    if extra_connect:
        connect_src = f"{connect_src} {extra_connect}"
    img_src = "'self' data: blob:"
    if extra_img:
        img_src = f"'self' data: blob: {extra_img}"

    directives = [
        "default-src 'self'",
        "base-uri 'self'",
        "form-action 'self'",
        "frame-ancestors 'none'",
        # Inline styles needed for per-track accent color CSS vars on
        # dynamically-rendered nodes; we don't allow inline scripts.
        "style-src 'self' 'unsafe-inline'",
        "script-src 'self'",
        f"img-src {img_src}",
        "font-src 'self' data:",
        f"connect-src {connect_src}",
        "object-src 'none'",
    ]
    return "; ".join(directives)


_CSP = _build_csp()
_DOCS_PATH_PREFIXES = ("/docs", "/redoc", "/openapi.json")
_HSTS_ENABLED = os.getenv("ENABLE_HSTS", "0") == "1"
_HSTS_VALUE = "max-age=31536000; includeSubDomains"


def _is_docs_path(path: str) -> bool:
    """True for FastAPI/jvspatial Swagger / ReDoc / OpenAPI surfaces."""
    return any(path == p or path.startswith(p + "/") for p in _DOCS_PATH_PREFIXES)


class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    """Append a baseline of production security headers to every response."""

    async def dispatch(self, request: Request, call_next) -> Response:
        """Attach baseline security headers to every outbound response."""
        response = await call_next(request)
        h = response.headers
        # Each header is set unconditionally — middleware order ensures we
        # run after handlers, and any header explicitly set by a handler
        # would still be overwritten by `setdefault`-style assignment, so
        # we use direct assignment to enforce the policy.
        h["X-Content-Type-Options"] = "nosniff"
        h["X-Frame-Options"] = "DENY"
        h["Referrer-Policy"] = "strict-origin-when-cross-origin"
        h["Permissions-Policy"] = PERMISSIONS_POLICY
        # Cross-origin isolation. COEP omitted — would break inline images.
        h["Cross-Origin-Opener-Policy"] = "same-origin"
        h["Cross-Origin-Resource-Policy"] = "same-site"
        # CSP — the heavyweight one. Apps can override via env.
        # Skip overwriting on docs paths so the relaxed CSP that
        # jvspatial's SecurityHeadersMiddleware sets there (permits the
        # Swagger UI / ReDoc CDN bundles) stays. Production lockdown:
        # set JVSPATIAL_DOCS_DISABLED=1 to unpublish the docs surface
        # entirely (routes return 404, this middleware sees no docs
        # paths). For app routes we own the policy because Integral's
        # CSP needs `unsafe-inline` styles + `data:` images that
        # jvspatial's stricter default does not permit.
        if not _is_docs_path(request.url.path):
            h["Content-Security-Policy"] = _CSP
        if _HSTS_ENABLED:
            h["Strict-Transport-Security"] = _HSTS_VALUE
        return response
