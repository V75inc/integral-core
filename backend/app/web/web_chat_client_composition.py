"""Explicit, unmounted composition for the managed connector's client API."""

import re
import time
from typing import Awaitable, Callable, Literal

from fastapi.routing import APIRouter
from jvspatial.db.postgres import PostgresDB

from app.agentive.connectors.web_chat_turn_store import JvSpatialWebChatTurnStore
from app.agentive.services.public_web_chat import PublicWebChatScope
from app.agentive.services.web_chat_admission import VerifiedFullPageSession
from app.agentive.services.web_chat_browser_transport import (
    BrowserSessionAccess,
    BrowserVisitorSessions,
    BrowserWebChatController,
)
from app.agentive.services.web_chat_history import PostgresWebChatHistory
from app.agentive.services.web_chat_profile_inference import TextModel
from app.web.web_chat_client_grants import PostgresWebChatClientGrants
from app.web.web_chat_client_http import (
    BoundClientSession,
    IssuedClientSession,
    create_web_chat_client_router,
)
from app.web.web_chat_client_sessions import (
    PostgresWebChatClientSessions,
    WebChatClientSession,
)

Operation = Literal["read", "send"]
ClientRequestBudget = Callable[[Operation, WebChatClientSession], Awaitable[bool]]
_REVISION = re.compile(r"[1-9][0-9]{0,18}|0")


def _visitor(session: WebChatClientSession) -> VerifiedFullPageSession:
    exact = session.grant.assignment
    if (
        session.conversation.provision != exact.provision
        or not _REVISION.fullmatch(exact.publication_revision)
        or not _REVISION.fullmatch(exact.route_epoch)
        or exact.publication_revision == "0"
    ):
        raise ValueError("Invalid Web Chat client session scope")
    return VerifiedFullPageSession(
        session.conversation,
        PublicWebChatScope(
            exact.provision.workspace_id,
            session.visitor_id,
            exact.profile_key,
            int(exact.publication_revision),
            int(exact.route_epoch),
        ),
    )


def compose_web_chat_client_api(
    *,
    origin: str,
    database: PostgresDB,
    grants: PostgresWebChatClientGrants,
    sessions: PostgresWebChatClientSessions,
    request_budget: ClientRequestBudget,
    complete: TextModel,
) -> APIRouter:
    """Use the existing text-only turn pipeline with no browser or staff fallback.

    The approved host supplies exact grant authority, a dedicated public
    database/model lane and durable client-level rate admission. The caller
    must compose the session store with the same grant authority. Never mount
    these routes implicitly with Core.
    """
    if (
        not isinstance(database, PostgresDB)
        or not isinstance(grants, PostgresWebChatClientGrants)
        or not isinstance(sessions, PostgresWebChatClientSessions)
        or not callable(request_budget)
        or not callable(complete)
    ):
        raise TypeError("Web Chat client API requires explicit trusted composition")
    store = JvSpatialWebChatTurnStore(database=database)

    async def start(grant_bearer: str) -> IssuedClientSession | None:
        grant = await grants.current(grant_bearer)
        if grant is None:
            return None
        issued = await sessions.open(grant)
        if issued is None or issued.session.grant != grant:
            return None
        _visitor(issued.session)
        ttl = issued.session.expires_at - int(time.time())
        if ttl <= 0:
            return None

        async def current() -> bool:
            return await sessions.current(
                issued.secret
            ) == issued.session and await grants.is_current(grant)

        return IssuedClientSession(issued.secret, ttl, current)

    async def bind(session_bearer: str) -> BoundClientSession | None:
        exact = await sessions.current(session_bearer)
        if exact is None or not await grants.is_current(exact.grant):
            return None
        visitor = _visitor(exact)
        provision = visitor.conversation.provision

        async def current() -> bool:
            return await sessions.current(session_bearer) == exact

        async def read_verified() -> VerifiedFullPageSession | None:
            return visitor if await current() else None

        async def read_session() -> BrowserSessionAccess:
            result = await read_verified()
            if result is None:
                return BrowserSessionAccess("expired")
            return BrowserSessionAccess("current", result)

        async def open_session(mode: str) -> BrowserSessionAccess:
            # Reserved for verified GET history. This path cannot mint/rotate.
            if mode != "resume":
                return BrowserSessionAccess("unavailable")
            return await read_session()

        async def ready(workspace: str) -> bool:
            return (
                workspace == provision.workspace_id
                and await grants.is_current(exact.grant)
                and await current()
            )

        async def budget(operation, verified_provision, verified_session):
            if verified_provision != provision or not await current():
                return False
            if operation == "send" and verified_session == visitor:
                return await request_budget("send", exact)
            if operation == "start" and verified_session is None:
                return await request_budget("read", exact)
            return False

        history = PostgresWebChatHistory(
            database=database, read_verified_session=read_verified, readiness=ready
        )
        controller = BrowserWebChatController(
            provision,
            BrowserVisitorSessions(open=open_session, current=read_session),
            budget,
            history.ports,
            store,
            complete,
            readiness=ready,
            expose_conflict=True,
        )

        async def read():
            return await controller.start("resume")

        return BoundClientSession(read, controller.send, current)

    return create_web_chat_client_router(origin=origin, start=start, bind=bind)
