"""Detached, bearer-only Web Chat HTTP contract for trusted server clients.

This adapter is not a credential store, an authorizer, or a Core startup mount.
"""

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
from app.schemas.agentive.web_chat_http import FullPageWebChatSend
from app.web.full_page_chat import (
    _body,
    _header,
    _Rejected,
    _reply,
    is_canonical_web_chat_origin,
)

_PREFIX = "/api"
_ROOT = "/v1/web-chat"
_HEADERS = {"Cache-Control": "no-store", "X-Content-Type-Options": "nosniff"}
_BEARER = re.compile(r"(?i:Bearer) (iwc[gs]_[A-Za-z0-9_-]{43})")
_SESSION_TOKEN = re.compile(r"iwcs_[A-Za-z0-9_-]{43}")
_MAX_TTL_SECONDS = 15 * 60
_Action = Literal["start", "read", "send"]
Current = Callable[[], Awaitable[bool]]


@dataclass(frozen=True)
class IssuedClientSession:
    """Private result of authorized, budgeted issuance for one current grant."""

    credential: str = field(repr=False)
    expires_in_seconds: int
    is_current: Current


@dataclass(frozen=True)
class BoundClientSession:
    """One instance bound to a verified credential and its exact publication.

    The backing service must check the active grant, publication, and session
    at the relevant operations and account for its public traffic separately.
    No selected or ambient workspace can become the bound identity.
    """

    read: Callable[[], Awaitable[BrowserWebChatReply]]
    send: Callable[[str, str], Awaitable[BrowserWebChatReply]]
    is_current: Current


def create_web_chat_client_router(
    *,
    origin: str,
    start: Callable[[str], Awaitable[IssuedClientSession | None]],
    bind: Callable[[str], Awaitable[BoundClientSession | None]],
) -> APIRouter:
    """Build only the transport. The host owns admission and must opt in."""
    if not is_canonical_web_chat_origin(origin):
        raise ValueError("Web Chat client API requires a canonical HTTPS origin")
    if not callable(start) or not callable(bind):
        raise TypeError("Web Chat client API requires explicit credential ports")
    authority = urlsplit(origin).netloc
    paths = {
        "start": f"{_ROOT}/sessions",
        "read": f"{_ROOT}/session",
        "send": f"{_ROOT}/session/messages",
    }

    async def dispatch(request: Request, action: _Action) -> JSONResponse:
        status = 503
        try:
            raw = request.scope.get("raw_path")
            if raw is not None and raw != (_PREFIX + paths[action]).encode("ascii"):
                raise _Rejected(404)
            if (
                request.scope.get("scheme") != "https"
                or _header(request, "host") != authority
                or any(
                    name in request.headers
                    for name in ("origin", "cookie", "sec-fetch-site")
                )
            ):
                raise _Rejected(403)
            if request.scope.get("query_string"):
                raise _Rejected(400)
            header = _header(request, "authorization")
            match = _BEARER.fullmatch(header) if header and len(header) < 60 else None
            prefix = "iwcg_" if action == "start" else "iwcs_"
            if match is None or not match[1].startswith(prefix):
                raise _Rejected(401)
            credential = match[1]
            if action == "send":
                message = FullPageWebChatSend.model_validate(await _body(request))
                try:
                    _text(message.text, 1000)
                except ValueError:
                    raise _Rejected(400) from None
            else:
                if _header(request, "content-length") not in (None, "0"):
                    raise _Rejected(400)
                # Do not buffer or accept an undeclared streaming body.
                async for chunk in request.stream():
                    if chunk:
                        raise _Rejected(400)
            if action == "start":
                ticket = await start(credential)
                if ticket is None:
                    raise _Rejected(401)
                if (
                    not isinstance(ticket, IssuedClientSession)
                    or not isinstance(ticket.credential, str)
                    or not _SESSION_TOKEN.fullmatch(ticket.credential)
                    or type(ticket.expires_in_seconds) is not int
                    or not 1 <= ticket.expires_in_seconds <= _MAX_TTL_SECONDS
                ):
                    raise _Rejected(503)
                if await ticket.is_current() is not True:
                    raise _Rejected(401)
                return JSONResponse(
                    {
                        "session_token": ticket.credential,
                        "expires_in": ticket.expires_in_seconds,
                        "messages": [],
                    },
                    status_code=201,
                    headers=_HEADERS,
                )
            session = await bind(credential)
            if not isinstance(session, BoundClientSession):
                raise _Rejected(401)
            if await session.is_current() is not True:
                raise _Rejected(401)
            if action == "read":
                response = _reply(await session.read(), "session")
            else:
                response = _reply(
                    await session.send(message.id, message.text), "messages"
                )
            # The transport's last server-side guard protects both returned
            # history and current-turn text. Already sent bytes cannot be recalled.
            if await session.is_current() is not True:
                raise _Rejected(401)
            return response
        except _Rejected as error:
            status = error.status
        except ValidationError:
            status = 400
        except Exception:
            pass  # Never return credentials, scope, text or provider diagnostics.
        headers = (
            {**_HEADERS, "WWW-Authenticate": "Bearer"} if status == 401 else _HEADERS
        )
        return JSONResponse(
            {"kind": "unavailable"}, status_code=status, headers=headers
        )

    async def start_session(request: Request) -> JSONResponse:
        return await dispatch(request, "start")

    async def read_session(request: Request) -> JSONResponse:
        return await dispatch(request, "read")

    async def send_message(request: Request) -> JSONResponse:
        return await dispatch(request, "send")

    router = BaseRouter()
    router.add_route(paths["start"], start_session, ["POST"], include_in_schema=False)
    router.add_route(paths["read"], read_session, ["GET"], include_in_schema=False)
    router.add_route(paths["send"], send_message, ["POST"], include_in_schema=False)
    detached = BaseRouter().router
    detached.redirect_slashes = False
    detached.include_router(router.router, prefix=_PREFIX)
    return detached
