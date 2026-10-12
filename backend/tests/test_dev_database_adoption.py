"""Bounded current identity lookup retains duplicate-profile validation."""

from types import SimpleNamespace

import pytest

from app.models.nodes import User
from app.services import permissions

pytestmark = [pytest.mark.unit, pytest.mark.smoke]


async def test_auth_user_resolution_bounds_raw_candidates_before_hydration(monkeypatch):
    calls = []
    user = SimpleNamespace(id="graph-user")
    row = {
        "id": user.id,
        "entity": User._entity_name(),
        "context": {"user_id": "o.User.auth-user"},
    }

    async def find(collection, query, *, limit):
        calls.append((collection, query, limit))
        return [row]

    async def auth_record(database, identity):
        assert identity == "o.User.auth-user"
        return {"context": {"is_active": True}}

    async def hydrate(model, record):
        assert model is User and record is row
        return user

    context = SimpleNamespace(
        database=SimpleNamespace(find=find), _deserialize_entity=hydrate
    )
    monkeypatch.setattr("jvspatial.core.context.get_default_context", lambda: context)
    monkeypatch.setattr(permissions, "primary_auth_record", auth_record)
    assert await permissions.get_user_node("o.User.auth-user") is user
    assert calls == [
        (
            "node",
            {
                "entity": User._entity_name(),
                "$or": [
                    {"context.user_id": "o.User.auth-user"},
                    {"user_id": "o.User.auth-user"},
                ],
            },
            2,
        )
    ]
