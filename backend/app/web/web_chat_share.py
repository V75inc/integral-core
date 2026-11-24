"""Detached authenticated read of an already-issued full-page Web Chat share."""

from dataclasses import dataclass, field
from typing import Awaitable, Callable
from urllib.parse import urlsplit

from fastapi import Request
from fastapi.routing import APIRouter
from jvspatial.api.endpoints.router import BaseRouter
from pydantic import ValidationError
from starlette.responses import JSONResponse

from app.agentive.connectors.external_chat import ExternalChatProvision
from app.agentive.services.profile_management import require_profile_manager
from app.api.errors import InsufficientPermissionsError, MissingAuthenticationError
from app.api.utils import resolve_principal_id
from app.connectors.channel_share_check import bind_mounted_share_check
from app.schemas.agentive.managed_channels import (
    ApprovedFullPageShare,
    ManageChannelShareQuery,
)
from app.services.permissions import get_user_node
from app.services.scope_header import parse_scope_header
from app.web.full_page_chat import (
    is_canonical_web_chat_origin,
    is_web_chat_route_reference,
)
from app.web.full_page_chat_composition import ResolvedWebChatRoute, RouteResolver

_HEADERS = {"Cache-Control": "no-store", "X-Content-Type-Options": "nosniff"}


@dataclass(frozen=True)
class IssuedFullPageShare:
    """Trusted host's already issued reference and visitor page URL."""

    route_key: str = field(repr=False)
    full_page_url: str = field(repr=False)


ReadIssuedShare = Callable[
    [ExternalChatProvision, str, tuple[str, int]], Awaitable[IssuedFullPageShare | None]
]


class _Denied(Exception):
    def __init__(self, status: int) -> None:
        super().__init__(status)
        self.status = status


def visitor_channel_slug(request: Request, slug: str) -> str:
    """Return the channel id when it is the visitor surface, else refuse."""
    from app.web.visitor_channel import accepted_visitor_slug

    kind = accepted_visitor_slug(request, slug)
    if kind is None:
        raise _Denied(404)
    return kind


def _share_query_values(request: Request, *versions: str) -> dict[str, str | int]:
    raw = dict(request.query_params)
    if len(raw) != len(request.query_params.multi_items()) or any(
        val != val.strip() for val in raw.values()
    ):
        raise _Denied(400)
    result: dict[str, str | int] = dict(raw)
    for key in versions:
        version = raw.get(key, "")
        if (
            len(version) > 16
            or not version.isascii()
            or not version.isdecimal()
            or version.startswith("0")
        ):
            raise _Denied(400)
        result[key] = int(version)
    return result


def _query(request: Request) -> ManageChannelShareQuery:
    return ManageChannelShareQuery.model_validate(
        _share_query_values(request, "published_version")
    )


async def _manager(request: Request, workspace: str) -> str:
    principal = resolve_principal_id(request)
    if not principal:
        raise _Denied(401)
    actor = await get_user_node(principal)
    if actor is None:
        raise _Denied(401)
    headers = request.headers.getlist("X-Integral-Scope")
    selected = parse_scope_header(headers[0]) if len(headers) == 1 else None
    if selected is None or selected.get("workspace_id") != workspace:
        raise _Denied(400)
    await require_profile_manager(actor.id, workspace)
    return actor.id


def _matches(
    route: ResolvedWebChatRoute | None,
    issued: IssuedFullPageShare,
    provision: ExternalChatProvision,
    epoch: str,
    publication: tuple[str, int],
) -> bool:
    if (
        not isinstance(route, ResolvedWebChatRoute)
        or route.provision != provision
        or route.route_key != issued.route_key
        or route.route_epoch != epoch
        or route.publication != publication
        or not is_canonical_web_chat_origin(route.origin)
    ):
        return False
    hostname = urlsplit(route.origin).hostname or ""
    if (
        hostname == "localhost"
        or hostname.endswith(".localhost")
        or hostname.startswith("127.")
        or hostname == "0.0.0.0"
    ):
        return False
    return issued.full_page_url == f"{route.origin}/chat/{route.route_key}"


def create_managed_web_chat_share_router(
    *, read_issued: ReadIssuedShare, resolve_active_route: RouteResolver
) -> APIRouter:
    """Build an unmounted GET route for the standard authenticated API host.

    Pass a resolver that checks current host readiness and durable managed
    route/publication (such as WebChatRouteAuthority.resolve). The host supplies
    its standard auth middleware. Nothing issues or constructs a share here.
    """
    if not callable(read_issued) or not callable(resolve_active_route):
        raise TypeError("Managed share requires explicit trusted read ports")

    async def prove(
        request: Request, slug: str, query: ManageChannelShareQuery
    ) -> IssuedFullPageShare:
        kind = visitor_channel_slug(request, slug)
        caller = await _manager(request, query.workspace_id)
        provision = ExternalChatProvision(query.workspace_id, kind, query.provision_id)
        epoch = query.route_epoch
        publication = (query.profile_key, query.published_version)
        issued = await read_issued(provision, epoch, publication)
        if (
            not isinstance(issued, IssuedFullPageShare)
            or not is_web_chat_route_reference(issued.route_key)
            or not isinstance(issued.full_page_url, str)
        ):
            raise _Denied(404)
        route = await resolve_active_route(issued.route_key)
        if not _matches(route, issued, provision, epoch, publication):
            raise _Denied(404)
        if await read_issued(provision, epoch, publication) != issued:
            raise _Denied(404)
        if await _manager(request, query.workspace_id) != caller:
            raise _Denied(403)
        if await resolve_active_route(issued.route_key) != route:
            raise _Denied(404)
        return issued

    async def availability(
        request: Request, slug: str, query: ManageChannelShareQuery
    ) -> bool:
        await prove(request, slug, query)
        return True

    async def read_share(slug: str, request: Request) -> JSONResponse:
        status = 503
        try:
            query = _query(request)
            issued = await prove(request, slug, query)
            output = ApprovedFullPageShare(
                **query.model_dump(), slug=slug, full_page_url=issued.full_page_url
            )
            return JSONResponse(output.model_dump(), headers=_HEADERS)
        except _Denied as rejection:
            status = rejection.status
        except ValidationError:
            status = 400
        except MissingAuthenticationError:
            status = 401
        except InsufficientPermissionsError:
            status = 403
        except Exception:
            # Read ports may return private diagnostics. Never echo them.
            pass
        return JSONResponse(
            {"kind": "unavailable"}, status_code=status, headers=_HEADERS
        )

    bind_mounted_share_check(read_share, availability)
    router = BaseRouter()
    router.add_route(
        "/agentive/managed-channels/{slug}/share",
        read_share,
        ["GET"],
        include_in_schema=False,
    )
    detached = BaseRouter().router
    detached.redirect_slashes = False
    detached.include_router(router.router, prefix="/api")
    return detached
