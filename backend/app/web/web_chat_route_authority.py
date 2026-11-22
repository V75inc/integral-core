"""Detached, read-only authority for exact managed Web Chat routes."""

from dataclasses import dataclass
from typing import Protocol

from pydantic import ValidationError

from app.agentive.connectors.external_chat import ExternalChatProvision
from app.agentive.services import profile_management, public_web_chat
from app.agentive.services.web_chat_package import SLUG
from app.api.errors import BadRequestError
from app.connectors.channel_packages import (
    ChannelInstallation,
    ChannelScope,
    ChannelServingStatus,
    ServingState,
)
from app.schemas.agentive.connection_profiles import PublicWebChatRoute
from app.web.full_page_chat import (
    is_canonical_web_chat_origin,
    is_web_chat_route_reference,
)
from app.web.full_page_chat_composition import ResolvedWebChatRoute


@dataclass(frozen=True)
class ApprovedWebChatReference:
    """Host-owned approval of one origin and exact provision/publication epoch."""

    key: str
    origin: str
    provision: ExternalChatProvision
    epoch: str
    profile_key: str
    published_version: int


class WebChatReferenceReader(Protocol):
    """Read current host approval through consistent authoritative indexes.

    The host must never reassign an issued key to a different origin, provision,
    assignment epoch or publication. A new approval uses a new opaque key.
    """

    async def by_key(self, key: str) -> ApprovedWebChatReference | None:
        """Resolve the opaque reference, never caller-supplied route metadata."""
        ...

    async def by_provision(
        self, provision: ExternalChatProvision
    ) -> ApprovedWebChatReference | None:
        """Resolve the current approval via the exact provision index."""
        ...

    async def install_ready(self, workspace_id: str) -> bool:
        """Attest infrastructure for this workspace, before any provision exists."""
        ...

    async def transport_ready(self, approved: ApprovedWebChatReference) -> bool:
        """Attest the approved transport, independently of the channel enabled bit."""
        ...


class WebChatRouteAuthority:
    """Share exact host readiness across management and detached visitor loading."""

    def __init__(self, reader: WebChatReferenceReader) -> None:
        """Use an explicit host reader; no implicit origin or readiness."""
        self._reader = reader

    @staticmethod
    async def _absent(workspace: str) -> bool:
        try:
            found = await profile_management.record(workspace)
            route = PublicWebChatRoute.model_validate(
                found.public_web_chat if found else {}
            )
        except (BadRequestError, ValidationError):
            return False
        return route.provision_id is None

    @staticmethod
    async def _current(approval: ApprovedWebChatReference) -> bool | None:
        try:
            workspace = approval.provision.workspace_id
            found = await profile_management.record(workspace)
            route = (
                PublicWebChatRoute.model_validate(found.public_web_chat)
                if found
                else None
            )
            if (
                route is None
                or route.provision_id != approval.provision.provision_id
                or str(route.generation) != approval.epoch
                or route.profile_key != approval.profile_key
            ):
                return None
            published = public_web_chat.eligible_publication(
                found.profiles, route.profile_key
            )
        except (BadRequestError, ValidationError):
            return None
        if published is None or published["version"] != approval.published_version:
            return None
        return route.enabled

    async def _ready(
        self, approval: ApprovedWebChatReference, *, enabled: bool
    ) -> bool:
        if (
            not isinstance(approval, ApprovedWebChatReference)
            or not isinstance(approval.provision, ExternalChatProvision)
            or approval.provision.connector_kind != SLUG
            or not is_web_chat_route_reference(approval.key)
            or not is_canonical_web_chat_origin(approval.origin)
            or type(approval.published_version) is not int
            or len(str(approval.epoch)) > 20
        ):
            return False

        async def current() -> bool:
            return (
                await self._reader.by_key(approval.key) == approval
                and await self._reader.by_provision(approval.provision) == approval
                and await self._current(approval) is enabled
            )

        return (
            await current()
            and await self._reader.transport_ready(approval) is True
            and await current()
        )

    async def probe(
        self, scope: ChannelScope, installation: ChannelInstallation | None
    ) -> ChannelServingStatus:
        """Report disabled provision readiness only to its authorized manager."""
        await profile_management.require_profile_manager(
            scope.actor_id, scope.workspace_id
        )
        provision_id = installation.provision_id if installation else None
        epoch = installation.route_epoch if installation else None
        state: ServingState = "unavailable"
        if installation is None:
            if (
                await self._absent(scope.workspace_id)
                and await self._reader.install_ready(scope.workspace_id) is True
                and await self._absent(scope.workspace_id)
            ):
                state = "ready"
        elif (
            installation.workspace_id == scope.workspace_id
            and installation.slug == SLUG
            and type(installation.enabled) is bool
            and isinstance(installation.provision_id, str)
            and bool(installation.provision_id)
            and installation.provision_id == installation.provision_id.strip()
        ):
            provision = ExternalChatProvision(
                scope.workspace_id, SLUG, installation.provision_id
            )
            approval = await self._reader.by_provision(provision)
            if (
                isinstance(approval, ApprovedWebChatReference)
                and approval.provision == provision
                and approval.epoch == epoch
                and await self._ready(approval, enabled=installation.enabled)
            ):
                state = "accepting" if installation.enabled else "ready"
        return ChannelServingStatus(
            scope.workspace_id, SLUG, provision_id, epoch, state
        )

    async def resolve(self, key: str) -> ResolvedWebChatRoute | None:
        """Resolve only an enabled, exact, still-ready host-approved route."""
        if not is_web_chat_route_reference(key):
            return None
        approval = await self._reader.by_key(key)
        if (
            not isinstance(approval, ApprovedWebChatReference)
            or approval.key != key
            or not await self._ready(approval, enabled=True)
        ):
            return None
        return ResolvedWebChatRoute(
            approval.provision,
            approval.origin,
            approval.key,
            approval.epoch,
            (approval.profile_key, approval.published_version),
        )

    async def readiness(self, route: ResolvedWebChatRoute) -> bool:
        """Revalidate the same approved route, never a replacement publication."""
        return (
            isinstance(route, ResolvedWebChatRoute)
            and route.publication is not None
            and await self.resolve(route.route_key) == route
        )
