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
from app.connectors.channel_share_check import (
    MAX_EMBED_DESCRIPTORS,
    ApprovedEmbedDescriptor,
    bind_mounted_embed_discovery,
)
from app.schemas.agentive.managed_channels import (
    ApprovedWebChatEmbedShare,
    ManageChannelEmbedShareQuery,
    ManageChannelShareQuery,
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


ReadApprovedEmbedDescriptors = Callable[
    [ExternalChatProvision, str, tuple[str, int]],
    Awaitable[tuple[ApprovedEmbedDescriptor, ...]],
]


def create_managed_web_chat_embed_share_router(
    *,
    read_issued: ReadIssuedEmbedShare,
    read_current: ReadCurrentEmbedPolicy,
    read_approved_descriptors: ReadApprovedEmbedDescriptors | None = None,
) -> APIRouter:
    """Build an unmounted route; its API host must supply standard authentication.

    Both read ports are explicit. The current policy port must verify independent
    host and embed transport readiness, active exact assignment, publication
    and current parent-origin policy, not just persisted link existence.
    """
    if not callable(read_issued) or not callable(read_current):
        raise TypeError("Embed share requires explicit trusted read ports")

    if read_approved_descriptors is not None and not callable(
        read_approved_descriptors
    ):
        raise TypeError("Embed share discovery requires an explicit trusted read port")

    async def prove(
        request: Request, query: ManageChannelEmbedShareQuery
    ) -> IssuedWebChatEmbedShare:
        caller = await _manager(request, query.workspace_id)
        if not is_canonical_embed_origin(query.parent_origin):
            raise _Denied(400)
        provision = ExternalChatProvision(query.workspace_id, SLUG, query.provision_id)
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
        return issued

    async def discover(
        request: Request, slug: str, exact: ManageChannelShareQuery
    ) -> tuple[ApprovedEmbedDescriptor, ...]:
        if slug != SLUG or read_approved_descriptors is None:
            return ()
        caller = await _manager(request, exact.workspace_id)
        provision = ExternalChatProvision(exact.workspace_id, SLUG, exact.provision_id)
        key = (
            provision,
            exact.route_epoch,
            (exact.profile_key, exact.published_version),
        )
        candidates = await read_approved_descriptors(*key)
        if (
            not isinstance(candidates, tuple)
            or len(candidates) > MAX_EMBED_DESCRIPTORS
            or not all(
                isinstance(c, ApprovedEmbedDescriptor)
                and is_canonical_embed_origin(c.parent_origin)
                and type(c.embed_policy_revision) is int
                and 0 < c.embed_policy_revision <= 9007199254740991
                for c in candidates
            )
            or len({c.parent_origin for c in candidates}) != len(candidates)
        ):
            return ()
        found = []
        for candidate in candidates:
            query = ManageChannelEmbedShareQuery(
                **exact.model_dump(),
                parent_origin=candidate.parent_origin,
                embed_policy_revision=candidate.embed_policy_revision,
            )
            try:
                await prove(request, query)
            except _Denied as denial:
                if denial.status == 404:
                    continue
                raise
            found.append(candidate)
        if (
            await read_approved_descriptors(*key) != candidates
            or await _manager(request, exact.workspace_id) != caller
        ):
            return ()
        return tuple(found)

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
            issued = await prove(request, query)
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

    if read_approved_descriptors is not None:
        bind_mounted_embed_discovery(read_share, discover)
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
