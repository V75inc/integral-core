"""Owner review binds adoption to current target, recipient and membership."""

import pytest

from app.api.errors import InsufficientPermissionsError, ResourceConflictError
from app.exceptions import AuthorizationUnavailableError
from app.models.edges import COLLABORATES_ON, INVITED_TO, IS_MEMBER_OF
from app.models.nodes import Invitation, User
from app.services import invitations
from app.services.app_graph import catalog_user
from app.services.invitation_adoption import (
    adopt_invitation,
    preview_invitation_adoption,
)
from app.services.permissions import resolve_role
from app.services.workspace_governance import ordinary_authority_only
from tests.test_invitation_grant_outcomes import issue_resource, recipient_fixture

pytestmark = pytest.mark.smoke


async def adopt(owner, workspace, invitation, **changes):
    review = await preview_invitation_adoption(
        invitation.id, workspace.id, actor_id=owner.id
    )
    args = dict(
        actor_id=owner.id,
        review_digest=review["review_digest"],
        role="viewer",
        can_create_apps=False,
        can_create_tracks=False,
    )
    args.update(changes)
    return await adopt_invitation(invitation.id, workspace.id, **args)


@pytest.mark.asyncio
async def test_explicit_owner_adoption_rotates_token_and_does_not_grant():
    owner, workspace, track, recipient, email = await recipient_fixture()
    invitation, old_token, _ = await issue_resource(owner, workspace, track, email)
    context = await invitation.get_context()
    await context.database.find_one_and_update(
        "node",
        {"id": invitation.id},
        {
            "$unset": {
                "context.issuer_membership_id": "",
                "context.grant_baseline": "",
                "context.invitation_revision": "",
            }
        },
    )
    review = await preview_invitation_adoption(
        invitation.id, workspace.id, actor_id=owner.id
    )
    raw = await context.database.find_one("node", {"id": invitation.id})
    assert "grant_baseline" not in raw["context"]  # Preview is not migration.
    adopted, token, _ = await adopt_invitation(
        invitation.id,
        workspace.id,
        actor_id=owner.id,
        review_digest=review["review_digest"],
        role="viewer",
        can_create_apps=False,
        can_create_tracks=False,
    )
    assert adopted.invited_by_user_id == invitation.invited_by_user_id
    assert adopted.adopted_by_user_id == owner.id and adopted.invitation_revision == 1
    assert await resolve_role(recipient.id, "track", track.id) is None
    _, err = await invitations.consume_invitation_token(old_token, recipient.id)
    assert err == invitations.ERR_INVALID_TOKEN
    accepted, err = await invitations.consume_invitation_token(token, recipient.id)
    assert err is None and accepted.granted_role == "viewer"


@pytest.mark.asyncio
async def test_adoption_reconciles_proven_legacy_resource_only_workspace():
    owner, workspace, track, recipient, email = await recipient_fixture()
    invitation, _, _ = await issue_resource(owner, workspace, track, email)
    context = await invitation.get_context()
    for edge in await context.find_edges_between(
        invitation.id, workspace.id, edge_class=INVITED_TO
    ):
        await edge.delete()
    await context.database.find_one_and_update(
        "node", {"id": invitation.id}, {"$set": {"context.workspace_id": ""}}
    )
    adopted, token, _ = await adopt(owner, workspace, invitation)
    assert adopted.workspace_id == workspace.id
    assert (
        len(
            await context.find_edges_between(
                invitation.id, workspace.id, edge_class=INVITED_TO
            )
        )
        == 1
    )
    accepted, err = await invitations.consume_invitation_token(token, recipient.id)
    assert err is None and accepted.granted_role == "viewer"


@pytest.mark.asyncio
async def test_recipient_grant_change_invalidates_owner_review():
    owner, workspace, track, recipient, email = await recipient_fixture()
    invitation, _, _ = await issue_resource(owner, workspace, track, email)
    review = await preview_invitation_adoption(
        invitation.id, workspace.id, actor_id=owner.id
    )
    await recipient.connect(track, edge=COLLABORATES_ON, role="commenter")
    with pytest.raises(ResourceConflictError):
        await adopt(owner, workspace, invitation, review_digest=review["review_digest"])
    current = await Invitation.find_one({"id": invitation.id})
    assert current.adopted_by_user_id == "" and current.invitation_revision == 0


@pytest.mark.asyncio
async def test_admin_and_ordinary_app_mode_cannot_adopt():
    owner, workspace, track, _, email = await recipient_fixture()
    invitation, _, _ = await issue_resource(owner, workspace, track, email)
    admin = await User.create(display_name="Adoption admin")
    await catalog_user(admin)
    await admin.connect(workspace, edge=IS_MEMBER_OF, role="admin")
    with pytest.raises(InsufficientPermissionsError):
        await preview_invitation_adoption(
            invitation.id, workspace.id, actor_id=admin.id
        )
    with ordinary_authority_only(), pytest.raises(InsufficientPermissionsError):
        await preview_invitation_adoption(
            invitation.id, workspace.id, actor_id=owner.id
        )


@pytest.mark.asyncio
@pytest.mark.parametrize("terminal", ["accepted", "declined", "revoked", "expired"])
async def test_terminal_invitation_cannot_be_adopted(terminal):
    owner, workspace, track, _, email = await recipient_fixture()
    invitation, _, _ = await issue_resource(owner, workspace, track, email)
    invitation.status = terminal
    await invitation.save()
    with pytest.raises(ResourceConflictError):
        await preview_invitation_adoption(
            invitation.id, workspace.id, actor_id=owner.id
        )


@pytest.mark.asyncio
async def test_adopter_membership_replacement_invalidates_pending_adoption():
    owner, workspace, track, recipient, email = await recipient_fixture()
    invitation, _, _ = await issue_resource(owner, workspace, track, email)
    _, token, _ = await adopt(owner, workspace, invitation)
    context = await owner.get_context()
    for edge in await context.find_edges_between(
        owner.id, workspace.id, edge_class=IS_MEMBER_OF
    ):
        await edge.delete()
    await owner.connect(workspace, edge=IS_MEMBER_OF, role="owner")
    _, err = await invitations.consume_invitation_token(token, recipient.id)
    assert err == invitations.ERR_GRANT_FAILED
    assert await resolve_role(recipient.id, "track", track.id) is None


@pytest.mark.asyncio
async def test_additional_invitation_target_is_quarantined():
    owner, workspace, track, _, email = await recipient_fixture()
    invitation, _, _ = await issue_resource(owner, workspace, track, email)
    await invitation.connect(owner, edge=INVITED_TO)
    with pytest.raises(AuthorizationUnavailableError):
        await preview_invitation_adoption(
            invitation.id, workspace.id, actor_id=owner.id
        )


@pytest.mark.asyncio
async def test_pre_rotation_token_snapshot_cannot_accept_after_adoption():
    owner, workspace, track, recipient, email = await recipient_fixture()
    invitation, _, _ = await issue_resource(owner, workspace, track, email)
    old_hash = invitation.token_hash
    await adopt(owner, workspace, invitation)
    _, err = await invitations._finalize_invitation_acceptance(
        invitation=invitation,
        user=recipient,
        accepting_user_id=recipient.id,
        expected_token_hash=old_hash,
    )
    assert err == invitations.ERR_INVALID_TOKEN
    assert await resolve_role(recipient.id, "track", track.id) is None


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "field", ["adopted_by_user_id", "adopted_membership_id", "adopted_at"]
)
async def test_partial_adoption_provenance_cannot_grant(field):
    owner, workspace, track, recipient, email = await recipient_fixture()
    invitation, _, _ = await issue_resource(owner, workspace, track, email)
    adopted, token, _ = await adopt(owner, workspace, invitation)
    context = await adopted.get_context()
    await context.database.find_one_and_update(
        "node", {"id": adopted.id}, {"$unset": {f"context.{field}": ""}}
    )
    _, err = await invitations.consume_invitation_token(token, recipient.id)
    assert err == invitations.ERR_GRANT_FAILED
    assert await resolve_role(recipient.id, "track", track.id) is None
