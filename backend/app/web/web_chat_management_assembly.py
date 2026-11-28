"""Explicit management-only registration of the durable Web Chat package."""

from app.agentive.connectors.external_chat import VerifiedExternalChatMessage
from app.agentive.services.connection_profiles import ProfileDenied
from app.agentive.services.web_chat_package import SLUG, ManagedWebChatPackage
from app.agentive.services.web_chat_provision_store import (
    ProfileRouteWebChatProvisionStore,
)
from app.connectors.channel_packages import (
    ChannelInstallation,
    ChannelPackage,
    ChannelPackageRegistry,
    ChannelScope,
    ChannelServingStatus,
)


class _ManagementOnlyHost:
    async def probe(
        self, scope: ChannelScope, installation: ChannelInstallation | None
    ) -> ChannelServingStatus:
        return ChannelServingStatus(
            scope.workspace_id,
            SLUG,
            installation.provision_id if installation else None,
            installation.route_epoch if installation else None,
            "unavailable",
        )

    async def receive_verified(self, _message: VerifiedExternalChatMessage) -> None:
        raise ProfileDenied("Web Chat serving is not configured")


def build_web_chat_management_package() -> ChannelPackage:
    """Construct disabled-only workspace setup with no public transport ports."""
    host = _ManagementOnlyHost()
    return ManagedWebChatPackage(ProfileRouteWebChatProvisionStore(host), host).package


def register_web_chat_management(registry: ChannelPackageRegistry) -> None:
    """Opt an existing registry into workspace-owned disabled Web Chat setup.

    No traffic, origin approval, or host-readiness facility is composed. The
    caller still owns registry injection into the authenticated management
    API. A serving host must instead explicitly compose its real authority;
    duplicate registration here never replaces an existing channel.
    """
    if not isinstance(registry, ChannelPackageRegistry):
        raise TypeError("A managed channel package registry is required")
    registry.register(build_web_chat_management_package())
