"""Invitation service callers cannot mint owner-only workspace rights."""

from unittest.mock import AsyncMock

import pytest

from app.api.errors import InsufficientPermissionsError
from app.api.invitations import post_create_invitation
from app.models.edges import IS_MEMBER_OF
from app.models.nodes import Invitation
from app.services import invitations
from app.services.invitation_authority import require_workspace_invitation_authority
from tests.test_model_api_authority import request_for
from tests.test_resource_location import graph

pytestmark = pytest.mark.smoke


@pytest.mark.asyncio
@pytest.mark.parametrize("own_creation_flags", [False, True])
@pytest.mark.parametrize(
    "role,apps,tracks",
    [("admin", False, False), ("member", True, False), ("guest", False, True)],
)
@pytest.mark.parametrize("surface", ["service", "handler"])
async def test_admin_cannot_delegate_owner_only_rights(
    monkeypatch, own_creation_flags, role, apps, tracks, surface
):
    _, workspace, _, _, _ = await graph()
    admin, _, _, _, _ = await graph()
    await admin.connect(
        workspace,
        edge=IS_MEMBER_OF,
        role="admin",
        can_create_apps=own_creation_flags,
        can_create_tracks=own_creation_flags,
    )
    email = "delegation-ceiling@example.com"
    delivery = AsyncMock()
    monkeypatch.setattr("app.services.email_delivery.enqueue_email", delivery)
    with pytest.raises(InsufficientPermissionsError):
        if surface == "service":
            await invitations.create_invitation(
                workspace=workspace,
                inviter_user_id=admin.id,
                email=email,
                role=role,
                can_create_apps=apps,
                can_create_tracks=tracks,
            )
        else:
            await post_create_invitation(
                request_for(admin.id, workspace.id),
                workspace.id,
                email=email,
                role=role,
                can_create_apps=apps,
                can_create_tracks=tracks,
            )
    assert await Invitation.find({"context.email": email}) == []
    delivery.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("role", ["member", "guest"])
async def test_admin_can_issue_basic_membership(role):
    _, workspace, _, _, _ = await graph()
    admin, _, _, _, _ = await graph()
    await admin.connect(workspace, edge=IS_MEMBER_OF, role="admin")
    issued, _, _ = await invitations.create_invitation(
        workspace=workspace,
        inviter_user_id=admin.id,
        email="basic-membership@example.com",
        role=role,
        send_email_notification=False,
    )
    assert issued.role == role
    assert issued.invited_by_user_id == admin.id
    assert issued.can_create_apps is False and issued.can_create_tracks is False


@pytest.mark.asyncio
async def test_owner_without_creation_flags_can_delegate_creation_and_admin():
    owner, workspace, _, _, _ = await graph()
    issued, _, _ = await invitations.create_invitation(
        workspace=workspace,
        inviter_user_id=owner.id,
        email="owner-delegation@example.com",
        role="admin",
        can_create_apps=True,
        can_create_tracks=True,
        send_email_notification=False,
    )
    assert issued.role == "admin"
    assert issued.can_create_apps is True and issued.can_create_tracks is True


@pytest.mark.asyncio
@pytest.mark.parametrize("role", ["member", "guest", "outsider"])
async def test_nonadministrator_service_caller_cannot_issue(role):
    _, workspace, _, _, _ = await graph()
    issuer, _, _, _, _ = await graph()
    if role != "outsider":
        await issuer.connect(workspace, edge=IS_MEMBER_OF, role=role)
    with pytest.raises(InsufficientPermissionsError):
        await require_workspace_invitation_authority(
            issuer.id,
            workspace.id,
            role="member",
            can_create_apps=False,
            can_create_tracks=False,
        )


@pytest.mark.asyncio
@pytest.mark.parametrize("flag", ["true", "false", 0, 1, None])
async def test_service_rejects_coerced_creation_flags(flag):
    owner, workspace, _, _, _ = await graph()
    with pytest.raises(ValueError, match="invalid_creation_flags"):
        await require_workspace_invitation_authority(
            owner.id,
            workspace.id,
            role="member",
            can_create_apps=flag,
            can_create_tracks=False,
        )


@pytest.mark.asyncio
async def test_warmed_admin_authority_does_not_survive_membership_removal():
    from app.services.workspace_permissions import can_access_workspace

    _, workspace, _, _, _ = await graph()
    issuer, _, _, _, _ = await graph()
    membership = await issuer.connect(workspace, edge=IS_MEMBER_OF, role="admin")
    assert await can_access_workspace(issuer.id, workspace.id) == "admin"
    await membership.delete()
    with pytest.raises(InsufficientPermissionsError):
        await require_workspace_invitation_authority(
            issuer.id,
            workspace.id,
            role="member",
            can_create_apps=False,
            can_create_tracks=False,
        )


@pytest.mark.asyncio
async def test_ordinary_app_mode_cannot_consume_protected_owner_delegation():
    from app.services.workspace_governance import ordinary_authority_only

    owner, workspace, _, _, _ = await graph()
    with ordinary_authority_only(), pytest.raises(InsufficientPermissionsError):
        await require_workspace_invitation_authority(
            owner.id,
            workspace.id,
            role="admin",
            can_create_apps=True,
            can_create_tracks=False,
        )


@pytest.mark.asyncio
@pytest.mark.parametrize("target_type", ["app", "track", "entry"])
async def test_resource_service_rejects_wrong_workspace_before_issuance(target_type):
    owner, _, app, track, entry = await graph()
    _, other, _, _, _ = await graph()
    target = {"app": app, "track": track, "entry": entry}[target_type]
    with pytest.raises(InsufficientPermissionsError):
        await invitations.create_resource_invitation(
            resource_type=target_type,
            resource_id=target.id,
            workspace_id=other.id,
            inviter_user_id=owner.id,
            email="wrong-workspace@example.com",
            role="viewer",
            send_email_notification=False,
        )
    assert await Invitation.find({"context.email": "wrong-workspace@example.com"}) == []


@pytest.mark.asyncio
async def test_resource_issuance_resolves_omitted_workspace_and_canonical_issuer():
    owner, workspace, _, track, _ = await graph()
    issued, _, _ = await invitations.create_resource_invitation(
        resource_type="track",
        resource_id=track.id,
        workspace_id="",
        inviter_user_id=owner.id,
        email="resolved-workspace@example.com",
        role="viewer",
        send_email_notification=False,
    )
    assert issued.workspace_id == workspace.id
    assert issued.invited_by_user_id == owner.id


@pytest.mark.asyncio
@pytest.mark.parametrize("source_change", ["grant_removed", "rejoined", "legacy"])
async def test_pending_resource_invitation_rechecks_current_issuer(source_change):
    from jvspatial.api.auth.models import User as AuthUser

    from app.models.edges import COLLABORATES_ON
    from app.models.nodes import User
    from app.services.app_graph import catalog_user
    from app.services.permissions import resolve_role

    _, workspace, _, track, _ = await graph()
    issuer, _, _, _, _ = await graph()
    membership = await issuer.connect(workspace, edge=IS_MEMBER_OF, role="member")
    grant = await issuer.connect(track, edge=COLLABORATES_ON, role="admin")
    identity = await AuthUser.create(
        email="current-issuer@example.com", password_hash="fixture"
    )
    recipient = await User.create(user_id=identity.id)
    await catalog_user(recipient)
    issued, token, _ = await invitations.create_resource_invitation(
        resource_type="track",
        resource_id=track.id,
        workspace_id=workspace.id,
        inviter_user_id=issuer.id,
        email=identity.email,
        role="editor",
        send_email_notification=False,
    )
    assert issued.issuer_membership_id == membership.id
    if source_change == "grant_removed":
        await grant.delete()
    elif source_change == "rejoined":
        await membership.delete()
        replacement = await issuer.connect(workspace, edge=IS_MEMBER_OF, role="member")
        assert replacement.id != membership.id
        assert await resolve_role(issuer.id, "track", track.id) == "admin"
    else:
        context = await issuer.get_context()
        await context.database.find_one_and_update(
            "node", {"id": issued.id}, {"$unset": {"context.issuer_membership_id": ""}}
        )
    accepted, error = await invitations.consume_invitation_token(token, recipient.id)
    assert error == invitations.ERR_GRANT_FAILED
    assert accepted.id == issued.id
    assert (await Invitation.find_one({"id": issued.id})).status == "pending"
    assert await resolve_role(recipient.id, "track", track.id) is None
