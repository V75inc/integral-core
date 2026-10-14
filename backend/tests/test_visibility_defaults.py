"""Private defaults must agree across models and service validation."""

from unittest.mock import AsyncMock

import pytest

from app.api.errors import BadRequestError
from app.api.validators import (
    effective_app_visibility,
    normalize_track_visibility_input,
)
from app.models.nodes import App, Track


@pytest.mark.asyncio
@pytest.mark.parametrize("value", [None, "", "private"])
async def test_app_default_does_not_infer_workspace_access(monkeypatch, value):
    lookup = AsyncMock(return_value=True)
    monkeypatch.setattr("app.api.validators._is_organization_workspace", lookup)
    assert await effective_app_visibility("workspace-id", value) == "private"
    lookup.assert_not_awaited()


@pytest.mark.asyncio
async def test_app_inherit_is_not_an_authority_choice():
    with pytest.raises(BadRequestError):
        await effective_app_visibility("workspace-id", "inherit")


def test_resource_models_start_private():
    assert App.model_fields["visibility"].default == "private"
    assert Track.model_fields["visibility"].default == "private"


def test_track_inheritance_is_only_explicit():
    assert normalize_track_visibility_input(None) is None
    assert normalize_track_visibility_input("") is None
    assert normalize_track_visibility_input("inherit") == "inherit"
