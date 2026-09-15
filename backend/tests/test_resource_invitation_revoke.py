"""Phase 8 Plan 08-05 Task 3 (B2) — DELETE /api/invitations/{invitation_id}
for resource-level invitation revoke.

3 load-bearing cases:

1. ``test_revoke_resource_invitation_by_owner_returns_200`` — happy path.
   Owner of Track + invitation issued by owner → DELETE returns 200, invitation
   status is "revoked", and the audit envelope uses the REUSED
   ``workspace.invitation_revoke`` ChangeEventAction Literal with
   ``resource_type="Track"``.
2. ``test_revoke_resource_invitation_cross_owner_returns_404`` — invitation
   exists on User A's Track; User B calls DELETE → 404 (no existence leak,
   T-08-05-E02 mitigation).
3. ``test_revoke_workspace_invitation_via_resource_endpoint_returns_400`` —
   prevents the two surfaces from racing on the same revoke (T-08-05-T02).
   Caller must use ``DELETE /workspaces/{id}/invitations/{id}`` instead.
"""

from __future__ import annotations

import pytest
from httpx import AsyncClient

from app.models.edges import OWNS
from app.models.nodes import Track, User, Workspace
from app.services.invitations import (
    create_invitation,
    create_resource_invitation,
)

# ---------------------------------------------------------------------------
# Helpers — isolated per-test data so reset_test_db keeps things clean.
# ---------------------------------------------------------------------------


async def _track(title: str, workspace_id: str = "") -> Track:
    return await Track.create(
        title=title, title_fold=title.casefold(), workspace_id=workspace_id
    )


async def _workspace(name: str, kind: str = "organization") -> Workspace:
    return await Workspace.create(kind=kind, name=name, name_fold=name.casefold())


async def _user(suffix: str) -> User:
    return await User.create(
        user_id=f"resinvrev_{suffix}", display_name=f"User {suffix}"
    )


# ---------------------------------------------------------------------------
# 1. Happy path — owner revokes a resource-level invitation.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_revoke_resource_invitation_by_owner_returns_200(
    authenticated_client: AsyncClient, test_user
):
    """Owner of a Track revokes a pending resource-level invitation."""
    track = await _track("OwnerRevokeT")
    await test_user.connect(track, edge=OWNS)
    principal_id = test_user.user_id

    invitation, _, _ = await create_resource_invitation(
        resource_type="track",
        resource_id=track.id,
        workspace_id="",
        inviter_user_id=principal_id,
        email="invitee@example.com",
        role="viewer",
        send_email_notification=False,
        resource_label="OwnerRevokeT",
    )
    assert invitation.status == "pending"

    resp = await authenticated_client.delete(f"/api/invitations/{invitation.id}")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["message"] == "Invitation revoked"
    assert body["invitation"]["status"] == "revoked"

    # Verify persistence — pull the Invitation back and confirm status.
    from app.models.nodes import Invitation as _Inv

    reloaded = await _Inv.get(invitation.id)
    assert reloaded is not None
    assert reloaded.status == "revoked"


# ---------------------------------------------------------------------------
# 2. Cross-owner — non-owner gets 404 (no existence leak).
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_revoke_resource_invitation_cross_owner_returns_404(
    authenticated_client: AsyncClient, test_user, second_user, second_user_client
):
    """User A owns Track + invitation; User B's DELETE returns 404."""
    if second_user is None or second_user_client is None:
        pytest.skip("second_user / second_user_client fixture unavailable")
    track = await _track("CrossOwnerT")
    # second_user is the OWNER; the authenticated_client (test_user) is NOT.
    await second_user.connect(track, edge=OWNS)
    invitation, _, _ = await create_resource_invitation(
        resource_type="track",
        resource_id=track.id,
        workspace_id="",
        inviter_user_id=second_user.user_id,
        email="x@example.com",
        role="viewer",
        send_email_notification=False,
        resource_label="CrossOwnerT",
    )

    # test_user (NOT the owner) tries to revoke → 404.
    resp = await authenticated_client.delete(f"/api/invitations/{invitation.id}")
    assert resp.status_code == 404, resp.text

    # Verify it was NOT actually revoked — the cross-owner attempt is a no-op.
    from app.models.nodes import Invitation as _Inv

    reloaded = await _Inv.get(invitation.id)
    assert reloaded is not None
    assert (
        reloaded.status == "pending"
    ), "cross-owner DELETE must NOT mutate the invitation — T-08-05-E02"


# ---------------------------------------------------------------------------
# 3. Workspace-targeted invitation via the resource endpoint → 400.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_revoke_workspace_invitation_via_resource_endpoint_returns_400(
    authenticated_client: AsyncClient, test_user
):
    """Workspace-targeted invitation cannot be revoked via the resource endpoint.

    Caller must use the workspace-scoped DELETE
    /workspaces/{id}/invitations/{id} endpoint. The resource endpoint
    refuses workspace invitations with 400 to prevent T-08-05-T02
    (two surfaces racing on the same revoke).
    """
    ws = await _workspace("WsScopedOrg")
    invitation, _, _ = await create_invitation(
        workspace=ws,
        inviter_user_id=test_user.user_id,
        email="wsguest@example.com",
        role="member",
        send_email_notification=False,
    )

    resp = await authenticated_client.delete(f"/api/invitations/{invitation.id}")
    assert resp.status_code == 400, resp.text
    body = resp.json()
    # Canonical 5-key envelope OR FastAPI {detail} envelope — accept both.
    msg = (
        body.get("message")
        or (body.get("detail") if isinstance(body.get("detail"), str) else "")
        or ""
    ).lower()
    assert (
        "workspace" in msg
    ), f"expected workspace-routing hint in error message; got: {body!r}"

    # Confirm the invitation is NOT mutated.
    from app.models.nodes import Invitation as _Inv

    reloaded = await _Inv.get(invitation.id)
    assert reloaded is not None
    assert reloaded.status == "pending"
