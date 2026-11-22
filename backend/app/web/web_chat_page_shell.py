"""Detached standalone Web Chat HTML and a fixed built asset allowlist."""

import base64
import hashlib
import re
from dataclasses import dataclass
from html import escape
from html.parser import HTMLParser
from itertools import islice
from pathlib import Path
from typing import Awaitable, Callable
from unicodedata import category
from urllib.parse import urlsplit

from fastapi import Request
from fastapi.routing import APIRouter
from jvspatial.api.endpoints.router import BaseRouter
from starlette.responses import HTMLResponse, Response

from app.web.full_page_chat import (
    is_canonical_web_chat_origin,
    is_web_chat_route_reference,
)
from app.web.web_chat_host_assembly import WebChatPageContext

_ASSET_PATH = "/web-chat-assets/assets/"
_FILE = re.compile(r"public-web-chat-[A-Za-z0-9_-]{8,48}\.(js|css)")
_MAX_BUNDLE = 8 * 1024 * 1024
_CSP = (
    "default-src 'none'; script-src 'self'; style-src 'self'; connect-src 'self'; "
    "img-src 'self' data:; font-src 'self'; base-uri 'none'; frame-ancestors 'none'; "
    "form-action 'none'; object-src 'none'"
)
_HEADERS = {
    "Cache-Control": "no-store",
    "Referrer-Policy": "no-referrer",
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Permissions-Policy": "camera=(), microphone=(), geolocation=()",
    "Content-Security-Policy": _CSP,
}
PageContextReader = Callable[[str, str], Awaitable[WebChatPageContext | None]]
HostReady = Callable[[str], Awaitable[bool]]


def _file(root: Path, path: Path, limit: int) -> bytes:
    if path.resolve() != path or not path.is_relative_to(root) or not path.is_file():
        raise ValueError("Invalid standalone Web Chat artifact")
    with path.open("rb") as stream:
        content = stream.read(limit + 1)
    if not content or len(content) > limit:
        raise ValueError("Invalid standalone Web Chat artifact size")
    return content


class _ViteEntries(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.entries: dict[str, str] = {}

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        """Read only the standalone build's external module and stylesheet paths."""
        attributes = dict(attrs)
        if tag == "script":
            if attributes.get("type") != "module":
                raise ValueError("Expected standalone Web Chat module")
            source = attributes.get("src")
            extension = "js"
        elif tag == "link":
            if attributes.get("rel") != "stylesheet":
                raise ValueError("Expected standalone Web Chat stylesheet")
            source = attributes.get("href")
            extension = "css"
        else:
            return
        if (
            len(attrs) != len(attributes)
            or source is None
            or not source.startswith(_ASSET_PATH)
            or extension in self.entries
        ):
            raise ValueError("Invalid standalone Web Chat entries")
        name = source.removeprefix(_ASSET_PATH)
        match = _FILE.fullmatch(name)
        if not match or match.group(1) != extension:
            raise ValueError("Invalid standalone Web Chat asset name")
        self.entries[extension] = name


@dataclass(frozen=True)
class _Asset:
    filename: str
    content: bytes
    media_type: str

    @property
    def integrity(self) -> str:
        """Compute subresource integrity for the exact approved local bytes."""
        digest = hashlib.sha384(self.content).digest()
        return "sha384-" + base64.b64encode(digest).decode("ascii")


@dataclass(frozen=True)
class StandaloneWebChatAssets:
    """Immutable deployment input read only from an approved build directory."""

    script: _Asset
    stylesheet: _Asset

    @classmethod
    def from_vite_build(cls, directory: Path) -> "StandaloneWebChatAssets":
        """Load precisely the standard standalone Vite module and stylesheet."""
        root = directory.absolute()
        if root.resolve() != root:
            raise ValueError("Expected a canonical trusted Web Chat build directory")
        manifest = _file(root, root / "public-web-chat.html", 16 * 1024)
        entries = _ViteEntries()
        entries.feed(manifest.decode("utf-8"))
        entries.close()
        if set(entries.entries) != {"js", "css"}:
            raise ValueError("Incomplete standalone Web Chat build")
        script, stylesheet = entries.entries["js"], entries.entries["css"]
        asset_dir = root / "assets"
        if asset_dir.resolve() != asset_dir or {
            child.name for child in islice(asset_dir.iterdir(), 3)
        } != {script, stylesheet}:
            raise ValueError("Unexpected standalone Web Chat asset inventory")
        return cls(
            script=_Asset(
                script,
                _file(root, root / "assets" / script, _MAX_BUNDLE),
                "text/javascript",
            ),
            stylesheet=_Asset(
                stylesheet,
                _file(root, root / "assets" / stylesheet, _MAX_BUNDLE),
                "text/css",
            ),
        )


def create_web_chat_page_shell_router(
    *,
    served_origin: str,
    host_ready: HostReady,
    page_context: PageContextReader,
    assets: StandaloneWebChatAssets,
) -> APIRouter:
    """Build an unmounted document/asset router for one approved HTTPS origin."""
    if (
        not is_canonical_web_chat_origin(served_origin)
        or not callable(host_ready)
        or not callable(page_context)
        or not isinstance(assets, StandaloneWebChatAssets)
        or any(
            not _FILE.fullmatch(asset.filename)
            for asset in (assets.script, assets.stylesheet)
        )
    ):
        raise ValueError("Web Chat page requires explicit approved host and assets")
    hostname = urlsplit(served_origin).netloc
    stylesheet_integrity = assets.stylesheet.integrity
    script_integrity = assets.script.integrity

    async def ready(request: Request, path: str) -> bool:
        return (
            request.scope.get("scheme") == "https"
            and request.headers.getlist("host") == [hostname]
            and not request.scope.get("query_string")
            and request.scope.get("raw_path", b"") == path.encode("ascii")
            and await host_ready(served_origin) is True
        )

    async def page(route: str, request: Request) -> Response:
        try:
            if not is_web_chat_route_reference(route) or not await ready(
                request, f"/chat/{route}"
            ):
                return Response(status_code=404, headers=_HEADERS)
            context = await page_context(route, served_origin)
            if (
                not isinstance(context, WebChatPageContext)
                or context.route != route
                or not isinstance(context.title, str)
                or not context.title.strip()
                or len(context.title) > 120
                or any(category(ch) in {"Cc", "Cs", "Zl", "Zp"} for ch in context.title)
                or not await ready(request, f"/chat/{route}")
                or await page_context(route, served_origin) != context
            ):
                return Response(status_code=404, headers=_HEADERS)
            title, route_attr = escape(context.title), escape(route)
            document = (
                '<!doctype html><html lang="en"><head><meta charset="UTF-8">'
                '<meta name="viewport" content="width=device-width, initial-scale=1">'
                f"<title>{title}</title>"
                f'<link rel="stylesheet" href="{_ASSET_PATH}{assets.stylesheet.filename}"'
                f' integrity="{stylesheet_integrity}" crossorigin="anonymous">'
                f'<script type="module" src="{_ASSET_PATH}{assets.script.filename}"'
                f' integrity="{script_integrity}" crossorigin="anonymous"></script>'
                '</head><body><div id="public-web-chat-root"'
                f' data-web-chat-route="{route_attr}" data-web-chat-title="{title}">'
                "</div></body></html>"
            )
            return HTMLResponse(document, headers=_HEADERS)
        except Exception:
            return Response(status_code=503, headers=_HEADERS)

    async def asset(name: str, request: Request) -> Response:
        found = files.get(name)
        if found is None:
            return Response(status_code=404, headers=_HEADERS)
        try:
            if not await ready(request, _ASSET_PATH + name):
                return Response(status_code=404, headers=_HEADERS)
            return Response(
                found.content, media_type=found.media_type, headers=_HEADERS
            )
        except Exception:
            return Response(status_code=503, headers=_HEADERS)

    files = {item.filename: item for item in (assets.script, assets.stylesheet)}
    router = BaseRouter()
    router.router.redirect_slashes = False
    router.add_route("/chat/{route}", page, ["GET"], include_in_schema=False)
    router.add_route(_ASSET_PATH + "{name}", asset, ["GET"], include_in_schema=False)
    return router.router
