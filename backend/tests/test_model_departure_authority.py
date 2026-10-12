"""Workspace departure cleans current Model grants without crossing scope."""

import uuid

import bcrypt
import pytest
from jvspatial.api.auth.models import User as AuthUser

from app.models.edges import COLLABORATES_ON, IS_MEMBER_OF
from app.schemas.policy import Resource
from app.services.library_model_creation import create_private_library_model
from app.services.model_authority import model_action_authorized
from app.services.workspace_departure import remove_workspace_membership
from tests.test_resource_location import graph as rooted_graph

pytestmark = pytest.mark.smoke


async def graph():
    """Bind rooted departure subjects to genuine current primary identities."""
    owner, workspace, app, track, entry = await rooted_graph()
    email = f"model-departure-{uuid.uuid4().hex}@example.com"
    identity = await AuthUser.create(
        email=email,
        password_hash=bcrypt.hashpw(
            b"model-departure-test-password", bcrypt.gensalt()
        ).decode(),
    )
    owner.user_id = identity.id
    owner.email_verified = True
    owner.verified_email = email
    await owner.save()
    return owner, workspace, app, track, entry


async def private_model(owner, workspace):
    return await create_private_library_model(
        user_id=owner.id,
        workspace_id=workspace.id,
        name="Departure library",
        scope="track",
    )


def model_resource(model):
    return Resource(
        kind="operational_model", id=model.id, scope=f"operational_model:{model.id}"
    )


@pytest.mark.asyncio
async def test_departure_removes_private_model_grant_before_guest_reentry():
    owner, workspace, _, _, _ = await graph()
    curator, _, _, _, _ = await graph()
    await curator.connect(workspace, edge=IS_MEMBER_OF, role="guest")
    model = await private_model(owner, workspace)
    await curator.connect(model, edge=COLLABORATES_ON, role="admin")
    assert await model_action_authorized(
        curator.id, "operational_model.update", model_resource(model)
    )
    assert await remove_workspace_membership(
        member=curator,
        workspace_id=workspace.id,
        actor_id=curator.id,
        clear_active_workspace=True,
    )
    context = await curator.get_context()
    assert (
        await context.find_edges_between(
            curator.id, model.id, edge_class=COLLABORATES_ON
        )
        == []
    )
    assert not await model_action_authorized(
        curator.id, "operational_model.update", model_resource(model)
    )
    await curator.connect(workspace, edge=IS_MEMBER_OF, role="guest")
    assert not await model_action_authorized(
        curator.id, "operational_model.update", model_resource(model)
    )


@pytest.mark.asyncio
async def test_departure_preserves_foreign_workspace_model_grant():
    owner, workspace, _, _, _ = await graph()
    foreign_owner, foreign_workspace, _, _, _ = await graph()
    curator, _, _, _, _ = await graph()
    for destination in (workspace, foreign_workspace):
        await curator.connect(destination, edge=IS_MEMBER_OF, role="guest")
    own = await private_model(owner, workspace)
    foreign = await private_model(foreign_owner, foreign_workspace)
    for model in (own, foreign):
        await curator.connect(model, edge=COLLABORATES_ON, role="admin")
    assert await remove_workspace_membership(
        member=curator,
        workspace_id=workspace.id,
        actor_id=curator.id,
        clear_active_workspace=True,
    )
    assert await model_action_authorized(
        curator.id, "operational_model.update", model_resource(foreign)
    )
    assert not await model_action_authorized(
        curator.id, "operational_model.update", model_resource(own)
    )


@pytest.mark.asyncio
async def test_unresolved_model_grant_keeps_membership_and_other_grants():
    owner, workspace, _, track, _ = await graph()
    curator, _, _, _, _ = await graph()
    await curator.connect(workspace, edge=IS_MEMBER_OF, role="guest")
    model = await private_model(owner, workspace)
    await curator.connect(model, edge=COLLABORATES_ON, role="admin")
    await curator.connect(track, edge=COLLABORATES_ON, role="editor")
    model.workspace_id = None
    await model.save()
    with pytest.raises(RuntimeError, match="Workspace grant traversal failed"):
        await remove_workspace_membership(
            member=curator,
            workspace_id=workspace.id,
            actor_id=curator.id,
            clear_active_workspace=True,
        )
    context = await curator.get_context()
    for target, edge in (
        (workspace, IS_MEMBER_OF),
        (model, COLLABORATES_ON),
        (track, COLLABORATES_ON),
    ):
        assert (
            len(
                await context.find_edges_between(curator.id, target.id, edge_class=edge)
            )
            == 1
        )


@pytest.mark.asyncio
async def test_departure_removes_named_attached_draft_reviewer_grant():
    from app.services.model_authority import get_current_model
    from app.services.operational_model_atomic_swap import fork_draft

    owner, workspace, _, track, _ = await graph()
    reviewer, _, _, _, _ = await graph()
    await reviewer.connect(workspace, edge=IS_MEMBER_OF, role="guest")
    published = await get_current_model(track.attached_operational_model_id)
    assert published is not None
    draft = await fork_draft(published=published, actor_id=owner.id)
    await reviewer.connect(draft, edge=COLLABORATES_ON, role="viewer")
    assert await model_action_authorized(
        reviewer.id, "operational_model.read", model_resource(draft)
    )
    assert await remove_workspace_membership(
        member=reviewer,
        workspace_id=workspace.id,
        actor_id=reviewer.id,
        clear_active_workspace=True,
    )
    await reviewer.connect(workspace, edge=IS_MEMBER_OF, role="guest")
    assert not await model_action_authorized(
        reviewer.id, "operational_model.read", model_resource(draft)
    )
