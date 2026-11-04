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


@pytest.mark.asyncio
async def test_related_navigation_requires_a_persisted_typed_link_and_both_app_permissions(
    monkeypatch,
):
    owner_app = SimpleNamespace(id="a", installed_package_slug="own")
    other_app = SimpleNamespace(id="b", installed_package_slug="other")
    monkeypatch.setattr(
        service.App,
        "get",
        AsyncMock(
            side_effect=lambda identity: owner_app if identity == "a" else other_app
        ),
    )
    source = SimpleNamespace(
        type_id="source-type", custom_fields={"source": "n.Entry.target"}
    )
    target = SimpleNamespace(type_id="target-type")
    monkeypatch.setattr(
        service.Entry,
        "get",
        AsyncMock(
            side_effect=lambda identity: (
                source if identity == "n.Entry.source" else target
            )
        ),
    )
    types = {
        "source-type": SimpleNamespace(
            form_schema={
                "fields": [
                    {
                        "key": "source",
                        "type": "relation",
                        "relation": {
                            "allow_cross_app": True,
                            "target_app": "other",
                            "target_entry_types": ["proposal"],
                        },
                    }
                ]
            }
        ),
        "target-type": SimpleNamespace(
            form_schema={"_manifest_entry_type_key": "proposal"}
        ),
    }
    monkeypatch.setattr(
        service.EntryType,
        "get",
        AsyncMock(side_effect=lambda identity: types[identity]),
    )
    monkeypatch.setattr(
        service,
        "locate_resource",
        AsyncMock(
            side_effect=lambda kind, identity: SimpleNamespace(
                workspace_id="ws", app_id="a" if identity == "n.Entry.source" else "b"
            )
        ),
    )
    access = AsyncMock()
    monkeypatch.setattr(service, "_ensure_app_access", access)
    monkeypatch.setattr(service, "resolve_role", AsyncMock(return_value="viewer"))
    args = dict(
        user_id="u",
        workspace_id="ws",
        app_id="a",
        kind="entry",
        resource_id="n.Entry.target",
        source_entry_id="n.Entry.source",
        field_key="source",
    )
    assert await service.resolve_extension_navigation(**args) == {
        "path": "/entries/n.Entry.target"
    }
    assert access.await_count == 2
    source.custom_fields["source"] = "different"
    with pytest.raises(InsufficientPermissionsError):
        await service.resolve_extension_navigation(**args)
    source.custom_fields["source"] = "n.Entry.target"
    types["target-type"].form_schema["_manifest_entry_type_key"] = "other-type"
    with pytest.raises(InsufficientPermissionsError):
        await service.resolve_extension_navigation(**args)
    types["target-type"].form_schema["_manifest_entry_type_key"] = "proposal"
    access.side_effect = [
        None,
        InsufficientPermissionsError(message="Revoked target App"),
    ]
    with pytest.raises(InsufficientPermissionsError):
        await service.resolve_extension_navigation(**args)
