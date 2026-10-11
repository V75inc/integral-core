"""Core call-site bounds for the qualified JV Spatial dev database API."""

from types import SimpleNamespace

import pytest

from app.models.nodes import User
from app.services import permissions

pytestmark = [pytest.mark.unit, pytest.mark.smoke]


async def test_auth_user_resolution_uses_first_match_not_full_hydration(monkeypatch):
    calls = []
    user = SimpleNamespace(id="graph-user")

    async def get(cls, node_id):
        return None

    async def auth_get(node_id):
        return SimpleNamespace(id="auth-user")

    async def find_one(cls, query):
        calls.append(query)
        return None if "context.user_id" in query else user

    async def forbidden(*args, **kwargs):
        raise AssertionError("first-match resolution hydrated every matching user")

    monkeypatch.setattr(User, "get", classmethod(get))
    monkeypatch.setattr(User, "find_one", classmethod(find_one))
    monkeypatch.setattr(User, "find", forbidden)
    monkeypatch.setattr(permissions, "AuthUser", SimpleNamespace(get=auth_get))
    monkeypatch.setattr(permissions, "permissions_cache_get", lambda: None)
    assert await permissions.get_user_node("auth-user") is user
    assert calls == [{"context.user_id": "auth-user"}, {"user_id": "auth-user"}]
