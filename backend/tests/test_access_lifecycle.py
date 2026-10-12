"""Lifecycle assertions inspect persisted ownership, denies and independent sources."""

import uuid

import pytest
from jvspatial.api.auth.models import User as AuthUser
from jvspatial.core.context import get_default_context

from app.api.errors import (
    BadRequestError,
    InsufficientPermissionsError,
    ResourceConflictError,
)
from app.models.access_lifecycle import SHARE_GRANT
from app.models.edges import COLLABORATES_ON, IS_MEMBER_OF, OWNS
from app.models.nodes import User
from app.services.app_graph import catalog_user
from app.services.ownership_consent import request_ownership_transfer
from app.services.permissions import resolve_role
from app.services.share_link_lifecycle import redeem_source_link, revoke_source_grant
from app.services.share_links import mint_share_link
from app.services.workspace_permissions import can_access_workspace
from app.services.workspace_suspension import (
    set_workspace_suspension,
    workspace_suspended,
)
from tests.test_resource_location import graph

pytestmark = [pytest.mark.asyncio, pytest.mark.smoke]


async def verified_subject(workspace, role="member"):
    email = f"lifecycle-{uuid.uuid4().hex}@example.com"
    auth = await AuthUser.create(email=email, password_hash="fixture")
    user = await User.create(
        user_id=auth.id,
        display_name="Lifecycle QA",
        email_verified=True,
        verified_email=email,
    )
    await catalog_user(user)
    if role:
        await user.connect(workspace, edge=IS_MEMBER_OF, role=role)
    return user


async def test_suspension_is_retained_and_owner_only():
    owner, workspace, app, _, _ = await graph()
    member = await verified_subject(workspace)
    await member.connect(app, edge=COLLABORATES_ON, role="editor")
    before = await resolve_role(member.id, "app", app.id)
    assert before == "editor"
    suspended = await set_workspace_suspension(
        actor_id=owner.id,
        workspace_id=workspace.id,
        member_id=member.id,
        suspended=True,
    )
    assert suspended["changed"] is True and suspended["revision"] == 1
    assert await workspace_suspended(member.id, workspace.id)
    assert await can_access_workspace(member.id, workspace.id) == "none"
    assert await resolve_role(member.id, "app", app.id) is None
    context = get_default_context()
    for edge in await context.find_edges_between(
        member.id, workspace.id, edge_class=IS_MEMBER_OF
    ):
        await edge.delete()
    assert await workspace_suspended(member.id, workspace.id)
    from app.services.workspace_suspension import review_workspace_suspension

    review = await review_workspace_suspension(
        actor_id=owner.id, workspace_id=workspace.id, member_id=member.id
    )
    resumed = await set_workspace_suspension(
        actor_id=owner.id,
        workspace_id=workspace.id,
        member_id=member.id,
        suspended=False,
        expected_revision=review["revision"],
        reviewed_access_digest=review["reviewed_access_digest"],
    )
    assert (
        resumed["revision"] == 2
        and await can_access_workspace(member.id, workspace.id) == "none"
    )


async def test_admin_and_owner_self_suspension_denied():
    owner, workspace, _, _, _ = await graph()
    admin = await verified_subject(workspace, "admin")
    member = await verified_subject(workspace)
    with pytest.raises(InsufficientPermissionsError):
        await set_workspace_suspension(
            actor_id=admin.id,
            workspace_id=workspace.id,
            member_id=member.id,
            suspended=True,
        )
    with pytest.raises(BadRequestError):
        await set_workspace_suspension(
            actor_id=owner.id,
            workspace_id=workspace.id,
            member_id=owner.id,
            suspended=True,
        )
    assert not await workspace_suspended(member.id, workspace.id)
    assert not await workspace_suspended(owner.id, workspace.id)


async def test_link_sources_preserve_direct_grant_and_replay_capacity():
    owner, workspace, app, _, _ = await graph()
    member = await verified_subject(workspace, "guest")
    await member.connect(app, edge=COLLABORATES_ON, role="viewer")
    minted = await mint_share_link(owner.id, "app", app.id, role="editor")
    link_id = minted["share_link"]["id"]
    first = await redeem_source_link(member.id, minted["token"])
    retry = await redeem_source_link(member.id, minted["token"])
    assert first["outcome"] == "created" and first["effective_role"] == "editor"
    assert retry["outcome"] == "unchanged" and retry["grant_id"] == first["grant_id"]
    row = await get_default_context().database.get("node", link_id)
    assert row["context"]["redemptions"] == 1
    removed = await revoke_source_grant(
        actor_id=owner.id, link_id=link_id, recipient_id=member.id
    )
    assert removed["effective_role"] == "viewer"
    assert await resolve_role(member.id, "app", app.id) == "viewer"
    with pytest.raises(ResourceConflictError):
        await redeem_source_link(member.id, minted["token"])
    assert (await get_default_context().database.get("node", link_id))["context"][
        "redemptions"
    ] == 1


async def test_suspension_blocks_link_reentry_without_capacity_consumption():
    owner, workspace, app, _, _ = await graph()
    recipient = await verified_subject(workspace, None)
    minted = await mint_share_link(owner.id, "app", app.id, role="viewer")
    await set_workspace_suspension(
        actor_id=owner.id,
        workspace_id=workspace.id,
        member_id=recipient.id,
        suspended=True,
    )
    with pytest.raises(InsufficientPermissionsError):
        await redeem_source_link(recipient.id, minted["token"])
    assert not await get_default_context().database.find(
        "edge", {"target": recipient.id, "entity": SHARE_GRANT.__name__}
    )
    assert (
        await get_default_context().database.get("node", minted["share_link"]["id"])
    )["context"]["redemptions"] == 0


async def test_ownership_request_does_not_change_owner_and_requires_verified_member():
    owner, workspace, app, _, _ = await graph()
    member = await verified_subject(workspace)
    proposal = await request_ownership_transfer(
        actor_id=owner.id,
        resource_type="app",
        resource_id=app.id,
        recipient_id=member.id,
    )
    assert proposal["status"] == "pending"
    rows = await get_default_context().database.find(
        "edge", {"entity": OWNS.__name__, "target": app.id}
    )
    assert len(rows) == 1 and rows[0]["source"] == owner.id
    member.email_verified = False
    await member.save()
    with pytest.raises(BadRequestError):
        await request_ownership_transfer(
            actor_id=owner.id,
            resource_type="app",
            resource_id=app.id,
            recipient_id=member.id,
        )


async def test_reviewed_resume_refuses_stale_rights_and_never_restores_revoked_grant():
    from app.services.sharing import remove_collaborator
    from app.services.workspace_suspension import review_workspace_suspension

    owner, workspace, app, _, _ = await graph()
    app.visibility = "private"
    await app.save()
    member = await verified_subject(workspace)
    await member.connect(app, edge=COLLABORATES_ON, role="editor")
    await set_workspace_suspension(
        actor_id=owner.id,
        workspace_id=workspace.id,
        member_id=member.id,
        suspended=True,
    )
    old_review = await review_workspace_suspension(
        actor_id=owner.id, workspace_id=workspace.id, member_id=member.id
    )
    await remove_collaborator(owner.id, "app", app.id, member.id)
    with pytest.raises(ResourceConflictError):
        await set_workspace_suspension(
            actor_id=owner.id,
            workspace_id=workspace.id,
            member_id=member.id,
            suspended=False,
            expected_revision=old_review["revision"],
            reviewed_access_digest=old_review["reviewed_access_digest"],
        )
    current = await review_workspace_suspension(
        actor_id=owner.id, workspace_id=workspace.id, member_id=member.id
    )
    await set_workspace_suspension(
        actor_id=owner.id,
        workspace_id=workspace.id,
        member_id=member.id,
        suspended=False,
        expected_revision=current["revision"],
        reviewed_access_digest=current["reviewed_access_digest"],
    )
    assert not await get_default_context().database.find(
        "edge",
        {"source": member.id, "target": app.id, "entity": COLLABORATES_ON.__name__},
    )
    assert await resolve_role(member.id, "app", app.id) is None


async def test_absolute_subtree_block_beats_direct_and_link_grants_and_reentry():
    from app.services.resource_access_blocks import set_resource_block

    owner, workspace, app, track, entry = await graph()
    member = await verified_subject(workspace)
    await member.connect(entry, edge=COLLABORATES_ON, role="editor")
    minted = await mint_share_link(owner.id, "app", app.id, role="editor")
    await redeem_source_link(member.id, minted["token"])
    assert await resolve_role(member.id, "entry", entry.id) == "editor"
    blocked = await set_resource_block(
        actor_id=owner.id,
        resource_type="app",
        resource_id=app.id,
        subject_id=member.id,
        blocked=True,
    )
    assert blocked["effective_role"] is None
    assert await resolve_role(member.id, "track", track.id) is None
    assert await resolve_role(member.id, "entry", entry.id) is None
    with pytest.raises(InsufficientPermissionsError):
        await redeem_source_link(member.id, minted["token"])
    await set_resource_block(
        actor_id=owner.id,
        resource_type="app",
        resource_id=app.id,
        subject_id=member.id,
        blocked=False,
    )
    assert await resolve_role(member.id, "entry", entry.id) == "editor"


async def test_absolute_block_cannot_target_canonical_owner():
    from app.services.resource_access_blocks import set_resource_block

    owner, _, app, _, _ = await graph()
    with pytest.raises(BadRequestError):
        await set_resource_block(
            actor_id=owner.id,
            resource_type="app",
            resource_id=app.id,
            subject_id=owner.id,
            blocked=True,
        )


async def test_corrupt_source_scope_cannot_disappear_from_reader_or_inventory():
    from app.exceptions import AuthorizationUnavailableError
    from app.services.share_grant_sources import share_grant_roles, share_grant_targets

    owner, workspace, app, _, _ = await graph()
    member = await verified_subject(workspace, "guest")
    minted = await mint_share_link(owner.id, "app", app.id, role="viewer")
    receipt = await redeem_source_link(member.id, minted["token"])
    await get_default_context().database.find_one_and_update(
        "edge", {"id": receipt["grant_id"]}, {"$unset": {"context.resource_id": ""}}
    )
    with pytest.raises(AuthorizationUnavailableError):
        await share_grant_roles(member.id, "app", app.id)
    with pytest.raises(AuthorizationUnavailableError):
        await share_grant_targets(member.id, "app")


async def test_inactive_workspace_owner_remains_protected_from_retained_block():
    from app.models.access_lifecycle import RESOURCE_ACCESS_BLOCK
    from app.services.resource_access_blocks import set_resource_block

    actor, workspace, app, _, _ = await graph()
    protected = await verified_subject(workspace, "owner")
    context = get_default_context()
    old = await context.find_edges_between(
        actor.id, workspace.id, edge_class=IS_MEMBER_OF
    )
    old[0].role = "member"
    await old[0].save()
    changed = await context.database.find_one_and_update(
        "object",
        {"id": protected.user_id, "entity": AuthUser._entity_name()},
        {"$set": {"context.is_active": False}},
    )
    assert changed is not None
    with pytest.raises(BadRequestError):
        await set_resource_block(
            actor_id=actor.id,
            resource_type="app",
            resource_id=app.id,
            subject_id=protected.id,
            blocked=True,
        )
    assert not await context.database.find(
        "edge", {"source": protected.id, "entity": RESOURCE_ACCESS_BLOCK.__name__}
    )


async def test_owner_review_is_concrete_and_resume_retry_is_idempotent():
    from app.services.workspace_suspension import review_workspace_suspension

    owner, workspace, app, _, _ = await graph()
    member = await verified_subject(workspace)
    await member.connect(app, edge=COLLABORATES_ON, role="viewer")
    review = await review_workspace_suspension(
        actor_id=owner.id, workspace_id=workspace.id, member_id=member.id
    )
    assert review["state"] == "active" and review["revision"] == 0
    assert review["retained_access"][0]["resource_id"] == app.id
    await set_workspace_suspension(
        actor_id=owner.id,
        workspace_id=workspace.id,
        member_id=member.id,
        suspended=True,
    )
    review = await review_workspace_suspension(
        actor_id=owner.id, workspace_id=workspace.id, member_id=member.id
    )
    command = dict(
        actor_id=owner.id,
        workspace_id=workspace.id,
        member_id=member.id,
        suspended=False,
        expected_revision=review["revision"],
        reviewed_access_digest=review["reviewed_access_digest"],
    )
    first = await set_workspace_suspension(**command)
    retry = await set_workspace_suspension(**command)
    assert first["revision"] == retry["revision"] == 2
    assert retry["changed"] is False
    assert await resolve_role(member.id, "app", app.id) == "viewer"


async def test_proposal_inbox_is_private_and_owner_inventory_is_bounded():
    from app.services.ownership_consent import list_ownership_proposals

    owner, workspace, app, _, _ = await graph()
    recipient = await verified_subject(workspace)
    outsider = await verified_subject(workspace)
    issued = await request_ownership_transfer(
        actor_id=owner.id,
        resource_type="app",
        resource_id=app.id,
        recipient_id=recipient.id,
    )
    assert (await list_ownership_proposals(actor_id=outsider.id))["proposals"] == []
    with pytest.raises(InsufficientPermissionsError):
        await list_ownership_proposals(actor_id=outsider.id, workspace_id=workspace.id)
    inbox = await list_ownership_proposals(actor_id=recipient.id)
    managed = await list_ownership_proposals(
        actor_id=owner.id, workspace_id=workspace.id
    )
    assert inbox["complete"] and managed["complete"]
    assert inbox["proposals"][0]["proposal_id"] == issued["proposal_id"]
    assert managed["proposals"][0]["ownership_current"] is True
    assert await resolve_role(owner.id, "app", app.id) == "owner"


async def test_review_never_discloses_foreign_workspace_resources():
    """Owner sees only this workspace even when retained grants exist elsewhere."""
    from app.services.workspace_suspension import review_workspace_suspension

    owner, workspace, app, _, _ = await graph()
    _, foreign_workspace, foreign_app, _, _ = await graph()
    member = await verified_subject(workspace)
    await member.connect(foreign_workspace, edge=IS_MEMBER_OF, role="member")
    await member.connect(app, edge=COLLABORATES_ON, role="viewer")
    await member.connect(foreign_app, edge=COLLABORATES_ON, role="editor")
    review = await review_workspace_suspension(
        actor_id=owner.id, workspace_id=workspace.id, member_id=member.id
    )
    assert {item["resource_id"] for item in review["retained_access"]} == {app.id}
    assert foreign_app.id not in str(review)
    assert foreign_workspace.id not in str(review)


async def test_workspace_cascade_removes_rooted_consent_proposals():
    """Workspace removal includes consent sidecars in its final raw inventory."""
    from app.models.access_lifecycle import OwnershipProposal
    from app.services.workspace_lifecycle import delete_workspace_cascade

    owner, workspace, _, _, _ = await graph()
    recipient = await verified_subject(workspace)
    issued = await request_ownership_transfer(
        actor_id=owner.id,
        resource_type="workspace",
        resource_id=workspace.id,
        recipient_id=recipient.id,
    )
    await delete_workspace_cascade(workspace, cascade=True, actor_id=owner.id)
    database = get_default_context().database
    assert await database.get("node", workspace.id) is None
    assert await database.get("node", issued["proposal_id"]) is None
    assert await OwnershipProposal.find_one({"id": issued["proposal_id"]}) is None


async def test_model_delete_denies_retained_external_consumer_without_effects():
    """A shared schema remains rooted when another App still consumes it."""
    from app.models.edges import HAS_OPERATIONAL_MODEL
    from app.services.app_deletion import delete_operational_model_subtree
    from app.services.app_graph import get_app_attached_operational_model

    _, _, app, _, _ = await graph()
    _, _, external, _, _ = await graph()
    model = await get_app_attached_operational_model(app)
    await external.connect(model, edge=HAS_OPERATIONAL_MODEL)
    with pytest.raises(ResourceConflictError):
        await delete_operational_model_subtree(
            model, allowed_consumer_ids=frozenset({app.id})
        )
    assert await get_default_context().database.get("node", model.id) is not None
    edges = await get_default_context().find_edges_between(
        external.id, model.id, edge_class=HAS_OPERATIONAL_MODEL
    )
    assert len(edges) == 1
