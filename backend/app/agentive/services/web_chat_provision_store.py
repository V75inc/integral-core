"""Durable managed provision backed by the existing profile-route revision CAS."""

from asyncio import timeout
from dataclasses import dataclass
from typing import Awaitable, Callable
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
from app.services.app_operations.transaction_scope import (
    require_outside_graph_transaction,
)


@dataclass(frozen=True)
class CommittedWebChatRouteChange:
    """Internal notification pinned to the exact committed CAS, not a request."""

    action: str
    previous: ChannelInstallation
    current: ChannelInstallation | None
    publication: tuple[str, int] | None


PostCommitWebChatRouteChange = Callable[[CommittedWebChatRouteChange], Awaitable[None]]


_POST_COMMIT_SECONDS = 2.0


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

    def __init__(
        self,
        host: WebChatPackageHost,
        *,
        post_commit: PostCommitWebChatRouteChange | None = None,
    ) -> None:
        """Require scoped readiness from the owner; supply no ready default."""
        self._host = host
        self._post_commit = post_commit

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
        committed = None

        async def mutate(profiles, _bindings, _native, route):
            nonlocal result, committed
            current = _installation(scope, route)
            if current != change.expected_installation:
                raise ResourceConflictError("Web Chat provision changed")
            if change.action == "install_disabled":
                if current is not None:
                    raise ResourceConflictError("Web Chat is already installed")
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
                route.theme = "system"
            else:
                raise ResourceConflictError("Unknown Web Chat lifecycle change")
            await profile_management.require_profile_manager(
                scope.actor_id, scope.workspace_id
            )
            route.generation += 1
            result = _installation(scope, route)
            if current is not None:
                published = public_web_chat.eligible_publication(
                    profiles, route.profile_key
                )
                committed = CommittedWebChatRouteChange(
                    change.action,
                    current,
                    result,
                    (
                        (route.profile_key, published["version"])
                        if published is not None
                        else None
                    ),
                )

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
        if self._post_commit is not None and committed is not None:
            try:
                # Do not leak work outside a transaction an upstream caller still owns.
                require_outside_graph_transaction()
                async with timeout(_POST_COMMIT_SECONDS):
                    await profile_management.require_profile_manager(
                        scope.actor_id, scope.workspace_id
                    )
                    await self._post_commit(committed)
            except Exception:
                # Core CAS is already committed. A failed host notification is not
                # a failed config write. The fresh serving probe denies missing proof.
                # Only an explicit host operation may repair it.
                pass
        return result
