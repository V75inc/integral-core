"""Frame navigation accepts only currently authorized destinations in its App."""

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.api.errors import InsufficientPermissionsError
from app.services import extension_navigation as service


@pytest.mark.asyncio
async def test_navigation_rechecks_scope_app_and_resource_authority(monkeypatch):
    monkeypatch.setattr(
        service.App, "get", AsyncMock(return_value=SimpleNamespace(id="a"))
    )
    access = AsyncMock()
    monkeypatch.setattr(service, "_ensure_app_access", access)
    location = SimpleNamespace(workspace_id="ws", app_id="a")
    monkeypatch.setattr(service, "locate_resource", AsyncMock(return_value=location))
    role = AsyncMock(return_value="viewer")
    monkeypatch.setattr(service, "resolve_role", role)
    args = dict(
        user_id="u",
        workspace_id="ws",
        app_id="a",
        kind="entry",
        resource_id="n.Entry.one",
    )
    assert await service.resolve_extension_navigation(**args) == {
        "path": "/entries/n.Entry.one"
    }
    access.assert_awaited_once()
    location.app_id = "foreign"
    with pytest.raises(InsufficientPermissionsError):
        await service.resolve_extension_navigation(**args)
    location.app_id = "a"
    location.workspace_id = "foreign"
    with pytest.raises(InsufficientPermissionsError):
        await service.resolve_extension_navigation(**args)
    location.workspace_id = "ws"
    role.return_value = None
    with pytest.raises(InsufficientPermissionsError):
        await service.resolve_extension_navigation(**args)
