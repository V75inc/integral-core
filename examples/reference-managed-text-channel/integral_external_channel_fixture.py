"""Test-only external channel package; importing it has no side effects."""

from dataclasses import replace
from itertools import count

from app.agentive.connectors.external_chat import VerifiedExternalChatMessage
from app.connectors.channel_packages import (
    ChannelDefinition,
    ChannelInstallation,
    ChannelLifecycle,
    ChannelPackage,
    ChannelScope,
    ChannelServing,
    ChannelServingStatus,
)

SLUG = "external_test_channel"


def build_channel() -> ChannelPackage:
    """Supply a local synthetic channel with no provider, network, or secrets."""
    installations: dict[str, ChannelInstallation] = {}
    serial = count(1)

    async def observe(scope: ChannelScope) -> ChannelInstallation | None:
        return installations.get(scope.workspace_id)

    async def install(scope: ChannelScope) -> ChannelInstallation:
        if scope.workspace_id in installations:
            raise ValueError("Synthetic instance already installed")
        unit = next(serial)
        installation = ChannelInstallation(
            scope.workspace_id,
            SLUG,
            f"test-provision-{unit}",
            f"test-epoch-{unit}",
            False,
        )
        installations[scope.workspace_id] = installation
        return installation

    async def set_enabled(
        scope: ChannelScope, expected: ChannelInstallation, enabled: bool
    ) -> None:
        if installations.get(scope.workspace_id) != expected:
            raise ValueError("Synthetic instance changed")
        if expected.enabled != enabled:
            installations[scope.workspace_id] = replace(
                expected, enabled=enabled, route_epoch=f"test-epoch-{next(serial)}"
            )

    async def remove(scope: ChannelScope, expected: ChannelInstallation) -> None:
        if installations.get(scope.workspace_id) != expected:
            raise ValueError("Synthetic instance changed")
        del installations[scope.workspace_id]

    async def probe(
        scope: ChannelScope, installation: ChannelInstallation | None
    ) -> ChannelServingStatus:
        current = installations.get(scope.workspace_id)
        state = (
            "unavailable"
            if current != installation
            else "accepting" if current is not None and current.enabled else "ready"
        )
        return ChannelServingStatus(
            scope.workspace_id,
            SLUG,
            installation.provision_id if installation else None,
            installation.route_epoch if installation else None,
            state,
        )

    async def receive(message: VerifiedExternalChatMessage) -> None:
        scope = message.key.conversation.provision
        current = installations.get(scope.workspace_id)
        if (
            current is None
            or not current.enabled
            or scope.connector_kind != SLUG
            or scope.provision_id != current.provision_id
        ):
            raise ValueError("Synthetic instance is not accepting")
        # No model call, network request, record or reply, even when "enabled".

    return ChannelPackage(
        ChannelDefinition(
            SLUG,
            "Integral QA",
            "External test channel",
            "Synthetic fixture installed separately from Core.",
            "managed-text-v1",
            install_scope="shared",
        ),
        ChannelLifecycle(observe, install, set_enabled, remove),
        ChannelServing(probe, receive),
    )
