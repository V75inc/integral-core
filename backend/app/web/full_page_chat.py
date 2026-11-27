"""Detached same-origin HTTP adapter for an explicitly composed visitor host.

No route is registered or mounted on import. A reviewed host must supply the
exact trusted route policy, per-request controller and visitor credential port.
The authenticated Integral API is not the issuer or transport for this adapter.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Awaitable, Callable, Literal, Protocol
from urllib.parse import urlsplit

from fastapi import Request
from fastapi.routing import APIRouter
from jvspatial.api.endpoints.router import BaseRouter
from pydantic import ValidationError
from starlette.responses import JSONResponse

from app.agentive.connectors.external_chat import ExternalChatProvision
from app.agentive.services.web_chat_browser_transport import (
    BrowserWebChatMessage,
    BrowserWebChatReply,
    StartMode,
)
from app.agentive.services.web_chat_profile_inference import (
    MAX_HISTORY_CHARACTERS,
    MAX_HISTORY_EXCHANGES,
    _text,
)
from app.schemas.agentive.web_chat_http import FullPageWebChatSend, FullPageWebChatStart
from app.web.visitor_channel import accepted_visitor_slug

_API_PREFIX = "/api"
_ROUTE_PATH = "/public/web-chat"
_PATH = _API_PREFIX + _ROUTE_PATH
_COOKIE = "__Secure-integral_web_chat"
_SEGMENT = re.compile(r"[A-Za-z0-9_-]{1,128}")
_CREDENTIAL = re.compile(r"[A-Za-z0-9_-]+(?:\.[A-Za-z0-9_-]+)?")
_ORIGIN = re.compile(r"https://[a-z0-9.-]+(?::[0-9]{1,5})?")
_CONTENT_TYPE = re.compile(r"application/json(?:\s*;\s*charset=utf-8)?", re.IGNORECASE)
_MAX_BODY_BYTES = 8192
_MAX_COOKIE_AGE = 7 * 24 * 60 * 60
_HEADERS = {"Cache-Control": "no-store", "X-Content-Type-Options": "nosniff"}
_Action = Literal["session", "messages"]


@dataclass(frozen=True)
class TrustedFullPageRoute:
    """Canonical origin and exact provision resolved by trusted host code."""

    provision: ExternalChatProvision
    origin: str


@dataclass(frozen=True)
class VisitorCookieIssue:
    """Opaque credential and expiry supplied by the trusted session issuer."""

    credential: str = field(repr=False)
    max_age_seconds: int


class VisitorController(Protocol):
    """Minimal structural interface implemented by BrowserWebChatController."""

    async def start(self, mode: StartMode) -> BrowserWebChatReply:
        """Open or resume a verified visitor conversation."""
        ...

    async def send(self, event_id: str, text: str) -> BrowserWebChatReply:
        """Send or retry an exact visitor turn."""
        ...


@dataclass(frozen=True)
class BoundVisitorController:
    """Request-bound controller and optional cookie from its completed start."""

    controller: VisitorController
    take_cookie: Callable[[], VisitorCookieIssue | None]


ResolveRoute = Callable[[str], Awaitable[TrustedFullPageRoute | None]]
BindController = Callable[
    [TrustedFullPageRoute, str | None], Awaitable[BoundVisitorController]
]


class _Rejected(Exception):
    def __init__(self, status: int) -> None:
        super().__init__(status)
        self.status = status


def is_canonical_web_chat_origin(value: str) -> bool:
    """Validate the canonical HTTPS origin spelling without network access."""
    if not isinstance(value, str) or len(value) > 261 or not _ORIGIN.fullmatch(value):
        return False
    parsed = urlsplit(value)
    try:
        port = parsed.port
    except ValueError:
        return False
    hostname = parsed.hostname
    labels = hostname.split(".") if hostname else []
    return bool(
        labels
        and all(
            re.fullmatch(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?", label)
            for label in labels
        )
        and port not in (0, 443)
        and (port is None or str(port) == parsed.netloc.rsplit(":", 1)[1])
    )


def is_web_chat_route_reference(value: str) -> bool:
    """Validate an opaque, single-segment host route reference."""
    return isinstance(value, str) and bool(_SEGMENT.fullmatch(value))


def _header(request: Request, name: str) -> str | None:
    values = request.headers.getlist(name)
    if len(values) > 1:
        raise _Rejected(400)
    return values[0] if values else None


def _gate_request(request: Request, route: str, action: _Action) -> str:
    if not is_web_chat_route_reference(route):
        raise _Rejected(404)
    expected_path = f"{_PATH}/{route}/{action}"
    # Only the canonical spelling is accepted; cookie scope uses this path.
    raw_path = request.scope.get("raw_path")
    if raw_path is not None and raw_path != expected_path.encode("ascii"):
        raise _Rejected(404)
    if (
        request.scope.get("query_string")
        or _header(request, "authorization") is not None
    ):
        raise _Rejected(400)
    origin = _header(request, "origin")
    fetch_site = _header(request, "sec-fetch-site")
    if (
        origin is None
        or not is_canonical_web_chat_origin(origin)
        or request.scope.get("scheme") != "https"
        or _header(request, "host") != urlsplit(origin).netloc
        or fetch_site not in (None, "same-origin")
    ):
        raise _Rejected(403)
    return origin


def _unique_fields(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("Duplicate JSON key")
        result[key] = value
    return result


def _reject_json_constant(_value: str) -> None:
    raise ValueError("Non-JSON number")


async def _body(request: Request) -> object:
    content_type = _header(request, "content-type")
    if (
        content_type is None
        or not _CONTENT_TYPE.fullmatch(content_type)
        or _header(request, "content-encoding") is not None
    ):
        raise _Rejected(415)
    length = _header(request, "content-length")
    declared = None
    if length is not None:
        if not re.fullmatch(r"[0-9]{1,10}", length):
            raise _Rejected(400)
        declared = int(length)
        if declared > _MAX_BODY_BYTES:
            raise _Rejected(413)
    data = bytearray()
    async for chunk in request.stream():
        if len(chunk) > _MAX_BODY_BYTES - len(data):
            raise _Rejected(413)
        data.extend(chunk)
    if declared is not None and declared != len(data):
        raise _Rejected(400)
    try:
        return json.loads(
            data.decode("utf-8"),
            object_pairs_hook=_unique_fields,
            parse_constant=_reject_json_constant,
        )
    except (ValueError, RecursionError):
        raise _Rejected(400) from None


def _valid_token(value: str) -> bool:
    return 24 <= len(value) <= 256 and _CREDENTIAL.fullmatch(value) is not None


def _credential(request: Request) -> str | None:
    value = None
    for header in request.headers.getlist("cookie"):
        for item in header.split(";"):
            name, _, token = item.strip().partition("=")
            if name != _COOKIE:
                continue
            if value is not None or not _valid_token(token):
                raise _Rejected(400)
            value = token
    return value


def _reply(reply: BrowserWebChatReply, action: _Action) -> JSONResponse:
    if not isinstance(reply, BrowserWebChatReply):
        raise ValueError("Invalid visitor response")
    kind = reply.kind
    if kind in ("expired", "unavailable") or (
        kind == "pending" and action == "messages"
    ):
        return JSONResponse({"kind": kind}, headers=_HEADERS)
    if (
        kind != "ready"
        or not isinstance(reply.messages, tuple)
        or len(reply.messages) > 2 * MAX_HISTORY_EXCHANGES
    ):
        raise ValueError("Invalid visitor response")
    parts = []
    ids = set()
    length = 0
    for message in reply.messages:
        if (
            not isinstance(message, BrowserWebChatMessage)
            or message.role not in ("visitor", "assistant")
            or not isinstance(message.id, str)
            or not _SEGMENT.fullmatch(message.id)
            or message.id in ids
        ):
            raise ValueError("Invalid visitor message")
        ids.add(message.id)
        text = _text(message.text, 1000 if message.role == "visitor" else 4000)
        length += len(text)
        if length > MAX_HISTORY_CHARACTERS:
            raise ValueError("Invalid visitor history length")
        parts.append({"id": message.id, "role": message.role, "text": text})
    return JSONResponse({"kind": "ready", "messages": parts}, headers=_HEADERS)


def _attach_cookie(
    response: JSONResponse,
    route: str,
    mode: StartMode,
    previous: str | None,
    issued: VisitorCookieIssue | None,
) -> None:
    rotate_required = mode == "new" or previous is None
    if issued is None and not rotate_required:
        return
    if (
        not isinstance(issued, VisitorCookieIssue)
        or not isinstance(issued.credential, str)
        or not _valid_token(issued.credential)
        or type(issued.max_age_seconds) is not int
        or not 1 <= issued.max_age_seconds <= _MAX_COOKIE_AGE
        or (rotate_required and issued.credential == previous)
    ):
        raise ValueError("Invalid visitor cookie")
    response.set_cookie(
        _COOKIE,
        issued.credential,
        max_age=issued.max_age_seconds,
        path=f"{_PATH}/{route}",
        secure=True,
        httponly=True,
        samesite="strict",
    )


def create_full_page_web_chat_router(
    resolve_route: ResolveRoute, bind: BindController
) -> APIRouter:
    """Build a detached router. The trusted HTTPS host must explicitly mount it."""

    async def dispatch(route: str, request: Request, action: _Action) -> JSONResponse:
        try:
            origin = _gate_request(request, route, action)
            supplied = await _body(request)
            if action == "session":
                start = FullPageWebChatStart.model_validate(supplied)
            else:
                message = FullPageWebChatSend.model_validate(supplied)
                if not _SEGMENT.fullmatch(message.id):
                    raise _Rejected(400)
                try:
                    _text(message.text, 1000)
                except ValueError:
                    raise _Rejected(400) from None
            token = _credential(request)
            trusted = await resolve_route(route)
            if trusted is None:
                raise _Rejected(404)
            if (
                not isinstance(trusted, TrustedFullPageRoute)
                or not isinstance(trusted.provision, ExternalChatProvision)
                or accepted_visitor_slug(request, trusted.provision.connector_kind)
                is None
                or not is_canonical_web_chat_origin(trusted.origin)
                or trusted.origin != origin
            ):
                raise _Rejected(403)
            if action == "messages" and token is None:
                return JSONResponse({"kind": "expired"}, headers=_HEADERS)
            bound = await bind(trusted, token)
            if not isinstance(bound, BoundVisitorController):
                raise ValueError("Invalid visitor controller")
            if action == "session":
                result = await bound.controller.start(start.mode)
                response = _reply(result, action)
                if result.kind == "ready":
                    _attach_cookie(
                        response, route, start.mode, token, bound.take_cookie()
                    )
                return response
            return _reply(await bound.controller.send(message.id, message.text), action)
        except _Rejected as rejection:
            status = rejection.status
        except ValidationError:
            status = 400
        except Exception:
            # Never expose visitor text, tokens, policy or provider diagnostics.
            status = 503
        return JSONResponse(
            {"kind": "unavailable"}, status_code=status, headers=_HEADERS
        )

    async def start_session(route: str, request: Request) -> JSONResponse:
        return await dispatch(route, request, "session")

    async def send_message(route: str, request: Request) -> JSONResponse:
        return await dispatch(route, request, "messages")

    # The normal @endpoint decorator registers with the active jvspatial Server
    # immediately. Build a detached router instead: importing this host adapter
    # must not expose anonymous paths on Integral's authenticated API.
    router = BaseRouter()
    router.add_route(
        f"{_ROUTE_PATH}/{{route}}/session",
        start_session,
        ["POST"],
        include_in_schema=False,
    )
    router.add_route(
        f"{_ROUTE_PATH}/{{route}}/messages",
        send_message,
        ["POST"],
        include_in_schema=False,
    )
    # jvspatial's route builder intentionally normalizes away its /api prefix.
    detached = BaseRouter().router
    detached.redirect_slashes = False
    detached.include_router(router.router, prefix=_API_PREFIX)
    return detached
