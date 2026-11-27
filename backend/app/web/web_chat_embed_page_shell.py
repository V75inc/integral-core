"""Opt-in embed document shell. Importing this module serves nothing."""

import re
from html import escape
from pathlib import Path
from typing import Awaitable, Callable
from urllib.parse import urlsplit

from fastapi import Request
from fastapi.routing import APIRouter
from jvspatial.api.endpoints.router import BaseRouter
from starlette.responses import HTMLResponse, Response

from app.web.full_page_chat import (
    is_canonical_web_chat_origin,
    is_web_chat_route_reference,
)
from app.web.web_chat_embed_policy import (
    ManagedWebChatEmbedPolicy,
    ReadCurrentEmbedPolicy,
    check_current_web_chat_embed_policy,
)
from app.web.web_chat_page_shell import (
    StandaloneWebChatAssets,
    _load_web_chat_assets,
)

_ASSET_PATH = "/web-chat-embed-assets/assets/"
_FILE = re.compile(r"public-web-chat-embed-[A-Za-z0-9_-]{8,48}\.(js|css)")
_SEED = re.compile(r"iweb_[A-Za-z0-9_-]{86}")
_CSP = (
    "default-src 'none'; script-src 'self'; style-src 'self'; connect-src 'self'; "
    "img-src 'self' data:; font-src 'self'; base-uri 'none'; "
    "form-action 'none'; object-src 'none'; frame-ancestors "
)
_SAFE_HEADERS = {
    "Cache-Control": "no-store",
    "Referrer-Policy": "no-referrer",
    "X-Content-Type-Options": "nosniff",
    "Permissions-Policy": "camera=(), microphone=(), geolocation=()",
}
DENIED_HEADERS = {
    **_SAFE_HEADERS,
    "Content-Security-Policy": "default-src 'none'; frame-ancestors 'none'",
    "X-Frame-Options": "DENY",
}
IssueSeed = Callable[[ManagedWebChatEmbedPolicy], Awaitable[str | None]]
HostReady = Callable[[str], Awaitable[bool]]


def load_web_chat_embed_assets(directory: Path) -> StandaloneWebChatAssets:
    """Load the distinct, explicitly approved embed build only."""
    return _load_web_chat_assets(
        directory,
        manifest_name="public-web-chat-embed.html",
        asset_path=_ASSET_PATH,
        filename=_FILE,
    )


def create_web_chat_embed_page_router(
    *,
    served_origin: str,
    host_ready: HostReady,
    read_current: ReadCurrentEmbedPolicy,
    issue_seed: IssueSeed,
    assets: StandaloneWebChatAssets,
    read_theme: Callable[[ManagedWebChatEmbedPolicy], Awaitable[str]] | None = None,
) -> APIRouter:
    """Compose a detached iframe document with exact parent CSP and no cookies.

    The public iframe URL and parent origin are never bearer credentials.
    A trusted host must supply exact current policy and an admitted one-use
    seed. Navigation metadata helps deny ordinary top-level navigation but
    cannot prove a parent's identity to the server.
    """
    if (
        not is_canonical_web_chat_origin(served_origin)
        or not callable(host_ready)
        or not callable(read_current)
        or not callable(issue_seed)
        or not isinstance(assets, StandaloneWebChatAssets)
        or not _FILE.fullmatch(assets.script.filename)
        or not assets.script.filename.endswith(".js")
        or not _FILE.fullmatch(assets.stylesheet.filename)
        or not assets.stylesheet.filename.endswith(".css")
    ):
        raise ValueError("Embed requires approved HTTPS host and embed assets")

    hostname = urlsplit(served_origin).netloc
    script, stylesheet = assets.script, assets.stylesheet
    integrity_css, integrity_js = stylesheet.integrity, script.integrity
    files = {item.filename: item for item in (script, stylesheet)}

    async def host_allowed(request: Request, raw_path: str) -> bool:
        return (
            request.scope.get("scheme") == "https"
            and request.headers.getlist("host") == [hostname]
            and request.scope.get("raw_path") == raw_path.encode("ascii")
            and not request.scope.get("query_string")
            and "authorization" not in request.headers
            and "cookie" not in request.headers
            and await host_ready(served_origin) is True
        )

    async def page(route: str, request: Request) -> Response:
        try:
            path = f"/embed/{route}"
            if (
                not is_web_chat_route_reference(route)
                or request.headers.getlist("sec-fetch-dest") != ["iframe"]
                or not await host_allowed(request, path)
            ):
                return Response(status_code=404, headers=DENIED_HEADERS)
            current = await read_current(route)
            if not isinstance(
                current, ManagedWebChatEmbedPolicy
            ) or not current.matches(origin=served_origin, path=path):
                return Response(status_code=404, headers=DENIED_HEADERS)
            seed = await issue_seed(current)
            if (
                not isinstance(seed, str)
                or not _SEED.fullmatch(seed)
                or not await host_allowed(request, path)
                or not await check_current_web_chat_embed_policy(
                    expected=current,
                    served_origin=served_origin,
                    raw_path=path,
                    read_current=read_current,
                )
            ):
                return Response(status_code=404, headers=DENIED_HEADERS)
            theme = await read_theme(current) if read_theme else "system"
            theme = theme if theme in ("light", "dark") else "system"
            document = (
                '<!doctype html><html lang="en"><head><meta charset="UTF-8">'
                '<meta name="viewport" content="width=device-width, initial-scale=1">'
                "<title>Web Chat</title>"
                f'<link rel="stylesheet" href="{_ASSET_PATH}{stylesheet.filename}"'
                f' integrity="{integrity_css}" crossorigin="anonymous">'
                f'<script type="module" src="{_ASSET_PATH}{script.filename}"'
                f' integrity="{integrity_js}" crossorigin="anonymous"></script>'
                '</head><body><div id="public-web-chat-embed-root"'
                f' data-web-chat-embed-route="{escape(route)}"'
                f' data-web-chat-embed-origin="{escape(served_origin)}"'
                f' data-web-chat-embed-bootstrap-seed="{escape(seed)}"'
                ' data-web-chat-embed-title="Web Chat"'
                f' data-visitor-theme="{theme}"></div></body></html>'
            )
            return HTMLResponse(
                document,
                headers={
                    **_SAFE_HEADERS,
                    "Content-Security-Policy": _CSP + current.parent_origin,
                },
            )
        except Exception:
            return Response(status_code=503, headers=DENIED_HEADERS)

    async def asset(name: str, request: Request) -> Response:
        found = files.get(name)
        try:
            if found is not None and await host_allowed(request, _ASSET_PATH + name):
                return Response(
                    found.content, media_type=found.media_type, headers=_SAFE_HEADERS
                )
            return Response(status_code=404, headers=DENIED_HEADERS)
        except Exception:
            return Response(status_code=503, headers=DENIED_HEADERS)

    router = BaseRouter()
    router.router.redirect_slashes = False
    router.add_route("/embed/{route}", page, ["GET"], include_in_schema=False)
    router.add_route(_ASSET_PATH + "{name}", asset, ["GET"], include_in_schema=False)
    return router.router
