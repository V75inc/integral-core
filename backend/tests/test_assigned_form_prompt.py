"""Tests for neutral assigned-form prompt persistence."""

from types import SimpleNamespace

import pytest

from app.api.auth import _merge_user_preferences
from app.services.assigned_form_prompt import (
    clear_pending_assigned_form,
    member_assigned_form_relative_url,
    pending_assigned_form_view,
    set_pending_assigned_form,
)


@pytest.mark.asyncio
async def test_prompt_lifecycle_uses_server_managed_slot():
    user = SimpleNamespace(preferences={"theme": "dark"})
    url = member_assigned_form_relative_url("record/1")
    assert url == "/me/assigned-form?entry=record%2F1"
    await set_pending_assigned_form(user, url)
    assert pending_assigned_form_view(user) == {"url": url}
    user.preferences = _merge_user_preferences(
        user.preferences, {"pending_assigned_form": {"url": "/other"}, "theme": "light"}
    )
    assert pending_assigned_form_view(user) == {"url": url}
    assert await clear_pending_assigned_form(user) is True
    assert await clear_pending_assigned_form(user) is False
    assert user.preferences == {"theme": "light"}
