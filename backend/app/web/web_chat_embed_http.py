"""Detached same-origin HTTP adapter for an explicitly approved embed host.

No host document, credential storage, authentication fallback or Core mount
is configured here. The document host must enforce the exact frame ancestor
in CSP: an API request's Origin authenticates no parent and is not admission.
"""

import json
import re
from dataclasses import dataclass, field
from typing import Awaitable, Callable, Literal
from urllib.parse import urlsplit

from fastapi import Request
from fastapi.routing import APIRouter
from jvspatial.api.endpoints.router import BaseRouter
from pydantic import ValidationError
from starlette.responses import JSONResponse

from app.agentive.services.web_chat_browser_transport import BrowserWebChatReply
from app.agentive.services.web_chat_profile_inference import _text
from app.schemas.agentive.web_chat_http import (
    EmbedWebChatBootstrap,
    FullPageWebChatSend,
    FullPageWebChatStart,
)
from app.web.full_page_chat import (
    _body,
    _header,
    _Rejected,
    _reply,
    is_canonical_web_chat_origin,
    is_web_chat_route_reference,
)
from app.web.web_chat_embed_policy import (
    ManagedWebChatEmbedPolicy,
    ReadCurrentEmbedPolicy,
    check_current_web_chat_embed_policy,
)

_API_PREFIX = "/api"
_ROUTE_PATH = "/public/web-chat-embed"
_PATH = _API_PREFIX + _ROUTE_PATH
_BEARER = re.compile(r"(?i:Bearer) (iwes_[A-Za-z0-9_-]{86})")
_TOKEN = re.compile(r"iwes_[A-Za-z0-9_-]{86}")
_CREDENTIAL_TEXT = re.compile(r"(?:iweb|iwes)_[A-Za-z0-9_-]{32,128}")
_MAX_SESSION_SECONDS = 300
_HEADERS = {
    "Cache-Control": "no-store",
    "X-Content-Type-Options": "nosniff",
    "Referrer-Policy": "no-referrer",
}

Current = Callable[[], Awaitable[bool]]
_Action = Literal["session", "messages"]


@dataclass(frozen=True)
class IssuedEmbedSession:
    """Private result of an atomic seed exchange for the exact live policy."""

    policy: ManagedWebChatEmbedPolicy
    credential: str = field(repr=False)
    expires_in_seconds: int
    is_current: Current


@dataclass(frozen=True)
class BoundEmbedSession:
    """Request-scoped session already verified against the exact live policy."""

    policy: ManagedWebChatEmbedPolicy
    expires_in_seconds: int
    is_current: Current
    read: Callable[[], Awaitable[BrowserWebChatReply]]
    send: Callable[[str, str], Awaitable[BrowserWebChatReply]]


ExchangeSeed = Callable[
    [ManagedWebChatEmbedPolicy, str], Awaitable[IssuedEmbedSession | None]
]
BindSession = Callable[
    [ManagedWebChatEmbedPolicy, str], Awaitable[BoundEmbedSession | None]
]


def _gate_request(request: Request, route: str, action: _Action) -> str:
    if not is_web_chat_route_reference(route) or _CREDENTIAL_TEXT.search(route):
        raise _Rejected(404)
    expected_path = f"{_PATH}/{route}/{action}".encode("ascii")
    if request.scope.get("raw_path") != expected_path:
        raise _Rejected(404)
    if request.scope.get("query_string") or "cookie" in request.headers:
        raise _Rejected(400)
    origin = _header(request, "origin")
    if (
        origin is None
        or not is_canonical_web_chat_origin(origin)
        or request.scope.get("scheme") != "https"
        or _header(request, "host") != urlsplit(origin).netloc
        or _header(request, "sec-fetch-site") not in (None, "same-origin")
    ):
        raise _Rejected(403)
    return origin


def _bearer(request: Request) -> str | None:
    supplied = _header(request, "authorization")
    if supplied is None:
        return None
    match = _BEARER.fullmatch(supplied)
    if match is None:
        raise _Rejected(401)
    return match.group(1)


def _checked_reply(reply: BrowserWebChatReply, action: _Action) -> JSONResponse:
    response = _reply(reply, action)
    # The common reply serializer validates type, size, shape and control
    # characters. Embed messages must also never contain credential-like text.
    if _CREDENTIAL_TEXT.search(response.body.decode("utf-8")):
        raise ValueError("Invalid embed response")
    response.headers.update(_HEADERS)
    return response


def _valid_ttl(ttl: int) -> bool:
    return type(ttl) is int and 1 <= ttl <= _MAX_SESSION_SECONDS


def _ready_session(credential: str, ttl: int, messages: list) -> JSONResponse:
    if (
        not isinstance(credential, str)
        or not _TOKEN.fullmatch(credential)
        or not _valid_ttl(ttl)
    ):
        raise ValueError("Invalid embed session result")
    return JSONResponse(
        {
            "kind": "ready",
            "messages": messages,
            "session_token": credential,
            "expires_in": ttl,
        },
        headers=_HEADERS,
    )


def create_web_chat_embed_router(
    *,
    resolve_policy: ReadCurrentEmbedPolicy,
    exchange: ExchangeSeed,
    bind: BindSession,
) -> APIRouter:
    """Build the detached wire surface; the host must provide all trusted ports.

    The host must consume seeds atomically and issue empty visitor sessions.
    Bound guards include holder, expiry and revocation checks. Ports own
    authorization, dedicated budgets, and reply storage outside this adapter.
    """
    if not all(callable(port) for port in (resolve_policy, exchange, bind)):
        raise TypeError("Embed Web Chat needs explicit trusted ports")

    async def dispatch(route: str, request: Request, action: _Action) -> JSONResponse:
        status = 503
        try:
            origin = _gate_request(request, route, action)
            credential = _bearer(request)
            raw = await _body(request)
            if action == "session":
                if credential is None:
                    boot = EmbedWebChatBootstrap.model_validate(raw)
                    start = None
                else:
                    start = FullPageWebChatStart.model_validate(raw)
                    boot = None
                    if start.mode != "resume":
                        # No bearer-backed rotation in this detached slice.
                        raise _Rejected(403)
            else:
                if credential is None:
                    raise _Rejected(401)
                message = FullPageWebChatSend.model_validate(raw)
                try:
                    text = _text(message.text, 1000)
                except ValueError:
                    raise _Rejected(400) from None
                if _CREDENTIAL_TEXT.search(text) or _CREDENTIAL_TEXT.search(message.id):
                    raise _Rejected(400)
                boot = None

            policy = await resolve_policy(route)
            if policy is None:
                raise _Rejected(404)
            path = f"/embed/{route}"
            if not isinstance(policy, ManagedWebChatEmbedPolicy) or not policy.matches(
                origin=origin, path=path
            ):
                raise _Rejected(403)

            async def still_current(check: Current) -> None:
                if (
                    await check() is not True
                    or not await check_current_web_chat_embed_policy(
                        expected=policy,
                        served_origin=origin,
                        raw_path=path,
                        read_current=resolve_policy,
                    )
                ):
                    raise _Rejected(401)

            if boot is not None:
                issued = await exchange(policy, boot.bootstrap_seed)
                if issued is None:
                    raise _Rejected(401)
                if (
                    not isinstance(issued, IssuedEmbedSession)
                    or issued.policy != policy
                ):
                    raise ValueError("Invalid embed exchange")
                response = _ready_session(
                    issued.credential, issued.expires_in_seconds, []
                )
                await still_current(issued.is_current)
                return response

            # All non-bootstrap calls used the dedicated session bearer above.
            if credential is None:
                raise _Rejected(401)
            session = await bind(policy, credential)
            if session is None:
                raise _Rejected(401)
            if (
                not isinstance(session, BoundEmbedSession)
                or session.policy != policy
                or not _valid_ttl(session.expires_in_seconds)
            ):
                raise ValueError("Invalid bound embed session")
            await still_current(session.is_current)
            if action == "session":
                response = _checked_reply(await session.read(), action)
                decoded = json.loads(response.body)
                if decoded["kind"] == "ready":
                    response = _ready_session(
                        credential, session.expires_in_seconds, decoded["messages"]
                    )
            else:
                response = _checked_reply(await session.send(message.id, text), action)
            await still_current(session.is_current)
            return response
        except _Rejected as error:
            status = error.status
        except ValidationError:
            status = 400
        except Exception:
            pass  # Never return credentials, policy details or user/provider text.
        return JSONResponse(
            {"kind": "expired" if status == 401 else "unavailable"},
            status_code=status,
            headers=_HEADERS,
        )

    async def start_session(route: str, request: Request) -> JSONResponse:
        return await dispatch(route, request, "session")

    async def send_message(route: str, request: Request) -> JSONResponse:
        return await dispatch(route, request, "messages")

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
    detached = BaseRouter().router
    detached.redirect_slashes = False
    detached.include_router(router.router, prefix=_API_PREFIX)
    return detached
