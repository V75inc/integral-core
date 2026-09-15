"""Regression: workspace invitation in-app notifications surface in GET /notifications.

Invitation dispatch stores notifications via create_notification, which must
persist the auth subject (User.user_id) so GET /notifications (JWT principal
query) returns them.
"""

from __future__ import annotations

from datetime import datetime

import pytest

from app.models.edges import IS_MEMBER_OF
from app.models.nodes import Notification, User, Workspace
from app.services.app_graph import catalog_user, catalog_workspace
from app.services.invitations import create_invitation


async def _make_graph_user(*, auth_subject: str, display_name: str) -> User:
    user = await User.create(
        user_id=auth_subject,
        display_name=display_name,
        created_at=datetime.now().isoformat(),
    )
    await catalog_user(user)
    return user


async def _make_org_workspace(*, owner: User, name: str) -> Workspace:
    now = datetime.now().isoformat()
    ws = await Workspace.create(
        kind="organization",
        workspace_type="company",
        name=name,
        name_fold=name.casefold(),
        created_at=now,
        updated_at=now,
    )
    await owner.connect(
        ws,
        edge=IS_MEMBER_OF,
        role="owner",
        joined_at=now,
        can_create_apps=True,
        can_create_tracks=True,
    )
    await catalog_workspace(ws)
    return ws


@pytest.mark.asyncio
async def test_workspace_invite_notification_queryable_by_auth_subject(
    bind_fresh_graph_context_for_async_tests,
):
    """create_invitation emits an in-app row visible to the inbox API query key."""
    from tests.conftest import _bootstrap_test_user_fast

    _token, inviter = await _bootstrap_test_user_fast(
        email="inviter-notify@example.com",
        password="testpassword123",
        name="Inviter Notify",
    )
    _token2, invitee = await _bootstrap_test_user_fast(
        email="invitee-notify@example.com",
        password="testpassword123",
        name="Invitee Notify",
    )

    workspace = await _make_org_workspace(owner=inviter, name="Notify Test Org")

    invitation, _token_plain, _url = await create_invitation(
        workspace=workspace,
        inviter_user_id=inviter.user_id,
        email="invitee-notify@example.com",
        role="member",
        send_email_notification=False,
        inviter_display_name="Inviter Notify",
    )
    assert invitation.id

    # GET /notifications queries by JWT principal (auth subject), not graph node id.
    rows = await Notification.find({"context.user_id": invitee.user_id})
    invite_rows = [n for n in rows if n.type == "invitation"]
    assert len(invite_rows) >= 1, rows

    match = next(
        (
            n
            for n in invite_rows
            if (n.metadata or {}).get("payload", {}).get("invitation_id")
            == invitation.id
        ),
        None,
    )
    assert match is not None, invite_rows
    assert match.user_id == invitee.user_id
    assert match.user_id != invitee.id
