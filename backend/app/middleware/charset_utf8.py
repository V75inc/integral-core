"""Ensure API and text responses declare UTF-8 so clients decode JSON correctly."""

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response


class CharsetUTF8Middleware(BaseHTTPMiddleware):
    """
    Append ``charset=utf-8`` when missing on common text/JSON content types.

    JSON is UTF-8 per RFC 8259, but some stacks mishandle bodies when the charset
    parameter is omitted; declaring it avoids corrupt display (e.g. ``?`` / �).
    """

    async def dispatch(self, request: Request, call_next) -> Response:
        """Forward the request and add ``charset=utf-8`` to text/JSON responses when needed."""
        response = await call_next(request)
        ct = response.headers.get("content-type")
        if not ct or "charset" in ct.lower():
            return response
        base = ct.split(";")[0].strip().lower()
        if base in (
            "application/json",
            "application/problem+json",
            "text/html",
            "text/plain",
        ) or base.startswith("application/json"):
            response.headers["content-type"] = ct + "; charset=utf-8"
        return response
