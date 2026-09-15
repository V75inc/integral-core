"""W0.3 / W0.4 / W0.5 — prefs scrub, tags list ACL, SECRET_KEY default reject."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from httpx import AsyncClient


@pytest.mark.asyncio
async def test_put_preferences_preserves_sensitive_slots(
    authenticated_client: AsyncClient, test_user
):
    """Wholesale prefs replace must not clear reset_token / email_verification."""
    from app.models.nodes import User

    user = await User.get(test_user.id)
    assert user is not None
    user.preferences = {
        "theme": "light",
        "reset_token": {"hash": "keep-me", "attempts": 1},
        "email_verification": {"hash": "otp-hash", "attempts": 0},
    }
    await user.save()

    resp = await authenticated_client.put(
        f"/api/users/{test_user.id}",
        json={
            "preferences": {
                "theme": "dark",
                "reset_token": {"hash": "attacker"},
                "email_verification": None,
            }
        },
    )
    assert resp.status_code == 200, resp.text
    body_prefs = resp.json()["user"].get("preferences") or {}
    assert body_prefs.get("theme") == "dark"
    assert "reset_token" not in body_prefs
    assert "email_verification" not in body_prefs

    refreshed = await User.get(test_user.id)
    assert refreshed is not None
    stored = refreshed.preferences or {}
    assert stored.get("theme") == "dark"
    assert stored.get("reset_token") == {"hash": "keep-me", "attempts": 1}
    assert stored.get("email_verification") == {"hash": "otp-hash", "attempts": 0}


@pytest.mark.asyncio
async def test_unfiltered_list_tags_post_filters_by_policy(monkeypatch):
    """When neither track_id nor app_id is set, tags need parent *.read."""
    from app.api.tags import list_tags
    from app.schemas.policy import Decision

    class _Tag:
        def __init__(self, _id: str, track_id: str = "", app_id: str = ""):
            self.id = _id
            self.track_id = track_id
            self.app_id = app_id

    tags_in_ws = [
        _Tag("tag_ok", track_id="t_ok"),
        _Tag("tag_denied", track_id="t_denied"),
        _Tag("tag_app", app_id="a_ok"),
    ]

    async def fake_policy(*, subject, action, resource):
        allowed = resource.id in ("t_ok", "a_ok")
        return Decision(allowed=allowed, reason="test")

    async def fake_export(node):
        return {
            "id": node.id,
            "track_id": getattr(node, "track_id", None),
            "app_id": getattr(node, "app_id", None),
        }

    monkeypatch.setattr("app.api.tags.resolve_principal_id", lambda _req: "user-1")
    monkeypatch.setattr(
        "app.api.tags.resolve_workspace_id_from_request",
        AsyncMock(return_value="ws-1"),
    )
    monkeypatch.setattr(
        "app.api.tags.App.find",
        AsyncMock(return_value=[SimpleNamespace(id="a_ok")]),
    )
    monkeypatch.setattr(
        "app.api.tags.Track.find",
        AsyncMock(
            return_value=[
                SimpleNamespace(id="t_ok"),
                SimpleNamespace(id="t_denied"),
            ]
        ),
    )
    monkeypatch.setattr("app.api.tags.Tag.find", AsyncMock(return_value=tags_in_ws))
    monkeypatch.setattr("app.api.tags.policy_evaluate", fake_policy)
    monkeypatch.setattr("app.api.tags.export_node", fake_export)

    result = await list_tags(request=SimpleNamespace(), track_id=None, app_id=None)
    ids = {t["id"] for t in result["tags"]}
    assert ids == {"tag_ok", "tag_app"}
    assert result["total"] == 2


def test_secret_key_rejects_config_default():
    from app.main import (
        _CONFIG_DEFAULT_SECRET,
        _DEFAULT_WEAK_SECRET,
        _secret_key_is_acceptable,
    )

    assert _secret_key_is_acceptable(_DEFAULT_WEAK_SECRET) is False
    assert _secret_key_is_acceptable(_CONFIG_DEFAULT_SECRET) is False
    assert _secret_key_is_acceptable("short") is False
    assert _secret_key_is_acceptable("a-strong-enough-secret-key-32chars-min!!") is True
