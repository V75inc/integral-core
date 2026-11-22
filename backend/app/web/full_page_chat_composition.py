"""Compose the detached browser Web Chat router without serving or registering it."""

from dataclasses import dataclass
from typing import Awaitable, Callable

from fastapi.routing import APIRouter
from jvspatial.db.postgres import PostgresDB

from app.agentive.connectors.web_chat_turn_store import JvSpatialWebChatTurnStore
from app.agentive.services.web_chat_browser_transport import BrowserWebChatController
from app.agentive.services.web_chat_history import PostgresWebChatHistory
from app.agentive.services.web_chat_profile_inference import TextModel
from app.agentive.services.web_chat_request_budget import (
    PostgresWebChatRequestBudget,
    WebChatBudgetPolicy,
)
from app.agentive.services.web_chat_visitor_sessions import (
    VISITOR_SESSION_TTL_SECONDS,
    PostgresBrowserVisitorSessions,
)
from app.web.full_page_chat import (
    BoundVisitorController,
    TrustedFullPageRoute,
    VisitorCookieIssue,
    create_full_page_web_chat_router,
)


@dataclass(frozen=True)
class ResolvedWebChatRoute(TrustedFullPageRoute):
    """Server-owned mapping of the exact opaque path to current route authority."""

    route_key: str
    route_epoch: str
    publication: tuple[str, int] | None = None


RouteResolver = Callable[[str], Awaitable[ResolvedWebChatRoute | None]]
RouteReady = Callable[[ResolvedWebChatRoute], Awaitable[bool]]


def compose_full_page_web_chat(
    *,
    database: PostgresDB,
    resolve_route: RouteResolver,
    readiness: RouteReady,
    complete: TextModel,
    budget_policy: WebChatBudgetPolicy,
    budget_timeout_seconds: float,
    max_parallel_budget_acquisitions: int,
) -> APIRouter:
    """Build a detached production-shaped router, with no implicit dependencies.

    Hosts must provide trusted, current route lookup/readiness and a bounded
    tool-free completion port. This factory does not mount anything, supply a
    model/provider, or create/enable a managed connector.
    """
    if not callable(resolve_route) or not callable(readiness) or not callable(complete):
        raise TypeError("Web Chat composition requires explicit trusted ports")
    turns = JvSpatialWebChatTurnStore(database=database)
    budget = PostgresWebChatRequestBudget(
        database=database,
        policy=budget_policy,
        acquire_seconds=budget_timeout_seconds,
        max_parallel_acquisitions=max_parallel_budget_acquisitions,
    )

    async def resolve(key: str) -> ResolvedWebChatRoute | None:
        value = await resolve_route(key)
        if (
            not isinstance(value, ResolvedWebChatRoute)
            or value.route_key != key
            or not isinstance(value.route_epoch, str)
            or not value.route_epoch.isascii()
            or not value.route_epoch.isdecimal()
            or len(value.route_epoch) > 20
            or str(int(value.route_epoch)) != value.route_epoch
        ):
            return None
        return value

    async def bind(
        current: TrustedFullPageRoute, credential: str | None
    ) -> BoundVisitorController:
        if not isinstance(current, ResolvedWebChatRoute):
            raise ValueError("Missing Web Chat route revision")

        async def ready(provision, epoch) -> bool:
            if (provision, epoch) != (current.provision, current.route_epoch):
                return False
            if await resolve(current.route_key) != current:
                return False
            return (
                await readiness(current) is True
                and await resolve(current.route_key) == current
            )

        async def workspace_ready(workspace: str) -> bool:
            return workspace == current.provision.workspace_id and await ready(
                current.provision, current.route_epoch
            )

        bound = PostgresBrowserVisitorSessions(database=database, readiness=ready).bind(
            current.provision,
            current.route_epoch,
            credential,
            publication=current.publication,
        )

        async def read_session():
            access = await bound.ports.current()
            return access.session if access.kind == "current" else None

        history = PostgresWebChatHistory(
            database=database,
            read_verified_session=read_session,
            readiness=workspace_ready,
        )
        controller = BrowserWebChatController(
            current.provision,
            bound.ports,
            budget,
            history.ports,
            turns,
            complete,
            readiness=workspace_ready,
        )

        def take_cookie() -> VisitorCookieIssue | None:
            issued = bound.take_issued_credential()
            return (
                VisitorCookieIssue(issued, VISITOR_SESSION_TTL_SECONDS)
                if issued
                else None
            )

        return BoundVisitorController(controller, take_cookie)

    return create_full_page_web_chat_router(resolve, bind)
