"""Blank App create must invalidate accessible-apps aggregates immediately."""

from __future__ import annotations

import pytest


@pytest.mark.asyncio
async def test_blank_app_appears_in_accessible_apps_without_reload():
    from app.models.edges import IS_MEMBER_OF
    from app.models.nodes import User
    from app.services.app_service import create_app_for_user
    from app.services.permissions import get_user_accessible_apps
    from tests.fixtures.workspaces import make_org_workspace

    workspace = await make_org_workspace("blank-app-cache")
    owners = await workspace.nodes(
        edge=[IS_MEMBER_OF], direction="in", node=["User"], limit=1
    )
    owner = owners[0]
    assert isinstance(owner, User)

    # Warm both request/process caches for the accessible-apps aggregate.
    before = await get_user_accessible_apps(owner.id)
    before_ids = {app.id for app in before}

    app = await create_app_for_user(
        owner.id,
        "Immediate Blank App",
        workspace_id=workspace.id,
    )
    assert app.id not in before_ids

    after = await get_user_accessible_apps(owner.id)
    assert any(item.id == app.id for item in after)
