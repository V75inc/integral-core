"""Direct member management enforces ceilings and stable source revisions."""

import pytest
from jvspatial.core.context import get_default_context

from app.api.errors import (
    BadRequestError,
    InsufficientPermissionsError,
    ResourceConflictError,
)
from app.api.workspaces import (
    add_workspace_member,
    patch_workspace_member,
    remove_workspace_member,
)
from app.exceptions import AuthorizationUnavailableError
from app.models.edges import IS_MEMBER_OF
from app.models.nodes import User
from app.services import workspace_membership as membership
from app.services.app_graph import catalog_user
from app.services.workspace_governance import ordinary_authority_only
from tests.test_model_api_authority import request_for
from tests.test_resource_location import graph

pytestmark = [pytest.mark.smoke, pytest.mark.asyncio]


async def subject(workspace, role=None, **flags):
    user = await User.create(display_name="Member authority QA")
    await catalog_user(user)
    if role:
        await user.connect(workspace, edge=IS_MEMBER_OF, role=role, **flags)
    return user


async def row(user, workspace):
    return await membership._membership(
        await User.find_one({"id": user.id}), workspace.id
    )


@pytest.mark.parametrize("surface", ["service", "handler"])
@pytest.mark.parametrize("operation", ["add", "update"])
@pytest.mark.parametrize(
    "rights",
    [{"role": "admin"}, {"can_create_apps": True}, {"can_create_tracks": True}],
)
async def test_admin_cannot_delegate_protected_rights(surface, operation, rights):
    _, workspace, _, _, _ = await graph()
    admin = await subject(
        workspace, "admin", can_create_apps=True, can_create_tracks=True
    )
    member = await subject(workspace, "member" if operation == "update" else None)
    before = await row(member, workspace)
    with pytest.raises(InsufficientPermissionsError):
        if surface == "service":
            function = (
                membership.add_member
                if operation == "add"
                else membership.update_member
            )
            await function(
                actor_id=admin.id,
                workspace_id=workspace.id,
                member_id=member.id,
                **rights,
            )
        else:
            function = (
                add_workspace_member if operation == "add" else patch_workspace_member
            )
            await function(
                request_for(admin.id, workspace.id),
                workspace.id,
                member_user_id=member.id,
                **rights,
            )
    assert await row(member, workspace) == before


@pytest.mark.parametrize("operation", ["update", "remove"])
@pytest.mark.parametrize("target", ["self", "peer", "creator", "owner"])
async def test_admin_cannot_change_self_or_protected_peer(operation, target):
    owner, workspace, _, _, _ = await graph()
    admin = await subject(workspace, "admin")
    member = (
        admin
        if target == "self"
        else (
            owner
            if target == "owner"
            else await subject(
                workspace,
                "admin" if target == "peer" else "member",
                can_create_tracks=target == "creator",
            )
        )
    )
    before = await row(member, workspace)
    with pytest.raises((InsufficientPermissionsError, BadRequestError)):
        if operation == "update":
            await membership.update_member(
                actor_id=admin.id,
                workspace_id=workspace.id,
                member_id=member.id,
                role="guest",
            )
        else:
            await remove_workspace_member(
                request_for(admin.id, workspace.id), workspace.id, member.id
            )
    assert await row(member, workspace) == before


async def test_owner_updates_preserve_lifetime_join_time_metadata_and_advance_revision():
    owner, workspace, _, _, _ = await graph()
    member = await subject(workspace, "member")
    before = await row(member, workspace)
    await get_default_context().database.find_one_and_update(
        "edge", {"id": before["id"]}, {"$set": {"context.source_note": "preserve"}}
    )
    result = await membership.update_member(
        actor_id=owner.id,
        workspace_id=workspace.id,
        member_id=member.id,
        role="admin",
        can_create_apps=True,
    )
    current = await row(member, workspace)
    assert current["id"] == before["id"] == result["membership_id"]
    assert current["context"]["joined_at"] == before["context"]["joined_at"]
    assert current["context"]["source_note"] == "preserve"
    assert result["role"] == "admin" and result["can_create_apps"] is True
    assert result["grant_revision"] == 1
    unchanged = await membership.update_member(
        actor_id=owner.id, workspace_id=workspace.id, member_id=member.id, role="admin"
    )
    assert unchanged["grant_revision"] == 1


async def test_owner_ordinary_function_mode_cannot_use_stewardship():
    owner, workspace, _, _, _ = await graph()
    member = await subject(workspace, "member")
    before = await row(member, workspace)
    with ordinary_authority_only(), pytest.raises(InsufficientPermissionsError):
        await membership.update_member(
            actor_id=owner.id,
            workspace_id=workspace.id,
            member_id=member.id,
            role="admin",
        )
    assert await row(member, workspace) == before


async def test_duplicate_add_is_conflict_and_keeps_existing_lifetime():
    owner, workspace, _, _, _ = await graph()
    member = await subject(workspace, "guest")
    before = await row(member, workspace)
    with pytest.raises(ResourceConflictError):
        await membership.add_member(
            actor_id=owner.id,
            workspace_id=workspace.id,
            member_id=member.id,
            role="admin",
        )
    assert await row(member, workspace) == before


async def test_admin_can_manage_basic_members_but_never_assign_creation_flags():
    _, workspace, _, _, _ = await graph()
    admin = await subject(workspace, "admin")
    member = await subject(workspace)
    created = await membership.add_member(
        actor_id=admin.id, workspace_id=workspace.id, member_id=member.id
    )
    assert created["role"] == "member" and created["can_create_apps"] is False
    changed = await membership.update_member(
        actor_id=admin.id, workspace_id=workspace.id, member_id=member.id, role="guest"
    )
    assert changed["membership_id"] == created["membership_id"]
    await membership.remove_member(
        actor_id=admin.id, workspace_id=workspace.id, member_id=member.id
    )
    assert await row(member, workspace) is None


@pytest.mark.parametrize("malformed", ["yes", 1, {}, []])
async def test_service_boolean_coercion_never_grants_creation(malformed):
    owner, workspace, _, _, _ = await graph()
    member = await subject(workspace, "member")
    before = await row(member, workspace)
    with pytest.raises(BadRequestError):
        await membership.update_member(
            actor_id=owner.id,
            workspace_id=workspace.id,
            member_id=member.id,
            can_create_apps=malformed,
        )
    assert await row(member, workspace) == before


@pytest.mark.parametrize(
    "field,value",
    [
        ("role", "unknown"),
        ("can_create_apps", "false"),
        ("grant_revision", True),
        ("grant_revision", -1),
    ],
)
async def test_malformed_primary_membership_is_not_repaired_by_generic_edit(
    field, value
):
    owner, workspace, _, _, _ = await graph()
    member = await subject(workspace, "member")
    before = await row(member, workspace)
    database = get_default_context().database
    await database.find_one_and_update(
        "edge", {"id": before["id"]}, {"$set": {f"context.{field}": value}}
    )
    raw = await database.find_one("edge", {"id": before["id"]})
    with pytest.raises(AuthorizationUnavailableError):
        await membership.update_member(
            actor_id=owner.id,
            workspace_id=workspace.id,
            member_id=member.id,
            role="guest",
        )
    assert await database.find_one("edge", {"id": before["id"]}) == raw


async def test_member_role_round_trip_advances_baseline_so_stale_invitation_conflicts():
    from app.services import invitations
    from tests.test_invitation_grant_outcomes import recipient_fixture

    owner, workspace, _, recipient, email = await recipient_fixture()
    await membership.add_member(
        actor_id=owner.id, workspace_id=workspace.id, member_id=recipient.id
    )
    invitation, token, _ = await invitations.create_invitation(
        workspace=workspace,
        inviter_user_id=owner.id,
        email=email,
        role="admin",
        send_email_notification=False,
    )
    for role in ("guest", "member"):
        await membership.update_member(
            actor_id=owner.id,
            workspace_id=workspace.id,
            member_id=recipient.id,
            role=role,
        )
    accepted, error = await invitations.consume_invitation_token(token, recipient.id)
    assert error is None and accepted.id == invitation.id
    assert accepted.grant_outcome == "conflict" and accepted.granted_role == "member"
    assert (await row(recipient, workspace))["context"]["grant_revision"] == 2


async def test_membership_edit_retains_existing_model_creator_authority():
    from app.services.model_authority import model_action_authorized
    from tests.test_model_creator_grants import fixture, resource

    owner, workspace, creator, member, model_id, _ = await fixture()
    await membership.update_member(
        actor_id=owner.id,
        workspace_id=workspace.id,
        member_id=creator.id,
        role="guest",
        can_create_tracks=False,
    )
    assert (await row(creator, workspace))["id"] == member.id
    assert await model_action_authorized(
        creator.id, "operational_model.update", resource(model_id)
    )


@pytest.mark.parametrize("malformed", ["true", "false", 1, 0, {}, [], None])
@pytest.mark.parametrize("operation", ["add", "update"])
async def test_http_creation_flags_require_json_booleans(
    authenticated_client, second_user, malformed, operation
):
    created = await authenticated_client.post(
        "/api/workspaces", json={"name": "Strict membership rights"}
    )
    assert created.status_code == 200, created.text
    workspace_id = created.json()["workspace"]["id"]
    path = f"/api/workspaces/{workspace_id}/members"
    if operation == "update":
        added = await authenticated_client.post(
            path, json={"member_user_id": second_user.id}
        )
        assert added.status_code == 200, added.text
        result = await authenticated_client.patch(
            f"{path}/{second_user.id}", json={"can_create_apps": malformed}
        )
    else:
        result = await authenticated_client.post(
            path, json={"member_user_id": second_user.id, "can_create_apps": malformed}
        )
    assert result.status_code in {400, 422}, result.text


async def test_guest_can_leave_personal_workspace_without_changing_owner():
    owner, workspace, _, _, _ = await graph()
    workspace.kind = "personal"
    await workspace.save()
    guest = await subject(workspace, "guest")
    await membership.remove_member(
        actor_id=guest.id, workspace_id=workspace.id, member_id=guest.id, leave=True
    )
    assert await row(guest, workspace) is None
    assert (await row(owner, workspace))["context"]["role"] == "owner"


@pytest.mark.parametrize("operation", ["add", "update"])
async def test_http_query_rights_cannot_bypass_raw_json_validation(
    authenticated_client, second_user, operation
):
    created = await authenticated_client.post(
        "/api/workspaces", json={"name": "Query member rights"}
    )
    workspace_id = created.json()["workspace"]["id"]
    path = f"/api/workspaces/{workspace_id}/members"
    if operation == "update":
        added = await authenticated_client.post(
            path, json={"member_user_id": second_user.id}
        )
        assert added.status_code == 200, added.text
        result = await authenticated_client.patch(
            f"{path}/{second_user.id}", params={"can_create_apps": "true"}, json={}
        )
    else:
        result = await authenticated_client.post(
            path,
            params={"can_create_apps": "true"},
            json={"member_user_id": second_user.id},
        )
    assert result.status_code in {400, 422}, result.text


async def test_invitation_upgrade_keeps_legacy_and_typed_revisions_coherent():
    from app.services import invitations
    from tests.test_invitation_grant_outcomes import recipient_fixture

    owner, workspace, _, recipient, email = await recipient_fixture()
    added = await membership.add_member(
        actor_id=owner.id, workspace_id=workspace.id, member_id=recipient.id
    )
    await get_default_context().database.find_one_and_update(
        "edge", {"id": added["membership_id"]}, {"$set": {"grant_revision": 0}}
    )
    _, token, _ = await invitations.create_invitation(
        workspace=workspace,
        inviter_user_id=owner.id,
        email=email,
        role="admin",
        send_email_notification=False,
    )
    accepted, error = await invitations.consume_invitation_token(token, recipient.id)
    assert error is None and accepted.grant_outcome == "upgraded"
    current = await row(recipient, workspace)
    assert current["grant_revision"] == current["context"]["grant_revision"] == 1


async def test_invitation_audit_snapshot_includes_requested_rights_and_source_binding():
    from app.services import invitations

    owner, workspace, _, _, _ = await graph()
    invitation, _, _ = await invitations.create_invitation(
        workspace=workspace,
        inviter_user_id=owner.id,
        email="requested-rights@example.com",
        role="admin",
        can_create_apps=True,
        send_email_notification=False,
    )
    snapshot = invitations._invitation_snapshot(invitation)
    assert (
        snapshot["can_create_apps"] is True and snapshot["can_create_tracks"] is False
    )
    assert snapshot["issuer_membership_id"] == (await row(owner, workspace))["id"]
    assert snapshot["grant_baseline"] == invitation.grant_baseline
    assert "token_hash" not in snapshot
