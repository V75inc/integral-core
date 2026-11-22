"""Durable managed provision backed by the existing profile-route revision CAS."""

from uuid import uuid4

from app.agentive.services import profile_management, public_web_chat
from app.agentive.services.web_chat_package import (
    SLUG,
    WebChatPackageHost,
    WebChatProvisionChange,
)
from app.api.errors import BadRequestError, ResourceConflictError
from app.connectors.channel_packages import (
    ChannelInstallation,
    ChannelScope,
    ChannelServingStatus,
)
from app.schemas.agentive.connection_profiles import PublicWebChatRoute


def _installation(
    scope: ChannelScope, route: PublicWebChatRoute
) -> ChannelInstallation | None:
    if route.provision_id is None:
        return None
    return ChannelInstallation(
        scope.workspace_id,
        SLUG,
        route.provision_id,
        str(route.generation),
        route.enabled,
    )


class ProfileRouteWebChatProvisionStore:
    """Own exact provision and assignment CAS on the same persisted route record."""

    def __init__(self, host: WebChatPackageHost) -> None:
        """Require scoped readiness from the owner; supply no ready default."""
        self._host = host

    async def observe(self, scope: ChannelScope) -> ChannelInstallation | None:
        """Resolve current durable state for the authorized workspace manager."""
        await profile_management.require_profile_manager(
            scope.actor_id, scope.workspace_id
        )
        record = await profile_management.record(scope.workspace_id)
        route = PublicWebChatRoute.model_validate(
            record.public_web_chat if record else {}
        )
        return _installation(scope, route)

    async def _assert_ready(
        self, scope: ChannelScope, current: ChannelInstallation | None
    ) -> None:
        observed = await self._host.probe(scope, current)
        if observed != ChannelServingStatus(
            scope.workspace_id,
            SLUG,
            current.provision_id if current else None,
            current.route_epoch if current else None,
            "ready",
        ):
            raise ResourceConflictError("Web Chat host is not ready")

    async def commit(
        self, scope: ChannelScope, change: WebChatProvisionChange
    ) -> ChannelInstallation | None:
        """Apply one action only at the expected profile revision and provision."""
        result = None

        async def mutate(profiles, _bindings, _native, route):
            nonlocal result
            current = _installation(scope, route)
            if current != change.expected_installation:
                raise ResourceConflictError("Web Chat provision changed")
            if change.action == "install_disabled":
                if current is not None:
                    raise ResourceConflictError("Web Chat is already installed")
                await self._assert_ready(scope, None)
                route.provision_id = uuid4().hex
                # Explicitly start fresh, not as an implicit legacy migration.
                route.enabled = False
                route.profile_key = None
            elif current is None:
                raise ResourceConflictError("Web Chat is not installed")
            elif change.action == "assign":
                if route.enabled or (
                    change.profile_key is not None
                    and public_web_chat.eligible_publication(
                        profiles, change.profile_key
                    )
                    is None
                ):
                    raise ResourceConflictError(
                        "Web Chat profile could not be assigned"
                    )
                route.profile_key = change.profile_key
            elif change.action == "enable":
                if (
                    route.enabled
                    or public_web_chat.eligible_publication(profiles, route.profile_key)
                    is None
                ):
                    raise ResourceConflictError("Web Chat is not ready to enable")
                await self._assert_ready(scope, current)
                route.enabled = True
            elif change.action == "disable":
                route.enabled = False
            elif change.action == "remove":
                route.enabled = False
                route.profile_key = None
                route.provision_id = None
            else:
                raise ResourceConflictError("Unknown Web Chat lifecycle change")
            await profile_management.require_profile_manager(
                scope.actor_id, scope.workspace_id
            )
            route.generation += 1
            result = _installation(scope, route)

        try:
            await profile_management.mutate_config(
                scope.actor_id,
                scope.workspace_id,
                change.expected_profile_revision,
                mutate,
            )
        except BadRequestError:
            raise ResourceConflictError(
                "Web Chat settings changed. Refresh and try again."
            ) from None
        return result
