"""Unified but unmounted assembly of the managed full-page Web Chat host."""

from dataclasses import dataclass
from typing import Awaitable, Callable, Protocol

from fastapi.routing import APIRouter
from jvspatial.db.postgres import PostgresDB

from app.agentive.connectors.external_chat import (
    ExternalChatProvision,
    VerifiedExternalChatMessage,
)
from app.agentive.services import profile_management, public_web_chat
from app.agentive.services.connection_profiles import ProfileDenied
from app.agentive.services.web_chat_package import SLUG, ManagedWebChatPackage
from app.agentive.services.web_chat_profile_inference import TextModel
from app.agentive.services.web_chat_provision_store import (
    ProfileRouteWebChatProvisionStore,
)
from app.agentive.services.web_chat_request_budget import WebChatBudgetPolicy
from app.connectors.channel_packages import (
    ChannelInstallation,
    ChannelPackageRegistry,
    ChannelScope,
    ChannelServingStatus,
)
from app.schemas.agentive.connection_profiles import PublicWebChatRoute
from app.web.full_page_chat import is_canonical_web_chat_origin
from app.web.full_page_chat_composition import compose_full_page_web_chat
from app.web.web_chat_reference_store import PostgresWebChatReferenceStore
from app.web.web_chat_route_authority import (
    ApprovedWebChatReference,
    WebChatRouteAuthority,
)
from app.web.web_chat_share import (
    IssuedFullPageShare,
    create_managed_web_chat_share_router,
)


@dataclass(frozen=True)
class ManagedWebChatHostRoute:
    """Current durable assignment; independent of host approval or traffic."""

    provision: ExternalChatProvision
    epoch: str
    profile_key: str
    published_version: int
    enabled: bool


class WebChatPlatform(Protocol):
    """Trusted deployment owner; never populated from request data."""

    async def install_ready(self, workspace_id: str) -> bool:
        """Attest exact workspace infrastructure before an installation."""
        ...

    async def approved_ready_origin(self, route: ManagedWebChatHostRoute) -> str | None:
        """Attest approved origin and hosting readiness for this exact state.

        A disabled assignment is checked for pre-enable capability. For an
        enabled assignment also require exact visitor page and API readiness.
        """
        ...


@dataclass(frozen=True)
class WebChatPageContext:
    """Only the static title and opaque route required by the visitor UI."""

    route: str
    title: str = "Web Chat"


@dataclass(frozen=True)
class WebChatHostAssembly:
    """Explicit host outputs; no production registration or mount on construction."""

    registry: ChannelPackageRegistry
    share_router: APIRouter
    browser_router: APIRouter
    reconcile: Callable[[ExternalChatProvision], Awaitable[bool]]
    page_context: Callable[[str, str], Awaitable[WebChatPageContext | None]]


async def _managed_route(
    provision: ExternalChatProvision,
) -> ManagedWebChatHostRoute | None:
    if provision.connector_kind != SLUG:
        return None
    record = await profile_management.record(provision.workspace_id)
    if record is None:
        return None
    policy = PublicWebChatRoute.model_validate(record.public_web_chat)
    if policy.provision_id != provision.provision_id or policy.profile_key is None:
        return None
    published = public_web_chat.eligible_publication(
        record.profiles, policy.profile_key
    )
    if published is None:
        return None
    return ManagedWebChatHostRoute(
        provision,
        str(policy.generation),
        policy.profile_key,
        published["version"],
        policy.enabled,
    )


def _same_assignment(
    approved: ApprovedWebChatReference, current: ManagedWebChatHostRoute | None
) -> bool:
    return current is not None and (
        approved.provision,
        approved.epoch,
        approved.profile_key,
        approved.published_version,
    ) == (
        current.provision,
        current.epoch,
        current.profile_key,
        current.published_version,
    )


class _Host:
    def __init__(self, authority: WebChatRouteAuthority) -> None:
        self._authority = authority

    async def probe(
        self, scope: ChannelScope, installation: ChannelInstallation | None
    ) -> ChannelServingStatus:
        """Use the same exact route authority used by visitor requests."""
        return await self._authority.probe(scope, installation)

    async def receive_verified(self, message: VerifiedExternalChatMessage) -> None:
        """A normalized envelope is not a cookie-bound verified browser request."""
        raise ProfileDenied("Use the authorized Web Chat browser request boundary")


def assemble_web_chat_host(
    *,
    database: PostgresDB,
    platform: WebChatPlatform,
    complete: TextModel,
    budget_policy: WebChatBudgetPolicy,
    budget_timeout_seconds: float,
    max_parallel_budget_acquisitions: int,
) -> WebChatHostAssembly:
    """Compose one explicit host while leaving every production mount external."""
    if not callable(getattr(platform, "install_ready", None)) or not callable(
        getattr(platform, "approved_ready_origin", None)
    ):
        raise TypeError("Web Chat requires explicit platform authority")

    async def origin_for(route: ManagedWebChatHostRoute) -> str | None:
        origin = await platform.approved_ready_origin(route)
        return origin if is_canonical_web_chat_origin(origin) else None

    async def transport_ready(approved: ApprovedWebChatReference) -> bool:
        current = await _managed_route(approved.provision)
        if current is None or not _same_assignment(approved, current):
            return False
        return (
            await origin_for(current) == approved.origin
            and await _managed_route(approved.provision) == current
        )

    references = PostgresWebChatReferenceStore(
        database=database,
        install_readiness=platform.install_ready,
        transport_readiness=transport_ready,
    )
    authority = WebChatRouteAuthority(references)
    host = _Host(authority)
    registry = ChannelPackageRegistry()
    registry.register(
        ManagedWebChatPackage(ProfileRouteWebChatProvisionStore(host), host).package
    )

    async def reconcile(provision: ExternalChatProvision) -> bool:
        # Internal host operation only. Never pass browser intent directly.
        old = await references.observe(provision)
        current = await _managed_route(provision)
        origin = await origin_for(current) if current is not None else None
        if current != await _managed_route(provision):
            return False
        if current is None or origin is None:
            if old.approved is not None:
                await references.revoke(provision, expected_revision=old.revision)
            return False
        if (
            old.approved is not None
            and old.approved.origin == origin
            and _same_assignment(old.approved, current)
        ):
            return await references.by_provision(provision) == old.approved
        updated = await references.issue(
            provision,
            expected_revision=old.revision,
            origin=origin,
            epoch=current.epoch,
            profile_key=current.profile_key,
            published_version=current.published_version,
        )
        return (
            updated is not None
            and updated.approved is not None
            and await _managed_route(provision) == current
            and await references.by_provision(provision) == updated.approved
            and await origin_for(current) == origin
        )

    async def read_issued(
        provision: ExternalChatProvision, epoch: str, publication: tuple[str, int]
    ) -> IssuedFullPageShare | None:
        approved = await references.by_provision(provision)
        if (
            approved is None
            or approved.epoch != epoch
            or (approved.profile_key, approved.published_version) != publication
        ):
            return None
        # This formats a persisted current key; the share router re-admits it.
        return IssuedFullPageShare(
            approved.key, f"{approved.origin}/chat/{approved.key}"
        )

    async def page_context(key: str, served_origin: str) -> WebChatPageContext | None:
        # The host supplies its canonical serving origin, not a forwarded URL.
        active = await authority.resolve(key)
        if active is None or active.origin != served_origin:
            return None
        return WebChatPageContext(route=active.route_key)

    return WebChatHostAssembly(
        registry=registry,
        share_router=create_managed_web_chat_share_router(
            read_issued=read_issued,
            resolve_active_route=authority.resolve,
        ),
        browser_router=compose_full_page_web_chat(
            database=database,
            resolve_route=authority.resolve,
            readiness=authority.readiness,
            complete=complete,
            budget_policy=budget_policy,
            budget_timeout_seconds=budget_timeout_seconds,
            max_parallel_budget_acquisitions=max_parallel_budget_acquisitions,
        ),
        reconcile=reconcile,
        page_context=page_context,
    )
