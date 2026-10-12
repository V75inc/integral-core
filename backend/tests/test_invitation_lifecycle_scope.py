"""Actual-store lifecycle scope and PostgreSQL rollback proofs."""

import pytest

from app.exceptions import AuthorizationUnavailableError
from app.models.nodes import Invitation
from app.services.app_operations.transaction_scope import graph_transaction_available
from app.services.invitation_lifecycle import invitation_lifecycle_scope
from app.services.invitations import create_invitation
from tests.test_resource_location import graph

pytestmark = pytest.mark.smoke


async def pending_invitation():
    """Create a current rooted invitation using its authorized producer."""
    owner, workspace, _, _, _ = await graph()
    invitation, _, _ = await create_invitation(
        workspace=workspace,
        inviter_user_id=owner.id,
        email="lifecycle-scope@example.com",
        send_email_notification=False,
    )
    return invitation, workspace


@pytest.mark.asyncio
async def test_scope_reloads_current_terminal_state():
    """A warmed pending snapshot cannot override a committed revoke."""
    invitation, workspace = await pending_invitation()
    current = await Invitation.find_one({"id": invitation.id})
    current.status = "revoked"
    await current.save()
    async with invitation_lifecycle_scope(invitation.id, workspace.id) as admitted:
        assert admitted.status == "revoked"


@pytest.mark.asyncio
async def test_scope_rejects_same_task_reentry_before_nested_effect():
    """Nested transitions cannot mutate the same terminal record twice."""
    invitation, workspace = await pending_invitation()
    async with invitation_lifecycle_scope(invitation.id, workspace.id):
        with pytest.raises(AuthorizationUnavailableError):
            async with invitation_lifecycle_scope(invitation.id, workspace.id):
                pytest.fail("nested transition entered")
    assert (await Invitation.find_one({"id": invitation.id})).status == "pending"


@pytest.mark.asyncio
async def test_scope_rejects_changed_target_workspace():
    """The transaction cannot act through an obsolete workspace hint."""
    invitation, workspace = await pending_invitation()
    current = await Invitation.find_one({"id": invitation.id})
    current.workspace_id = "different-workspace"
    await current.save()
    with pytest.raises(AuthorizationUnavailableError):
        async with invitation_lifecycle_scope(invitation.id, workspace.id):
            pytest.fail("moved invitation entered")


@pytest.mark.asyncio
@pytest.mark.parametrize("field", ["status", "invitation_revision"])
async def test_scope_rejects_missing_raw_state_instead_of_hydration_defaults(field):
    """A default pending status or revision cannot invent lifecycle authority."""
    invitation, workspace = await pending_invitation()
    context = await invitation.get_context()
    await context.database.find_one_and_update(
        "node", {"id": invitation.id}, {"$unset": {f"context.{field}": ""}}
    )
    with pytest.raises(AuthorizationUnavailableError):
        async with invitation_lifecycle_scope(invitation.id, workspace.id):
            pytest.fail("missing persisted state entered")


@pytest.mark.asyncio
async def test_postgres_scope_rolls_back_terminal_write_and_workspace_revision():
    """A fault after save cannot commit half a lifecycle unit on PostgreSQL."""
    invitation, workspace = await pending_invitation()
    context = await invitation.get_context()
    if not graph_transaction_available(context.database):
        pytest.skip("PostgreSQL transaction lane required")
    from app.services.authorization_scope import authorization_state_id

    before = await context.database.find_one(
        "node", {"id": authorization_state_id(workspace.id)}
    )
    with pytest.raises(RuntimeError, match="injected failure"):
        async with invitation_lifecycle_scope(invitation.id, workspace.id) as current:
            current.status = "accepted"
            await current.save()
            raise RuntimeError("injected failure")
    assert (await Invitation.find_one({"id": invitation.id})).status == "pending"
    assert (
        await context.database.find_one(
            "node", {"id": authorization_state_id(workspace.id)}
        )
        == before
    )
