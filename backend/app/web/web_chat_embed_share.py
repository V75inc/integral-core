"""Detached authenticated read of an already approved Web Chat embed share."""

from dataclasses import dataclass, field
from typing import Awaitable, Callable

from fastapi import Request
from fastapi.routing import APIRouter
from jvspatial.api.endpoints.router import BaseRouter
from pydantic import ValidationError
from starlette.responses import JSONResponse

from app.agentive.connectors.external_chat import ExternalChatProvision
from app.agentive.services.web_chat_package import SLUG
from app.api.errors import InsufficientPermissionsError, MissingAuthenticationError
from app.schemas.agentive.managed_channels import (
    ApprovedWebChatEmbedShare,
    ManageChannelEmbedShareQuery,
)
from app.web.web_chat_embed_policy import (
    ManagedWebChatEmbedPolicy,
    ReadCurrentEmbedPolicy,
    check_current_web_chat_embed_policy,
    is_canonical_embed_origin,
)
from app.web.web_chat_share import _HEADERS, _Denied, _manager, _share_query_values


@dataclass(frozen=True)
class IssuedWebChatEmbedShare:
    """Host-confirmed already-issued share, never a session credential."""

    policy: ManagedWebChatEmbedPolicy
    url: str = field(repr=False)


ReadIssuedEmbedShare = Callable[
    [ExternalChatProvision, str, tuple[str, int], int, str],
    Awaitable[IssuedWebChatEmbedShare | None],
]


def _matches(
    issued: IssuedWebChatEmbedShare | None,
    provision: ExternalChatProvision,
    query: ManageChannelEmbedShareQuery,
) -> bool:
    if not isinstance(issued, IssuedWebChatEmbedShare) or not isinstance(
        issued.policy, ManagedWebChatEmbedPolicy
    ):
        return False
    policy = issued.policy
    return (
        policy.valid()
        and policy.provision == provision
        and policy.route_epoch == query.route_epoch
        and policy.profile_key == query.profile_key
        and policy.published_version == query.published_version
        and policy.policy_revision == query.embed_policy_revision
        and policy.parent_origin == query.parent_origin
        and issued.url == f"{policy.embed_origin}/embed/{policy.embed_reference}"
    )


def create_managed_web_chat_embed_share_router(
    *, read_issued: ReadIssuedEmbedShare, read_current: ReadCurrentEmbedPolicy
) -> APIRouter:
    """Build an unmounted route; its API host must supply standard authentication.

    Both read ports are explicit. The current policy port must verify independent
    host and embed transport readiness, active exact assignment, publication
    and current parent-origin policy, not just persisted link existence.
    """
    if not callable(read_issued) or not callable(read_current):
        raise TypeError("Embed share requires explicit trusted read ports")

    async def read_share(slug: str, request: Request) -> JSONResponse:
        status = 503
        try:
            if slug != SLUG:
                raise _Denied(404)
            query = ManageChannelEmbedShareQuery.model_validate(
                _share_query_values(
                    request, "published_version", "embed_policy_revision"
                )
            )
            caller = await _manager(request, query.workspace_id)
            if not is_canonical_embed_origin(query.parent_origin):
                raise _Denied(400)
            provision = ExternalChatProvision(
                query.workspace_id, SLUG, query.provision_id
            )
            args = (
                provision,
                query.route_epoch,
                (query.profile_key, query.published_version),
                query.embed_policy_revision,
                query.parent_origin,
            )
            issued = await read_issued(*args)
            if issued is None or not _matches(issued, provision, query):
                raise _Denied(404)
            policy = issued.policy

            async def active() -> bool:
                return await check_current_web_chat_embed_policy(
                    expected=policy,
                    served_origin=policy.embed_origin,
                    raw_path=f"/embed/{policy.embed_reference}",
                    read_current=read_current,
                )

            if not await active() or await read_issued(*args) != issued:
                raise _Denied(404)
            if await _manager(request, query.workspace_id) != caller:
                raise _Denied(403)
            if not await active():
                raise _Denied(404)
            response = ApprovedWebChatEmbedShare(
                **query.model_dump(), embed_page_url=issued.url
            )
            return JSONResponse(response.model_dump(), headers=_HEADERS)
        except _Denied as denial:
            status = denial.status
        except ValidationError:
            status = 400
        except MissingAuthenticationError:
            status = 401
        except InsufficientPermissionsError:
            status = 403
        except Exception:
            # Do not echo policy identifiers, URL or private diagnostics.
            pass
        return JSONResponse(
            {"kind": "unavailable"}, status_code=status, headers=_HEADERS
        )

    router = BaseRouter()
    router.add_route(
        "/agentive/managed-channels/{slug}/embed-share",
        read_share,
        ["GET"],
        include_in_schema=False,
    )
    detached = BaseRouter().router
    detached.redirect_slashes = False
    detached.include_router(router.router, prefix="/api")
    return detached
