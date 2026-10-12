"""Policy CRUD is gated on administering the SUBJECT, not merely on being
authenticated.

Before this hardening ``policy.create`` / ``policy.update`` / ``policy.delete``
were evaluated against ``Resource(kind="policy", scope="user:<caller>")`` —
always allowed for the caller under the default-human path — so any user
could attach, rewrite or delete a Policy for anyone else's AgentConfig or
Connector, and ``GET /api/policies`` listed every Policy platform-wide.
"""

from __future__ import annotations

import pytest
from httpx import AsyncClient

from tests.test_policy_node import _bounded_policy_body


async def _register_personal_agent(client: AsyncClient) -> str:
    r = await client.post(
        "/api/agentive/uplink/register",
        json={"scope": "personal", "uplink_url": "http://127.0.0.1:9"},
    )
    assert r.status_code == 200, r.text
    return r.json()["agent_config_id"]


async def _create_connector(client: AsyncClient) -> str:
    r = await client.post("/api/agentive/connectors", json={"kind": "custom"})
    assert r.status_code in (200, 201), r.text
    return r.json()["id"]


@pytest.mark.smoke
@pytest.mark.asyncio
async def test_non_owner_cannot_create_policy_for_foreign_agent(
    authenticated_client: AsyncClient, second_user_client: AsyncClient
):
    """A stranger gets 403 creating a Policy for someone else's agent."""
    agent_id = await _register_personal_agent(authenticated_client)

    r = await second_user_client.post(
        "/api/policies",
        json=await _bounded_policy_body(
            second_user_client,
            {"subject_kind": "agent", "subject_id": agent_id, "actions": ["*"]},
        ),
    )
    assert r.status_code == 403, r.text


@pytest.mark.smoke
@pytest.mark.asyncio
async def test_owner_can_create_patch_and_delete_policy_for_own_agent(
    authenticated_client: AsyncClient, second_user_client: AsyncClient
):
    """Owner CRUD succeeds; a stranger gets 404 / 403 / 403."""
    agent_id = await _register_personal_agent(authenticated_client)

    r = await authenticated_client.post(
        "/api/policies",
        json=await _bounded_policy_body(
            authenticated_client,
            {
                "subject_kind": "agent",
                "subject_id": agent_id,
                "actions": ["entry.read"],
            },
        ),
    )
    assert r.status_code == 201, r.text
    policy_id = r.json()["id"]

    # A stranger can neither read, modify nor delete it.
    assert (
        await second_user_client.get(f"/api/policies/{policy_id}")
    ).status_code == 404
    r = await second_user_client.patch(
        f"/api/policies/{policy_id}", json={"actions": ["*"]}
    )
    assert r.status_code == 403, r.text
    assert (
        await second_user_client.delete(f"/api/policies/{policy_id}")
    ).status_code == 403

    # The owner can.
    r = await authenticated_client.patch(
        f"/api/policies/{policy_id}", json={"actions": ["entry.read", "entry.update"]}
    )
    assert r.status_code == 200, r.text
    assert (
        await authenticated_client.delete(f"/api/policies/{policy_id}")
    ).status_code == 204


@pytest.mark.smoke
@pytest.mark.asyncio
async def test_connector_policy_requires_connector_owner(
    authenticated_client: AsyncClient, second_user_client: AsyncClient
):
    """Connector-subject Policies require the connector owner."""
    connector_id = await _create_connector(second_user_client)

    r = await authenticated_client.post(
        "/api/policies",
        json=await _bounded_policy_body(
            authenticated_client,
            {"subject_kind": "connector", "subject_id": connector_id},
        ),
    )
    assert r.status_code == 403, r.text

    r = await second_user_client.post(
        "/api/policies",
        json=await _bounded_policy_body(
            second_user_client,
            {"subject_kind": "connector", "subject_id": connector_id},
        ),
    )
    assert r.status_code == 201, r.text


@pytest.mark.smoke
@pytest.mark.asyncio
async def test_human_subject_and_unknown_subject_are_admin_only(
    authenticated_client: AsyncClient,
    authenticated_admin_client: AsyncClient,
    test_user,
):
    """Human / unresolvable subjects are platform-admin only."""
    caller_id = getattr(test_user, "user_id", None) or test_user.id

    # Even for themselves, a non-admin cannot self-attach a human Policy.
    r = await authenticated_client.post(
        "/api/policies",
        json=await _bounded_policy_body(
            authenticated_client, {"subject_kind": "human", "subject_id": caller_id}
        ),
    )
    assert r.status_code == 403, r.text
    # Nor for an agent id that resolves to no node.
    r = await authenticated_client.post(
        "/api/policies", json={"subject_kind": "agent", "subject_id": "a-ghost"}
    )
    assert r.status_code == 403, r.text

    r = await authenticated_admin_client.post(
        "/api/policies",
        json=await _bounded_policy_body(
            authenticated_admin_client,
            {"subject_kind": "human", "subject_id": caller_id},
        ),
    )
    assert r.status_code == 201, r.text


@pytest.mark.smoke
@pytest.mark.asyncio
async def test_list_policies_is_filtered_to_administered_subjects(
    authenticated_client: AsyncClient,
    second_user_client: AsyncClient,
    authenticated_admin_client: AsyncClient,
):
    """GET /policies shows only administered subjects; admins see all."""
    mine = await _register_personal_agent(authenticated_client)
    theirs = await _register_personal_agent(second_user_client)

    r = await authenticated_client.post(
        "/api/policies",
        json=await _bounded_policy_body(
            authenticated_client, {"subject_kind": "agent", "subject_id": mine}
        ),
    )
    assert r.status_code == 201, r.text
    r = await second_user_client.post(
        "/api/policies",
        json=await _bounded_policy_body(
            second_user_client, {"subject_kind": "agent", "subject_id": theirs}
        ),
    )
    assert r.status_code == 201, r.text

    subjects = {
        p["subject_id"]
        for p in (await authenticated_client.get("/api/policies")).json()
    }
    assert mine in subjects
    assert theirs not in subjects

    # Explicit subject filter does not bypass the gate either.
    r = await authenticated_client.get(
        f"/api/policies?subject_kind=agent&subject_id={theirs}"
    )
    assert r.status_code == 200, r.text
    assert r.json() == []

    admin_subjects = {
        p["subject_id"]
        for p in (await authenticated_admin_client.get("/api/policies")).json()
    }
    assert {mine, theirs} <= admin_subjects


@pytest.mark.smoke
@pytest.mark.asyncio
async def test_stale_admin_token_cannot_administer_after_current_role_revocation(
    authenticated_admin_client, second_user_client
):
    from jvspatial.api.auth.models import User as AuthUser

    response = await authenticated_admin_client.get("/api/auth/me")
    body = response.json().get("user", response.json())
    identity = body.get("user_id") or body["id"]
    from app.services.permissions import get_user_node

    profile = await get_user_node(identity)
    auth = await AuthUser.get(profile.user_id)
    assert auth is not None
    previous = list(auth.roles)
    foreign = await second_user_client.get("/api/auth/me")
    subject = foreign.json().get("user", foreign.json())["id"]
    payload = await _bounded_policy_body(
        authenticated_admin_client,
        {"subject_kind": "human", "subject_id": subject, "actions": ["app.read"]},
    )
    assert (
        await authenticated_admin_client.post("/api/policies", json=payload)
    ).status_code == 201
    try:
        auth.roles = ["user"]
        await auth.save()
        denied = await authenticated_admin_client.post("/api/policies", json=payload)
        assert denied.status_code == 403, denied.text
    finally:
        auth.roles = previous
        await auth.save()


@pytest.mark.smoke
@pytest.mark.asyncio
async def test_owner_can_disable_legacy_unbounded_policy_without_implicit_adoption(
    authenticated_client,
):
    from app.services.permissions import get_user_node
    from app.services.policy_registry import create_policy, get_policy

    response = await authenticated_client.get("/api/auth/me")
    actor = response.json().get("user", response.json())
    owner = await get_user_node(actor.get("user_id") or actor["id"])
    subject = await _register_personal_agent(authenticated_client)
    legacy = await create_policy(
        subject_kind="agent",
        subject_id=subject,
        scope="*",
        actions=["*"],
        created_by=owner.id,
    )
    denied = await authenticated_client.patch(
        f"/api/policies/{legacy.id}", json={"is_active": True}
    )
    assert denied.status_code == 400, denied.text
    disabled = await authenticated_client.patch(
        f"/api/policies/{legacy.id}", json={"is_active": False}
    )
    assert disabled.status_code == 200, disabled.text
    current = await get_policy(legacy.id)
    assert (
        current.is_active is False
        and current.scope == "*"
        and current.created_by == owner.id
    )
