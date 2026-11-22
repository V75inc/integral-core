"""Unregistered managed Web Chat package with explicit trusted host ports."""

from dataclasses import dataclass
from typing import Literal, Protocol

from app.agentive.connectors.external_chat import VerifiedExternalChatMessage
from app.agentive.services import profile_management, public_web_chat
from app.api.errors import ResourceConflictError
from app.connectors.channel_packages import (
    ChannelDefinition,
    ChannelInstallation,
    ChannelLifecycle,
    ChannelPackage,
    ChannelScope,
    ChannelServing,
    ChannelServingStatus,
)
from app.schemas.agentive.connection_profiles import PublicWebChatRoute

SLUG = "public_web_chat"


@dataclass(frozen=True)
class WebChatProvisionChange:
    """One requested effect, preconditioned on the observed provision and policy."""

    action: Literal["install_disabled", "assign", "enable", "disable", "remove"]
    expected_installation: ChannelInstallation | None
    expected_profile_revision: int
    profile_key: str | None = None


class WebChatProvisionStore(Protocol):
    """Trusted, durable workspace provision lifecycle (not implemented here)."""

    async def observe(self, scope: ChannelScope) -> ChannelInstallation | None:
        """Return exact stored provision; never derive one from profile config."""
        ...

    async def commit(
        self, scope: ChannelScope, change: WebChatProvisionChange
    ) -> ChannelInstallation | None:
        """Atomically CAS provision and profile revision using owning policy service.

        Recheck manager authority and the tool-free profile in the transaction.
        Install requires still-current host readiness, creates a fresh provision
        disabled, and does not adopt a legacy enablement. Enable requires fresh
        readiness at commit. Assignment, enablement and removal must invalidate
        old sessions (rotate epoch), and removal must leave no enabled route.
        """
        ...


class WebChatPackageHost(Protocol):
    """Trusted integration with actual readiness and verified visitor ingress."""

    async def probe(
        self, scope: ChannelScope, installation: ChannelInstallation | None
    ) -> ChannelServingStatus:
        """Attest exact scope and current readiness without accepting browser claims."""
        ...

    async def receive_verified(self, message: VerifiedExternalChatMessage) -> None:
        """Revalidate the verified session, current provision and profile admission."""
        ...


class ManagedWebChatPackage:
    """Compose management and transport without registering or mounting them."""

    def __init__(self, store: WebChatProvisionStore, host: WebChatPackageHost) -> None:
        """Require real owner implementations for persistence and ingress."""
        self._store = store
        self._host = host
        self.package = ChannelPackage(
            ChannelDefinition(
                SLUG,
                "Integral",
                "Web Chat",
                "A public text chat for one published agent profile.",
                "managed-text-v1",
            ),
            ChannelLifecycle(self.observe, self.install, self.set_enabled, self.remove),
            ChannelServing(self.probe, self.receive),
        )

    @staticmethod
    def _validate_installation(
        scope: ChannelScope, value: ChannelInstallation
    ) -> ChannelInstallation:
        if (
            not isinstance(value, ChannelInstallation)
            or value.workspace_id != scope.workspace_id
            or value.slug != SLUG
            or not isinstance(value.provision_id, str)
            or not value.provision_id.strip()
            or not isinstance(value.route_epoch, str)
            or not value.route_epoch.strip()
            or type(value.enabled) is not bool
        ):
            raise ResourceConflictError("Web Chat provision could not be verified")
        return value

    async def observe(self, scope: ChannelScope) -> ChannelInstallation | None:
        """Expose only a stored, exact workspace provision to a manager."""
        await profile_management.require_profile_manager(
            scope.actor_id, scope.workspace_id
        )
        stored = await self._store.observe(scope)
        return (
            self._validate_installation(scope, stored) if stored is not None else None
        )

    async def _profile(
        self, scope: ChannelScope
    ) -> tuple[int, PublicWebChatRoute, dict]:
        record = await profile_management.record(scope.workspace_id)
        if record is None:
            return 0, PublicWebChatRoute(), {}
        return (
            record.revision,
            PublicWebChatRoute.model_validate(record.public_web_chat),
            record.profiles,
        )

    async def _readiness(
        self, scope: ChannelScope, current: ChannelInstallation | None
    ) -> ChannelServingStatus:
        value = await self._host.probe(scope, current)
        provision = current.provision_id if current else None
        epoch = current.route_epoch if current else None
        if (
            not isinstance(value, ChannelServingStatus)
            or (value.workspace_id, value.slug, value.provision_id, value.route_epoch)
            != (scope.workspace_id, SLUG, provision, epoch)
            or value.state not in {"ready", "accepting", "unavailable"}
        ):
            return ChannelServingStatus(
                scope.workspace_id, SLUG, provision, epoch, "unavailable"
            )
        return value

    async def probe(
        self, scope: ChannelScope, installation: ChannelInstallation | None
    ) -> ChannelServingStatus:
        """Require fresh host evidence and the existing tool-free profile policy."""
        current = await self.observe(scope)
        if installation != current:
            raise ResourceConflictError("Web Chat provision changed")
        readiness = await self._readiness(scope, current)
        if current is None:
            return readiness
        _, route, profiles = await self._profile(scope)
        eligible = public_web_chat.eligible_publication(profiles, route.profile_key)
        if (
            eligible is not None
            and route.enabled == current.enabled
            and readiness.state == ("accepting" if current.enabled else "ready")
        ):
            return readiness
        return ChannelServingStatus(
            scope.workspace_id,
            SLUG,
            current.provision_id,
            current.route_epoch,
            "unavailable",
        )

    async def install(self, scope: ChannelScope) -> ChannelInstallation:
        """Create an explicitly disabled provision, never adopt an enabled flag."""
        if await self.observe(scope) is not None:
            raise ResourceConflictError("Web Chat is already installed")
        if (await self._readiness(scope, None)).state != "ready":
            raise ResourceConflictError("Web Chat host is not ready")
        revision, _, _ = await self._profile(scope)
        result = await self._store.commit(
            scope, WebChatProvisionChange("install_disabled", None, revision)
        )
        if result is None or self._validate_installation(scope, result).enabled:
            raise ResourceConflictError("Web Chat installation could not be verified")
        return result

    async def assign_profile(
        self,
        scope: ChannelScope,
        current: ChannelInstallation,
        expected_profile_revision: int,
        profile_key: str | None,
    ) -> None:
        """Configure a disabled provision; never enable or publish."""
        if await self.observe(scope) != current or current.enabled:
            raise ResourceConflictError("Web Chat provision changed or is enabled")
        revision, route, profiles = await self._profile(scope)
        if (
            revision != expected_profile_revision
            or route.enabled
            or (
                profile_key is not None
                and not public_web_chat.eligible_publication(profiles, profile_key)
            )
        ):
            raise ResourceConflictError("Web Chat profile could not be assigned")
        await self._store.commit(
            scope, WebChatProvisionChange("assign", current, revision, profile_key)
        )

    async def set_enabled(
        self, scope: ChannelScope, current: ChannelInstallation, enabled: bool
    ) -> None:
        """Require readiness to enable; permit exact disablement during outages."""
        if await self.observe(scope) != current:
            raise ResourceConflictError("Web Chat provision changed")
        revision, route, profiles = await self._profile(scope)
        if enabled and (
            current.enabled
            or route.enabled
            or not public_web_chat.eligible_publication(profiles, route.profile_key)
            or (await self._readiness(scope, current)).state != "ready"
        ):
            raise ResourceConflictError("Web Chat is not ready to enable")
        await self._store.commit(
            scope,
            WebChatProvisionChange(
                "enable" if enabled else "disable", current, revision
            ),
        )

    async def remove(self, scope: ChannelScope, current: ChannelInstallation) -> None:
        """Remove the exact provision without requiring healthy ingress."""
        if await self.observe(scope) != current:
            raise ResourceConflictError("Web Chat provision changed")
        revision, _, _ = await self._profile(scope)
        await self._store.commit(
            scope, WebChatProvisionChange("remove", current, revision)
        )

    async def receive(self, message: VerifiedExternalChatMessage) -> None:
        """Only delegate verified Web Chat ingress to its explicitly trusted host."""
        if message.key.conversation.provision.connector_kind != SLUG:
            raise ResourceConflictError("Wrong Web Chat transport")
        await self._host.receive_verified(message)
