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


async def _register_personal_agent(client: AsyncClient) -> str:
    r = await client.post(
        "/api/agentive/uplink/register",
        json={"scope": "personal", "uplink_url": "http://127.0.0.1:9"},
    )
    assert r.status_code == 200, r.text
    return r.json()["agent_config_id"]


async def _create_connector(client: AsyncClient) -> str:
    r = await client.post("/api/agentive/connectors", json={"kind": "jvagent"})
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
        json={"subject_kind": "agent", "subject_id": agent_id, "actions": ["*"]},
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
        json={
            "subject_kind": "agent",
            "subject_id": agent_id,
            "actions": ["entry.read"],
        },
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
        json={"subject_kind": "connector", "subject_id": connector_id},
    )
    assert r.status_code == 403, r.text

    r = await second_user_client.post(
        "/api/policies",
        json={"subject_kind": "connector", "subject_id": connector_id},
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
        "/api/policies", json={"subject_kind": "human", "subject_id": caller_id}
    )
    assert r.status_code == 403, r.text
    # Nor for an agent id that resolves to no node.
    r = await authenticated_client.post(
        "/api/policies", json={"subject_kind": "agent", "subject_id": "a-ghost"}
    )
    assert r.status_code == 403, r.text

    r = await authenticated_admin_client.post(
        "/api/policies", json={"subject_kind": "human", "subject_id": caller_id}
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
        "/api/policies", json={"subject_kind": "agent", "subject_id": mine}
    )
    assert r.status_code == 201, r.text
    r = await second_user_client.post(
        "/api/policies", json={"subject_kind": "agent", "subject_id": theirs}
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
