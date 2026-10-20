"""Library deletion never removes an attached live model."""

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.api import operational_models as api
from app.api.errors import BadRequestError, InsufficientPermissionsError


@pytest.mark.asyncio
async def test_deletion_rechecks_permission_before_touching_catalog(monkeypatch):
    model = SimpleNamespace(
        id="model", library_package=True, nodes=AsyncMock(), delete=AsyncMock()
    )
    monkeypatch.setattr(api, "resolve_principal_id", lambda _: "user")
    monkeypatch.setattr(api, "get_current_model", AsyncMock(return_value=model))
    monkeypatch.setattr(
        api, "_resolve_cp_edit_permission", AsyncMock(return_value=False)
    )
    with pytest.raises(InsufficientPermissionsError):
        await api.deprecate_library_operational_model(None, "model")
    model.nodes.assert_not_awaited()
    model.delete.assert_not_awaited()


@pytest.mark.asyncio
async def test_attached_model_is_preserved_even_for_an_authorized_editor(monkeypatch):
    model = SimpleNamespace(
        id="model",
        library_package=True,
        nodes=AsyncMock(return_value=[object()]),
        delete=AsyncMock(),
    )
    monkeypatch.setattr(api, "resolve_principal_id", lambda _: "user")
    monkeypatch.setattr(api, "get_current_model", AsyncMock(return_value=model))
    monkeypatch.setattr(
        api, "_resolve_cp_edit_permission", AsyncMock(return_value=True)
    )
    with pytest.raises(BadRequestError, match="attached by reference"):
        await api.deprecate_library_operational_model(None, "model")
    model.delete.assert_not_awaited()


@pytest.mark.asyncio
async def test_library_detail_advertises_exact_delete_capability(monkeypatch):
    model = SimpleNamespace(id="model", library_package=True)
    monkeypatch.setattr(api, "resolve_principal_id", lambda _: "user")
    monkeypatch.setattr(api, "get_current_model", AsyncMock(return_value=model))
    monkeypatch.setattr(
        api,
        "_resolve_active_workspace_for_library_list",
        AsyncMock(return_value="workspace"),
    )
    monkeypatch.setattr(api, "visible_library_model", AsyncMock(return_value=model))
    monkeypatch.setattr(api, "_require_operational_model_scope", AsyncMock())
    permission = AsyncMock(return_value=True)
    monkeypatch.setattr(api, "_resolve_cp_edit_permission", permission)
    monkeypatch.setattr(api, "export_node", AsyncMock(return_value={"id": "model"}))
    assert (await api.get_library_operational_model(None, "model"))[
        "operational_model"
    ]["can_delete"] is True
    assert permission.call_args.kwargs["action"] == "operational_model.delete"
