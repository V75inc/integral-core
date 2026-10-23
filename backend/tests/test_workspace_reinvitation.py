"""App access history must not invalidate a new workspace invitation."""

import pytest

from app.models.edges import OWNS
from app.models.nodes import App, Invitation
from app.services.app_graph import catalog_app
from app.services.invitations import create_resource_invitation
from app.services.permissions import get_user_node, resolve_role
from app.services.sharing import remove_collaborator
from app.services.workspace_permissions import can_access_workspace
from tests.test_invitation_mailbox_proof import invitee_account

pytestmark = pytest.mark.smoke


@pytest.mark.asyncio
@pytest.mark.parametrize("verify_mailbox", [True, False])
async def test_workspace_reinvite_after_app_commenter_removed(
    client, authenticated_client, monkeypatch, verify_mailbox
):
    caller, initial, workspace_id, code = await invitee_account(
        client, authenticated_client, monkeypatch
    )
    revoked = await authenticated_client.delete(
        f"/api/workspaces/{workspace_id}/invitations/{initial['invitation']['id']}"
    )
    assert revoked.status_code == 200, revoked.text
    owner_me = (await authenticated_client.get("/api/auth/me")).json()["user"]
    owner = await get_user_node(owner_me["id"])
    app = await App.create(name="Reinvite QA", workspace_id=workspace_id)
    await catalog_app(app)
    await owner.connect(app, edge=OWNS)
    async with caller:
        if verify_mailbox:
            verified = await caller.post("/api/auth/verify-email", json={"code": code})
            assert verified.status_code == 200, verified.text
        me = (await caller.get("/api/auth/me")).json()["user"]
        recipient = await get_user_node(me["id"])
        app_invite, token, _ = await create_resource_invitation(
            resource_type="app",
            resource_id=app.id,
            workspace_id=workspace_id,
            inviter_user_id=owner.id,
            email=initial["invitation"]["email"],
            role="commenter",
        )
        accepted = await caller.post(f"/api/invitations/{token}/accept")
        assert accepted.status_code == 200, accepted.text
        assert await can_access_workspace(recipient.id, workspace_id) == "guest"
        assert await resolve_role(recipient.id, "app", app.id) == "commenter"
        await remove_collaborator(owner.id, "app", app.id, recipient.id)
        assert await resolve_role(recipient.id, "app", app.id) is None
        issued = await authenticated_client.post(
            f"/api/workspaces/{workspace_id}/invitations",
            json={"email": initial["invitation"]["email"], "role": "member"},
        )
        assert issued.status_code == 200, issued.text
        fresh_id = issued.json()["invitation"]["id"]
        assert fresh_id != app_invite.id
        listed = (await caller.get("/api/me/invitations")).json()["invitations"]
        if not verify_mailbox:
            # Token acceptance does not prove the account's current mailbox.
            assert listed == []
            verified = await caller.post("/api/auth/verify-email", json={"code": code})
            assert verified.status_code == 200, verified.text
            listed = (await caller.get("/api/me/invitations")).json()["invitations"]
        assert fresh_id in {row["id"] for row in listed}
        accepted = await caller.post(f"/api/me/invitations/{fresh_id}/accept")
        assert accepted.status_code == 200, accepted.text
        assert accepted.json()["role"] == "member"
        assert await can_access_workspace(recipient.id, workspace_id) == "member"
        assert await resolve_role(recipient.id, "app", app.id) is None
        assert (await Invitation.get(app_invite.id)).status == "accepted"
        assert (await Invitation.get(fresh_id)).status == "accepted"
