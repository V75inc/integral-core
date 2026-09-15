"""``POST /agentive/uplink/register`` (user JWT) may only register the
caller's personal facet or an org-facing facet for a workspace the caller
administers. ``scope="system"`` used to be accepted, letting any user
overwrite the singleton system AgentConfig.
"""

from __future__ import annotations

import pytest
from httpx import AsyncClient


@pytest.mark.smoke
@pytest.mark.asyncio
async def test_user_jwt_cannot_register_system_scope(
    authenticated_client: AsyncClient,
):
    """scope=system on the user route is a 400 and leaves the singleton intact."""
    from app.agentive.nodes import AgentConfig

    before = await AgentConfig.find_one(user_id="", scope="system", workspace_id=None)

    r = await authenticated_client.post(
        "/api/agentive/uplink/register",
        json={"scope": "system", "uplink_url": "http://evil.invalid", "persona": "x"},
    )
    assert r.status_code == 400, r.text

    after = await AgentConfig.find_one(user_id="", scope="system", workspace_id=None)
    if before is None:
        assert after is None
    else:
        assert after is not None and after.uplink_url == before.uplink_url


@pytest.mark.smoke
@pytest.mark.asyncio
async def test_org_facing_requires_workspace_admin_or_owner(
    authenticated_client: AsyncClient, second_user
):
    """org_facing needs a workspace the caller administers."""
    from app.services.personal_workspace import ensure_personal_workspace

    foreign_ws = await ensure_personal_workspace(second_user)

    r = await authenticated_client.post(
        "/api/agentive/uplink/register",
        json={"scope": "org_facing", "workspace_id": foreign_ws.id},
    )
    assert r.status_code == 403, r.text

    r = await authenticated_client.post(
        "/api/agentive/uplink/register", json={"scope": "org_facing"}
    )
    assert r.status_code == 400, r.text


@pytest.mark.smoke
@pytest.mark.asyncio
async def test_personal_and_owned_org_facing_still_register(
    authenticated_client: AsyncClient, test_user
):
    """Legitimate personal / owned org_facing registrations still work."""
    from app.services.personal_workspace import ensure_personal_workspace

    r = await authenticated_client.post(
        "/api/agentive/uplink/register", json={"scope": "personal"}
    )
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "registered"

    own_ws = await ensure_personal_workspace(test_user)
    r = await authenticated_client.post(
        "/api/agentive/uplink/register",
        json={"scope": "org_facing", "workspace_id": own_ws.id},
    )
    assert r.status_code == 200, r.text

    from app.agentive.nodes import AgentConfig

    cfg = await AgentConfig.get(r.json()["agent_config_id"])
    assert cfg is not None
    assert cfg.scope == "org_facing" and cfg.facet == "org_facing"
