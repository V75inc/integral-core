"""Test exact refreshed server-owned embed policy independently of transport."""

from dataclasses import replace
from unittest.mock import AsyncMock

import pytest

from app.agentive.connectors.external_chat import ExternalChatProvision
from app.web.web_chat_embed_policy import (
    ManagedWebChatEmbedPolicy,
    check_current_web_chat_embed_policy,
)

POLICY = ManagedWebChatEmbedPolicy(
    ExternalChatProvision("workspace", "public_web_chat", "provision"),
    "5",
    "public-profile",
    3,
    2,
    "https://partner.example.invalid",
    "https://visitor.example.invalid",
    "embed-opaque",
)


async def _check(reader, *, policy=POLICY, path="/embed/embed-opaque"):
    return await check_current_web_chat_embed_policy(
        expected=policy,
        served_origin=POLICY.embed_origin,
        raw_path=path,
        read_current=reader,
    )


@pytest.mark.asyncio
async def test_exact_policy_ok_only_for_canonical_host():
    """The exact server-owned current scope matches; other origins do not."""
    reader = AsyncMock(return_value=POLICY)
    assert await _check(reader)
    reader.assert_awaited_once_with("embed-opaque")
    assert not await check_current_web_chat_embed_policy(
        expected=POLICY,
        served_origin=POLICY.parent_origin,
        raw_path="/embed/embed-opaque",
        read_current=reader,
    )
    reader.assert_awaited_once_with("embed-opaque")


@pytest.mark.parametrize(
    "change",
    [
        {"parent_origin": POLICY.embed_origin},
        {"parent_origin": "http://parent.example.invalid"},
        {"parent_origin": "https://parent.example.invalid/"},
        {"parent_origin": "https://localhost"},
        {"parent_origin": "https://127.0.0.1"},
        {"parent_origin": "https://0x7f000001"},
        {"parent_origin": "https://client.example.invalid;script-src"},
        {"embed_reference": "route/another"},
        {"policy_revision": 0},
        {"published_version": True},
        {"route_epoch": "05"},
        {"provision": ExternalChatProvision("workspace", "whatsapp", "provision")},
    ],
)
def test_invalid_policy_declines_embed(change):
    """Reject unsafe origins, revisions and mismatched connector kinds."""
    assert not replace(POLICY, **change).valid()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "current",
    [
        None,
        replace(POLICY, route_epoch="6"),
        replace(POLICY, published_version=4),
        replace(POLICY, policy_revision=3),
        replace(POLICY, parent_origin="https://new.example.invalid"),
        replace(POLICY, provision=replace(POLICY.provision, workspace_id="other")),
    ],
)
async def test_missing_or_changed_attestation_fails_current_policy_check(current):
    """Reject revoked policy, stale assignment and unrelated scope."""
    assert not await _check(AsyncMock(return_value=current))


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "path", ["/chat/embed-opaque", "/embed/embed-opaque?q=1", "/embed/other-reference"]
)
async def test_full_page_or_different_embed_path_is_not_accepted(path):
    """The embed policy cannot admit other routes or query-bearing paths."""
    reader = AsyncMock(return_value=POLICY)
    assert not await _check(reader, path=path)
    reader.assert_not_awaited()
