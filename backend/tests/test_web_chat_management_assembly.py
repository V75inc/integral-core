"""Explicit opt-in does not replace other managed packages or expose ingress."""

from unittest.mock import AsyncMock

import pytest

from app.agentive.connectors.external_chat import (
    ExternalChatConversation,
    ExternalChatEventKey,
    ExternalChatProvision,
    VerifiedExternalChatMessage,
)
from app.agentive.services.connection_profiles import ProfileDenied
from app.connectors.channel_packages import (
    ChannelDefinition,
    ChannelLifecycle,
    ChannelPackage,
    ChannelPackageRegistry,
    ChannelScope,
    ChannelServing,
    ChannelServingStatus,
)
from app.web.web_chat_management_assembly import register_web_chat_management

pytestmark = [pytest.mark.asyncio, pytest.mark.unit]


async def test_registration_is_explicit_and_does_not_replace_other_packages():
    registry = ChannelPackageRegistry()
    outsider = ChannelPackage(
        ChannelDefinition(
            "fixture_channel",
            "Fixture",
            "Fixture channel",
            "An unrelated test channel.",
            "managed-text-v1",
        ),
        ChannelLifecycle(
            observe=AsyncMock(return_value=None),
            install=AsyncMock(),
            set_enabled=AsyncMock(),
            remove=AsyncMock(),
        ),
        ChannelServing(
            probe=AsyncMock(
                return_value=ChannelServingStatus(
                    "fixture-workspace", "fixture_channel", None, None, "ready"
                )
            ),
            receive=AsyncMock(),
        ),
    )
    registry.register(outsider)
    scope = ChannelScope("fixture-owner", "fixture-workspace")
    before = await registry.describe_one(scope, "fixture_channel")
    assert before.can_install and registry.resolve("public_web_chat") is None

    assert register_web_chat_management(registry) is None
    web_chat = registry.resolve("public_web_chat")
    assert web_chat is not None
    assert registry.resolve("fixture_channel") is outsider
    assert await registry.describe_one(scope, "fixture_channel") == before
    assert web_chat.serving.full_page_link is None
    with pytest.raises(ValueError, match="already registered"):
        register_web_chat_management(registry)
    assert registry.resolve("public_web_chat") is web_chat
    with pytest.raises(TypeError, match="registry"):
        register_web_chat_management(object())

    provision = ExternalChatProvision(
        "fixture-workspace", "public_web_chat", "fixture-provision"
    )
    event = ExternalChatEventKey(
        ExternalChatConversation(provision, "fixture-conversation"), "fixture-event"
    )
    with pytest.raises(ProfileDenied, match="serving is not configured"):
        await web_chat.serving.receive(
            VerifiedExternalChatMessage(event, "fixture-sender", "Plain fixture text")
        )
