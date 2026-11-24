"""Explicit unmounted manager-only, stateless Web Chat test surface."""

import asyncio
from dataclasses import dataclass, field
from typing import Awaitable, Callable

from fastapi import Request
from fastapi.routing import APIRouter
from jvspatial.api.endpoints.router import BaseRouter
from pydantic import ValidationError
from starlette.responses import JSONResponse

from app.agentive.connectors.external_chat import ExternalChatProvision
from app.agentive.services import profile_management
from app.api.errors import InsufficientPermissionsError, MissingAuthenticationError
from app.connectors.channel_share_check import bind_mounted_private_test
from app.schemas.agentive.managed_channels import (
    ManageChannelShareQuery,
    PrivateWebChatTestReply,
    PrivateWebChatTestTurn,
)
from app.web.full_page_chat import _body as read_bounded_json
from app.web.full_page_chat import _Rejected as BodyRejected
from app.web.web_chat_host_assembly import managed_route_from_record
from app.web.web_chat_share import _HEADERS, _Denied, _manager, visitor_channel_slug

_TIMEOUT_SECONDS = 15


@dataclass(frozen=True)
class PrivateWebChatTestScope:
    """Private host budget/readiness input, never passed to the text model."""

    actor_id: str = field(repr=False)
    provision: ExternalChatProvision
    epoch: str
    profile_key: str
    published_version: int


@dataclass(frozen=True)
class PrivateWebChatTestInput:
    """No provider, IDs, tools, sessions or authority in the model input."""

    persona: str = field(repr=False)
    text: str = field(repr=False)


TestPlanPort = Callable[[PrivateWebChatTestScope], Awaitable[bool]]
TestTextPort = Callable[[PrivateWebChatTestInput], Awaitable[str]]


@dataclass(frozen=True)
class WebChatPrivateTestConfiguration:
    """Supply private text capacity, scoped readiness and a bounded shared quota."""

    ready: TestPlanPort
    take_budget: TestPlanPort
    complete: TestTextPort


def _plain(value: str, limit: int, *, empty: bool = False) -> bool:
    return (
        isinstance(value, str)
        and len(value) <= limit
        and (empty or bool(value.strip()))
        and all(
            ch in "\r\n\t" or (ch >= " " and not "\ud800" <= ch <= "\udfff")
            for ch in value
        )
    )


async def _current(scope: PrivateWebChatTestScope):
    workspace = scope.provision.workspace_id
    record = await profile_management.record(workspace)
    route = managed_route_from_record(scope.provision, record)
    if (
        record is None
        or route is None
        or (route.epoch, route.profile_key, route.published_version)
        != (scope.epoch, scope.profile_key, scope.published_version)
    ):
        return None
    identity = profile_management.current_public_profile_service_identity(
        record, scope.profile_key, scope.published_version
    )
    if identity is None or (
        identity.workspace_id,
        identity.profile_key,
        identity.published_version,
    ) != (workspace, scope.profile_key, scope.published_version):
        return None
    persona = record.profiles[scope.profile_key]["published"].get("persona", "")
    if not _plain(persona, 3000, empty=True):
        return None
    return route, identity, persona


def create_web_chat_private_test_router(
    config: WebChatPrivateTestConfiguration,
) -> APIRouter:
    """Construct only an optional authenticated staff route; do not mount or call it."""
    if not isinstance(config, WebChatPrivateTestConfiguration) or not all(
        callable(port) for port in (config.ready, config.take_budget, config.complete)
    ):
        raise TypeError("Private Web Chat Test requires explicitly approved ports")

    async def scope_for(
        request: Request, slug: str, query: ManageChannelShareQuery
    ) -> PrivateWebChatTestScope:
        kind = visitor_channel_slug(request, slug)
        actor = await _manager(request, query.workspace_id)
        return PrivateWebChatTestScope(
            actor,
            ExternalChatProvision(query.workspace_id, kind, query.provision_id),
            query.route_epoch,
            query.profile_key,
            query.published_version,
        )

    async def verified(request: Request, scope: PrivateWebChatTestScope, initial=None):
        before = await _current(scope)
        if before is None or (initial is not None and before != initial):
            raise _Denied(404)
        if await config.ready(scope) is not True:
            raise _Denied(404)
        if await _manager(request, scope.provision.workspace_id) != scope.actor_id:
            raise _Denied(403)
        if await _current(scope) != before:
            raise _Denied(404)
        return before

    async def available(
        request: Request, slug: str, exact: ManageChannelShareQuery
    ) -> bool:
        async with asyncio.timeout(1):
            scope = await scope_for(request, slug, exact)
            await verified(request, scope)
            return True

    async def send_test(slug: str, request: Request) -> JSONResponse:
        status = 503
        try:
            async with asyncio.timeout(_TIMEOUT_SECONDS) as deadline:
                params = request.query_params.multi_items()
                if (
                    len(params) != 1
                    or params[0][0] != "workspace_id"
                    or not params[0][1]
                ):
                    raise _Denied(400)
                workspace = params[0][1]
                # Reject unauthorized callers before consuming their body.
                await _manager(request, workspace)
                body = PrivateWebChatTestTurn.model_validate(
                    await read_bounded_json(request)
                )
                if not _plain(body.text, 1000):
                    raise _Denied(400)
                query = ManageChannelShareQuery(
                    workspace_id=workspace, **body.model_dump(exclude={"text"})
                )
                scope = await scope_for(request, slug, query)
                before = await verified(request, scope)
                if await config.take_budget(scope) is not True:
                    raise _Denied(429)
                await verified(request, scope, initial=before)
                reply = await config.complete(
                    PrivateWebChatTestInput(before[2], body.text.strip())
                )
                if deadline.expired():
                    raise TimeoutError
                if not _plain(reply, 4000):
                    raise ValueError("Invalid private Web Chat result")
                await verified(request, scope, initial=before)
                if deadline.expired():
                    raise TimeoutError
                output = PrivateWebChatTestReply(
                    **query.model_dump(), slug=slug, reply=reply.strip()
                )
                return JSONResponse(output.model_dump(), headers=_HEADERS)
        except (_Denied, BodyRejected) as failure:
            status = failure.status
        except ValidationError:
            status = 400
        except MissingAuthenticationError:
            status = 401
        except InsufficientPermissionsError:
            status = 403
        except Exception:
            # Never disclose prompts, infrastructure diagnostics or identities.
            pass
        return JSONResponse(
            {"kind": "unavailable"}, status_code=status, headers=_HEADERS
        )

    bind_mounted_private_test(send_test, available)
    router = BaseRouter()
    router.add_route(
        "/agentive/managed-channels/{slug}/test",
        send_test,
        ["POST"],
        include_in_schema=False,
    )
    detached = BaseRouter().router
    detached.redirect_slashes = False
    detached.include_router(router.router, prefix="/api")
    return detached
