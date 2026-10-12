"""Subject administration is bounded by current issuer and canonical targets."""

import pytest

from app.agentive.edges import HAS_ORG_AGENT
from app.agentive.nodes import AgentConfig
from app.models.edges import COLLABORATES_ON, IS_MEMBER_OF
from app.models.nodes import User
from app.schemas.policy import Resource, Subject
from app.services.app_graph import catalog_user
from app.services.policy_registry import create_policy
from app.services.service_delegation import (
    delegated_principal_ids,
    delegated_subject_record,
    policy_delegation_allowed,
)
from tests.test_resource_location import graph

pytestmark = [pytest.mark.asyncio, pytest.mark.smoke]


async def test_legacy_scoped_wildcard_is_intersected_at_exact_current_target():
    owner, workspace, app, _, _ = await graph()
    agent = await AgentConfig.create(
        scope="org_facing",
        facet="org_facing",
        workspace_id=workspace.id,
        is_active=True,
    )
    await workspace.connect(agent, edge=HAS_ORG_AGENT)
    policy = await create_policy(
        subject_kind="agent",
        subject_id=agent.id,
        scope="*",
        actions=["app.*"],
        created_by=owner.id,
    )
    subject = Subject(kind="agent", id=agent.id)
    resource = Resource(kind="app", id=app.id, scope=f"app:{app.id}")
    assert await policy_delegation_allowed(
        policy=policy, subject=subject, action="app.read", resource=resource
    )
    assert await delegated_principal_ids(subject) == (owner.id,)
    assert await delegated_subject_record(subject) == (AgentConfig, "node", agent.id)
    _, _, foreign, _, _ = await graph()
    assert not await policy_delegation_allowed(
        policy=policy,
        subject=subject,
        action="app.read",
        resource=Resource(kind="app", id=foreign.id, scope=f"app:{foreign.id}"),
    )
    context = await owner.get_context()
    for edge in await context.find_edges_between(
        owner.id, workspace.id, edge_class=IS_MEMBER_OF
    ):
        await edge.delete()
    assert not await policy_delegation_allowed(
        policy=policy, subject=subject, action="app.read", resource=resource
    )


async def test_subject_ownership_does_not_give_issuer_foreign_resource_authority():
    owner, workspace, app, _, _ = await graph()
    delegate = await User.create(display_name="Subject admin")
    await catalog_user(delegate)
    await delegate.connect(workspace, edge=IS_MEMBER_OF, role="member")
    agent = await AgentConfig.create(
        scope="org_facing",
        facet="org_facing",
        workspace_id=workspace.id,
        is_active=True,
    )
    await workspace.connect(agent, edge=HAS_ORG_AGENT)
    policy = await create_policy(
        subject_kind="agent",
        subject_id=agent.id,
        scope=f"app:{app.id}",
        actions=["app.read"],
        created_by=delegate.id,
    )
    arguments = dict(
        policy=policy,
        subject=Subject(kind="agent", id=agent.id),
        action="app.read",
        resource=Resource(kind="app", id=app.id, scope=f"app:{app.id}"),
    )
    assert not await policy_delegation_allowed(**arguments)
    await delegate.connect(app, edge=COLLABORATES_ON, role="viewer")
    assert await policy_delegation_allowed(**arguments)


async def test_namespaced_service_action_retains_current_delegator_conditions_and_role():
    from app.services.access_delegation import create_binding, revoke_binding
    from app.services.service_delegation import validate_policy_issuance
    from tests.test_app_action_delegation import fixture

    owner, issuer, workspace, app, _, entry, _ = await fixture()
    agent = await AgentConfig.create(
        scope="org_facing",
        facet="org_facing",
        workspace_id=workspace.id,
        is_active=True,
    )
    await workspace.connect(agent, edge=HAS_ORG_AGENT)
    action = f"app:{app.id}:approve"
    binding = await create_binding(
        actor_id=owner.id,
        app_id=app.id,
        subject_kind="human",
        subject_id=issuer.id,
        role="reviewer",
        conditions=[
            {"attribute": "entry.status", "operator": "eq", "value": "pending"}
        ],
    )
    await validate_policy_issuance(
        issuer_id=issuer.id,
        scope=f"app:{app.id}",
        actions=[action],
        subject=Subject(kind="agent", id=agent.id),
    )
    policy = await create_policy(
        subject_kind="agent",
        subject_id=agent.id,
        scope=f"app:{app.id}",
        actions=[action],
        created_by=issuer.id,
    )
    args = dict(
        policy=policy,
        subject=Subject(kind="agent", id=agent.id),
        action=action,
        resource=Resource(kind="app", id=app.id, scope=f"app:{app.id}"),
        payload={"entry_id": entry.id},
    )
    entry.status = "pending"
    await entry.save()
    assert await policy_delegation_allowed(**args)
    entry.status = "closed"
    await entry.save()
    assert not await policy_delegation_allowed(**args)
    entry.status = "pending"
    await entry.save()
    await revoke_binding(actor_id=owner.id, binding_id=binding.id)
    assert not await policy_delegation_allowed(**args)


@pytest.mark.parametrize("narrowing", ["actions", "scope", "filters", "missing-scope"])
async def test_primary_policy_narrowing_cannot_reuse_stale_hydrated_grant(narrowing):
    from app.exceptions import AuthorizationUnavailableError
    from app.services.policy_registry import list_policies_for_subject

    owner, workspace, app, _, _ = await graph()
    agent = await AgentConfig.create(
        scope="org_facing",
        facet="org_facing",
        workspace_id=workspace.id,
        is_active=True,
    )
    await workspace.connect(agent, edge=HAS_ORG_AGENT)
    policy = await create_policy(
        subject_kind="agent",
        subject_id=agent.id,
        scope=f"app:{app.id}",
        actions=["app.read"],
        created_by=owner.id,
    )
    subject = Subject(kind="agent", id=agent.id)
    resource = Resource(kind="app", id=app.id, scope=f"app:{app.id}")
    args = dict(policy=policy, subject=subject, action="app.read", resource=resource)
    assert await policy_delegation_allowed(**args)
    context = await owner.get_context()
    update = (
        {"$set": {"context.actions": ["entry.read"]}}
        if narrowing == "actions"
        else (
            {"$set": {"context.scope": "app:unrelated"}}
            if narrowing == "scope"
            else (
                {"$set": {"context.tags": ["restricted"]}}
                if narrowing == "filters"
                else {"$unset": {"context.scope": ""}}
            )
        )
    )
    await context.database.find_one_and_update("node", {"id": policy.id}, update)
    if narrowing == "missing-scope":
        with pytest.raises(AuthorizationUnavailableError):
            await policy_delegation_allowed(**args)
        with pytest.raises(AuthorizationUnavailableError):
            await list_policies_for_subject("agent", agent.id)
        await context.database.find_one_and_update(
            "node", {"id": policy.id}, {"$set": {"context.is_active": False}}
        )
        assert not await policy_delegation_allowed(**args)
        assert await list_policies_for_subject("agent", agent.id) == []
    else:
        assert not await policy_delegation_allowed(**args)
