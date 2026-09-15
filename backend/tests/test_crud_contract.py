"""CRUD integrity contract tests — service-layer delegation invariants."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from app.models.nodes import App


@pytest.mark.asyncio
async def test_create_app_for_user_library_delegates_to_install():
    """Library App creation must route through install_app, not blank create."""
    from app.services.app_service import create_app_for_user

    lib_app = App(id="app_installed_1")
    install_result = {
        "status": "active",
        "app_id": "app_installed_1",
        "installed_at": "2026-01-01T00:00:00Z",
        "version": "1.0.0",
    }

    with (
        patch(
            "app.services.app_service.resolve_workspace_id",
            new_callable=AsyncMock,
            return_value="ws_1",
        ),
        patch(
            "app.services.app_service.can_create_app_under_workspace",
            new_callable=AsyncMock,
            return_value=True,
        ),
        patch(
            "app.services.app_service.effective_app_visibility",
            new_callable=AsyncMock,
            return_value="private",
        ),
        patch(
            "app.services.app_service.ContentProfile.get",
            new_callable=AsyncMock,
        ) as mock_cp_get,
        patch(
            "app.services.app_service.resolve_canonical_bundle_install",
            new_callable=AsyncMock,
            return_value=None,
        ),
        patch(
            "app.services.app_service._library_default_labels",
            new_callable=AsyncMock,
            return_value=("Demo App", "desc"),
        ),
        patch(
            "app.services.app_service.install_app",
            new_callable=AsyncMock,
            return_value=install_result,
        ) as mock_install,
        patch(
            "app.services.app_service.App.get",
            new_callable=AsyncMock,
            return_value=lib_app,
        ),
    ):
        mock_cp = type(
            "CP",
            (),
            {"library_package": True, "manifest": {"package": {"name": "demo"}}},
        )()
        mock_cp_get.return_value = mock_cp

        result = await create_app_for_user(
            "user_1",
            "Ignored Name",
            library_package_id="lib_cp_1",
        )

    mock_install.assert_awaited_once()
    call_kwargs = mock_install.await_args.kwargs
    assert call_kwargs["workspace_id"] == "ws_1"
    assert call_kwargs["library_cp_id"] == "lib_cp_1"
    assert call_kwargs["actor_id"] == "user_1"
    assert result is lib_app


@pytest.mark.asyncio
async def test_wire_app_owner_sets_fields():
    """wire_app_owner stamps workspace_id and owner_user_id on new Apps."""
    from datetime import datetime, timezone

    from app.models.edges import OWNS
    from app.models.nodes import User
    from app.services.app_graph import wire_app_owner

    now = datetime.now(timezone.utc).isoformat()
    user = await User.create(
        email="wire-owner@test.local",
        display_name="Wire Owner",
        created_at=now,
        updated_at=now,
    )
    app = await App.create(
        name="Wire Test App",
        name_fold="wire test app",
        owner_user_id="",
        description="",
        visibility="private",
        workspace_id="",
        created_at=now,
        updated_at=now,
    )

    await wire_app_owner(app, user.id, workspace_id="ws_wire_test")

    assert app.workspace_id == "ws_wire_test"
    assert app.owner_user_id == user.id

    ctx = await user.get_context()
    owns = await ctx.find_edges_between(user.id, app.id, edge_class=OWNS)
    assert owns
