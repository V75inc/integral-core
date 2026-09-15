"""ChannelIdentity link paths accept an AuthUser id, not just a graph User id.

Callers reach these services through ``resolve_principal_id(request)``, which
yields the **AuthUser** id (``o.User.*``) — not the graph ``User`` node id
(``n.User.*``). The services used ``await User.get(user_id)``, which does not
resolve the AuthUser form, and then silently skipped the
``HAS_CHANNEL_IDENTITY`` wire under ``if not user:``. That left every
identity detached from the graph (I-GRAPH-01), so ``resolve_channel_identity``
— which walks only that edge — could never resolve a verified identity, and a
second ``create_channel_identity_with_otp`` blew up with
``AttributeError: 'NoneType' object has no attribute 'nodes'``.

The existing suites miss this because ``conftest``'s ``test_user.id`` is
already a graph node id; these tests deliberately pass ``test_user.user_id``.
"""

from __future__ import annotations

import pytest

from app.agentive.edges import HAS_CHANNEL_IDENTITY
from app.agentive.services.channel_identity import (
    create_channel_identity_with_otp,
    initiate_link,
    resolve_channel_identity,
    verify_channel_otp,
)


def _auth_user_id(test_user) -> str:
    """The id ``resolve_principal_id`` would hand the service."""
    auth_user_id = getattr(test_user, "user_id", None)
    assert auth_user_id, "conftest test_user carries no AuthUser id"
    assert auth_user_id != test_user.id, "AuthUser id must differ from the node id"
    return auth_user_id


async def _identity_of(identity_id: str):
    from app.agentive.nodes import ChannelIdentity

    ci = await ChannelIdentity.get(identity_id)
    assert ci is not None
    return ci


@pytest.mark.asyncio
async def test_create_with_authuser_id_wires_the_edge(test_user):
    """The HAS_CHANNEL_IDENTITY edge exists when the caller passes an AuthUser id."""
    auth_user_id = _auth_user_id(test_user)
    phone = "+15550100001"

    result = await create_channel_identity_with_otp(auth_user_id, "sms", phone)
    ci = await _identity_of(result["identity_id"])

    owners = await ci.nodes(edge=[HAS_CHANNEL_IDENTITY], direction="in", node=["User"])
    assert [o.id for o in owners] == [test_user.id]


@pytest.mark.asyncio
async def test_verified_identity_resolves_back_to_the_user(test_user):
    """End-to-end: create with an AuthUser id, verify, then resolve."""
    auth_user_id = _auth_user_id(test_user)
    phone = "+15550100002"

    result = await create_channel_identity_with_otp(auth_user_id, "sms", phone)
    verified = await verify_channel_otp("sms", phone, result["otp_code"])
    assert verified.get("verified") is True, verified

    resolved = await resolve_channel_identity("sms", phone)
    assert resolved is not None, "a verified identity must resolve to its user"
    assert resolved["user_id"] == test_user.id


@pytest.mark.asyncio
async def test_second_create_does_not_explode_on_a_missing_user(test_user):
    """The re-issue branch called ``user.nodes`` on a ``None`` user."""
    auth_user_id = _auth_user_id(test_user)
    phone = "+15550100003"

    first = await create_channel_identity_with_otp(auth_user_id, "sms", phone)
    second = await create_channel_identity_with_otp(auth_user_id, "sms", phone)

    assert second["identity_id"] == first["identity_id"]
    assert second["verified"] is False
    ci = await _identity_of(second["identity_id"])
    owners = await ci.nodes(edge=[HAS_CHANNEL_IDENTITY], direction="in", node=["User"])
    assert [o.id for o in owners] == [test_user.id]


@pytest.mark.asyncio
async def test_initiate_link_with_authuser_id_wires_the_edge(test_user):
    """``initiate_link`` shares the same create path and the same bug."""
    auth_user_id = _auth_user_id(test_user)
    phone = "+15550100004"

    result = await initiate_link(auth_user_id, "whatsapp", phone)
    assert "error" not in result, result
    ci = await _identity_of(result["identity_id"])

    owners = await ci.nodes(edge=[HAS_CHANNEL_IDENTITY], direction="in", node=["User"])
    assert [o.id for o in owners] == [test_user.id]


@pytest.mark.asyncio
async def test_unresolvable_user_is_an_explicit_failure():
    """An unresolvable user must raise, never silently skip the edge wire."""
    from app.api.errors import ResourceNotFoundError

    with pytest.raises(ResourceNotFoundError):
        await create_channel_identity_with_otp(
            "o.User.does-not-exist", "sms", "+15550100005"
        )

    from app.agentive.nodes import ChannelIdentity

    orphans = await ChannelIdentity.find(channel="sms", channel_user_id="+15550100005")
    assert orphans == [], "no ChannelIdentity may be persisted without its owner"
