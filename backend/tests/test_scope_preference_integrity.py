"""Scope preferences cannot overwrite concurrent credential or OTP state."""

import pytest

from app.models.nodes import User
from app.services.request_scope import _persist_active_workspace
from tests.test_resource_location import graph

pytestmark = [pytest.mark.smoke, pytest.mark.asyncio]


async def test_explicit_preference_patch_preserves_concurrent_profile_slot(monkeypatch):
    user, workspace, _, _, _ = await graph()
    user.preferences = {"email_verification": {"hash": "old", "attempts": 0}}
    await user.save()
    context = await user.get_context()
    update = context.database.find_one_and_update
    calls = []

    async def concurrent(collection, query, patch, *args, **kwargs):
        if collection == "node" and query.get("id") == user.id:
            calls.append(patch)
            await update(
                "node",
                {"id": user.id},
                {
                    "$set": {
                        "context.preferences.email_verification": {
                            "hash": "new",
                            "attempts": 1,
                        }
                    }
                },
            )
        return await update(collection, query, patch, *args, **kwargs)

    monkeypatch.setattr(context.database, "find_one_and_update", concurrent)
    await _persist_active_workspace(user.id, workspace.id, explicit=True)
    row = await context.database.find_one(
        "node", {"id": user.id, "entity": User._entity_name()}
    )
    assert row["context"]["preferences"]["email_verification"] == {
        "hash": "new",
        "attempts": 1,
    }
    assert row["context"]["active_workspace_id"] == workspace.id
    assert row["context"]["active_workspace_id_explicit"] is True
    assert calls == [
        {
            "$set": {
                "context.active_workspace_id": workspace.id,
                "context.active_workspace_id_explicit": True,
            }
        }
    ]


async def test_automatic_scope_resolution_does_not_persist_a_profile(monkeypatch):
    user, workspace, _, _, _ = await graph()
    context = await user.get_context()

    async def forbidden(*args, **kwargs):
        pytest.fail("Automatic scope resolution attempted a profile write")

    monkeypatch.setattr(context.database, "find_one_and_update", forbidden)
    await _persist_active_workspace(user.id, workspace.id, explicit=False)
