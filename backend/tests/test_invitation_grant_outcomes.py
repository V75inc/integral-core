"""Actual-store grant baselines, upgrades, conflicts and receipt replay."""

import uuid

import pytest
from jvspatial.api.auth.models import User as AuthUser

from app.models.edges import COLLABORATES_ON, IS_MEMBER_OF
from app.models.nodes import Invitation, User
from app.services import invitations
from app.services.app_graph import catalog_user
from app.services.invitation_grants import invitation_effective_role
from app.services.permissions import resolve_role
from tests.test_resource_location import graph

pytestmark = pytest.mark.smoke


async def recipient_fixture():
    """Create the canonical workspace and a linked, current recipient account."""
    owner, workspace, _, track, _ = await graph()
    identity = await AuthUser.create(
        email=f"grant-outcome-{uuid.uuid4().hex}@example.com", password_hash="fixture"
    )
    recipient = await User.create(user_id=identity.id)
    await catalog_user(recipient)
    return owner, workspace, track, recipient, identity.email


async def issue_resource(owner, workspace, track, email, role="editor"):
    """Capture the existing target grant at actual service issuance."""
    return await invitations.create_resource_invitation(
        resource_type="track",
        resource_id=track.id,
        workspace_id=workspace.id,
        inviter_user_id=owner.id,
        email=email,
        role=role,
        send_email_notification=False,
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "old_role,expected_outcome,expected_role",
    [
        (None, "created", "editor"),
        ("viewer", "upgraded", "editor"),
        ("admin", "unchanged", "admin"),
    ],
)
async def test_resource_receipt_matches_created_upgraded_or_unchanged_grant(
    old_role, expected_outcome, expected_role
):
    """An intended upgrade retains the original edge identity and higher rights."""
    owner, workspace, track, recipient, email = await recipient_fixture()
    original = None
    if old_role:
        await recipient.connect(workspace, edge=IS_MEMBER_OF, role="guest")
        original = await recipient.connect(track, edge=COLLABORATES_ON, role=old_role)
    issued, token, _ = await issue_resource(owner, workspace, track, email)
    accepted, error = await invitations.consume_invitation_token(token, recipient.id)
    assert error is None
    assert accepted.grant_outcome == expected_outcome
    assert accepted.granted_role == expected_role
    assert accepted.effective_role_at_acceptance == expected_role
    assert await resolve_role(recipient.id, "track", track.id) == expected_role
    context = await recipient.get_context()
    rows = await context.database.find(
        "edge",
        {
            "source": recipient.id,
            "target": track.id,
            "entity": COLLABORATES_ON.__name__,
        },
    )
    assert len(rows) == 1
    if original:
        assert rows[0]["id"] == original.id
    assert (
        await Invitation.find_one({"id": issued.id})
    ).grant_outcome == expected_outcome


@pytest.mark.asyncio
@pytest.mark.parametrize("intervening_change", ["changed", "removed", "created"])
async def test_old_invitation_does_not_overwrite_intervening_grant_change(
    intervening_change,
):
    """Stale requested editor rights do not undo newer role/removal decisions."""
    owner, workspace, track, recipient, email = await recipient_fixture()
    await recipient.connect(workspace, edge=IS_MEMBER_OF, role="guest")
    original = None
    if intervening_change != "created":
        original = await recipient.connect(track, edge=COLLABORATES_ON, role="viewer")
    _, token, _ = await issue_resource(owner, workspace, track, email)
    if intervening_change == "removed":
        await original.delete()
        expected = None
    elif intervening_change == "changed":
        original.role = "commenter"
        await original.save()
        expected = "commenter"
    else:
        await recipient.connect(track, edge=COLLABORATES_ON, role="viewer")
        expected = "viewer"
    accepted, error = await invitations.consume_invitation_token(token, recipient.id)
    assert error is None
    assert accepted.grant_outcome == "conflict"
    assert accepted.granted_role == expected
    assert await resolve_role(recipient.id, "track", track.id) == expected


@pytest.mark.asyncio
async def test_existing_workspace_member_upgrade_preserves_membership_lifetime():
    """An invitation can upgrade rights without replacing the membership edge."""
    owner, workspace, _, recipient, email = await recipient_fixture()
    membership = await recipient.connect(workspace, edge=IS_MEMBER_OF, role="member")
    _, token, _ = await invitations.create_invitation(
        workspace=workspace,
        inviter_user_id=owner.id,
        email=email,
        role="admin",
        can_create_apps=True,
        send_email_notification=False,
    )
    accepted, error = await invitations.consume_invitation_token(token, recipient.id)
    assert error is None and accepted.grant_outcome == "upgraded"
    assert accepted.granted_role == "admin"
    assert accepted.granted_can_create_apps is True
    assert accepted.granted_can_create_tracks is False
    context = await recipient.get_context()
    row = await context.database.find_one("edge", {"id": membership.id})
    assert row["context"]["role"] == "admin"
    assert row["context"]["can_create_apps"] is True
    assert await invitation_effective_role(accepted, recipient.id) == "admin"


@pytest.mark.asyncio
async def test_receipt_retry_keeps_history_but_rechecks_current_access():
    """A completed invitation cannot regrant access removed after acceptance."""
    owner, workspace, track, recipient, email = await recipient_fixture()
    _, token, _ = await issue_resource(owner, workspace, track, email, role="viewer")
    accepted, error = await invitations.consume_invitation_token(token, recipient.id)
    assert error is None and accepted.granted_role == "viewer"
    context = await recipient.get_context()
    for edge in await context.find_edges_between(
        recipient.id, track.id, edge_class=COLLABORATES_ON
    ):
        await edge.delete()
    replay, error = await invitations.consume_invitation_token(token, recipient.id)
    assert error is None and replay.grant_outcome == "created"
    assert replay.granted_role == "viewer"
    assert await invitation_effective_role(replay, recipient.id) is None


@pytest.mark.asyncio
@pytest.mark.parametrize("field", ["role", "grant_baseline", "expires_at"])
async def test_missing_raw_invitation_facts_cannot_activate_defaults(field):
    """Hydration defaults cannot turn an incomplete pending record into authority."""
    owner, workspace, _, recipient, email = await recipient_fixture()
    issued, token, _ = await invitations.create_invitation(
        workspace=workspace,
        inviter_user_id=owner.id,
        email=email,
        role="admin",
        send_email_notification=False,
    )
    context = await recipient.get_context()
    await context.database.find_one_and_update(
        "node", {"id": issued.id}, {"$unset": {f"context.{field}": ""}}
    )
    _, error = await invitations.consume_invitation_token(token, recipient.id)
    assert error == invitations.ERR_GRANT_FAILED
    assert (
        await context.database.find(
            "edge",
            {
                "source": recipient.id,
                "target": workspace.id,
                "entity": IS_MEMBER_OF.__name__,
            },
        )
        == []
    )
