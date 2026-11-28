"""Verify Web Chat session admission against the canonical route."""

from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.agentive.connectors.external_chat import (
    ExternalChatAssignment,
    ExternalChatConversation,
    ExternalChatEventKey,
    ExternalChatProvision,
    VerifiedExternalChatMessage,
)
from app.agentive.services import profile_management, public_web_chat
from app.agentive.services.web_chat_admission import (
    FullPageWebChatAdmission,
    VerifiedFullPageSession,
)

pytestmark = pytest.mark.smoke


@pytest.fixture
def admitted(monkeypatch):
    monkeypatch.setattr(
        profile_management,
        "live_public_service_identity",
        AsyncMock(return_value=object()),
    )
    """Create an exact scoped session with a mutable simulated route."""
    provision = ExternalChatProvision("workspace", "public_web_chat", "provision")
    chat = ExternalChatConversation(provision, "conversation")
    message = VerifiedExternalChatMessage(
        ExternalChatEventKey(chat, "event"), "visitor", "Hello"
    )
    verified = VerifiedFullPageSession(
        chat, public_web_chat.PublicWebChatScope("workspace", "visitor", "public", 7, 3)
    )
    reader = AsyncMock(return_value=verified)
    route = {
        "profile_key": "public",
        "enabled": True,
        "generation": 3,
        "provision_id": provision.provision_id,
    }
    publication = {"key": "public", "audience": "public", "tools": [], "version": 7}
    record = AsyncMock(
        return_value=SimpleNamespace(
            public_web_chat=route, profiles={"public": {"published": publication}}
        )
    )
    ready = AsyncMock(return_value=True)
    monkeypatch.setattr(profile_management, "record", record)
    monkeypatch.setattr(public_web_chat, "transport_ready", ready)
    return SimpleNamespace(
        message=message,
        provision=provision,
        reader=reader,
        route=route,
        publication=publication,
        ready=ready,
        record=record,
        adapter=FullPageWebChatAdmission(reader),
    )


@pytest.mark.asyncio
async def test_adapter_projects_exact_current_assignment(admitted):
    """Project a ready assignment scoped to the verified conversation."""
    assert await admitted.adapter.text_only_assignment(
        admitted.message
    ) == ExternalChatAssignment(admitted.provision, "public", "7", "3")
    admitted.ready.assert_awaited_once_with("workspace")
    admitted.record.assert_awaited_once_with("workspace")


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "field",
    [
        "workspace_id",
        "provision_id",
        "connector_kind",
        "conversation",
        "visitor",
        "no-session",
    ],
)
async def test_scope_mismatches_deny_before_route_read(admitted, field):
    """Reject incorrect selectors before resolving host state."""
    message = admitted.message
    chat = message.key.conversation
    if field == "no-session":
        admitted.reader.return_value = None
    elif field == "visitor":
        message = replace(message, verified_sender_id="different-visitor")
    elif field == "conversation":
        message = replace(
            message,
            key=replace(
                message.key,
                conversation=replace(chat, external_conversation_id="different-room"),
            ),
        )
    else:
        message = replace(
            message,
            key=replace(
                message.key,
                conversation=replace(
                    chat, provision=replace(chat.provision, **{field: "different"})
                ),
            ),
        )
    assert await admitted.adapter.text_only_assignment(message) is None
    admitted.ready.assert_not_awaited()
    admitted.record.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "disposition", ["disabled", "new-epoch", "republished", "tools-enabled", "unready"]
)
async def test_fresh_lifecycle_denies_revoked_or_changed_assignment(
    admitted, disposition
):
    """Deny when the route or current eligible publication changes."""
    if disposition == "disabled":
        admitted.route["enabled"] = False
    elif disposition == "new-epoch":
        admitted.route["generation"] = 4
    elif disposition == "republished":
        admitted.publication["version"] = 8
    elif disposition == "tools-enabled":
        admitted.publication["tools"] = ["tool"]
    else:
        admitted.ready.return_value = False
    assert await admitted.adapter.text_only_assignment(admitted.message) is None


async def test_rejects_legacy_or_replaced_provision_with_same_epoch(admitted):
    """Exact installation identity matters even if a route reuses its epoch."""
    adapter = FullPageWebChatAdmission(admitted.reader)
    admitted.route.pop("provision_id")
    assert await adapter.text_only_assignment(admitted.message) is None
    admitted.route["provision_id"] = "replacement"
    assert await adapter.text_only_assignment(admitted.message) is None
