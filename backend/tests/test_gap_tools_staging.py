"""Staging + executor wiring for newly reconciled gap tools."""

from __future__ import annotations

import pytest

from app.agentive import staging_executors as se
from app.agentive.tooling.bindings import (
    TOOL_BINDINGS,
    _stage_delete_comment,
    _stage_edit_comment,
    _stage_mint_share_link,
    _stage_set_exclusion,
)
from app.agentive.tooling.dispatch import ToolResult, dispatch_tool


def test_edit_comment_stager_shape():
    staged = _stage_edit_comment(
        {"comment_id": "n.Comment.abc", "text": "updated body"}
    )
    assert staged["kind"] == "edit_comment"
    assert staged["payload"] == {
        "comment_id": "n.Comment.abc",
        "text": "updated body",
    }


def test_delete_comment_stager_shape():
    staged = _stage_delete_comment({"comment_id": "n.Comment.xyz"})
    assert staged["kind"] == "delete_comment"
    assert staged["payload"] == {"comment_id": "n.Comment.xyz"}


def test_set_exclusion_stager_shape():
    staged = _stage_set_exclusion(
        {
            "resource_type": "track",
            "resource_id": "n.Track.t1",
            "user_id": "o.User.u2",
        }
    )
    assert staged["kind"] == "set_exclusion"
    assert staged["payload"]["user_id"] == "o.User.u2"


def test_mint_share_link_stager_shape():
    staged = _stage_mint_share_link(
        {
            "resource_type": "entry",
            "resource_id": "n.Entry.e1",
            "role": "viewer",
        }
    )
    assert staged["kind"] == "mint_share_link"
    assert staged["payload"]["role"] == "viewer"


@pytest.mark.asyncio
async def test_x_edit_comment_forwards_to_handler(monkeypatch):
    seen: dict = {}

    async def _fake_handler(request, **kwargs):
        seen.update(kwargs)
        return {"comment": {"id": kwargs["comment_id"]}}

    async def _fake_user(_user_id: str):
        return object()

    monkeypatch.setattr(se, "_resolve_auth_user", _fake_user)
    monkeypatch.setattr(
        "app.api.comments.update_comment",
        _fake_handler,
    )
    result = await se._x_edit_comment(
        "o.User.test",
        {"comment_id": "n.Comment.abc", "text": "hello"},
    )
    assert not result.get("error")
    assert seen == {"comment_id": "n.Comment.abc", "text": "hello"}


@pytest.mark.asyncio
async def test_x_delete_comment_forwards_to_handler(monkeypatch):
    seen: dict = {}

    async def _fake_handler(request, comment_id: str):
        seen["comment_id"] = comment_id
        return {"message": "deleted"}

    async def _fake_user(_user_id: str):
        return object()

    monkeypatch.setattr(se, "_resolve_auth_user", _fake_user)
    monkeypatch.setattr(
        "app.api.comments.delete_comment",
        _fake_handler,
    )
    result = await se._x_delete_comment(
        "o.User.test",
        {"comment_id": "n.Comment.del"},
    )
    assert not result.get("error")
    assert seen == {"comment_id": "n.Comment.del"}


def test_mark_notification_read_direct_binding():
    binding = TOOL_BINDINGS["integral_mark_notification_read"]
    assert binding.direct_ref is not None
    assert binding.stager is None
    assert binding.direct_param_map({"notification_id": "n.Notification.1"}) == {
        "notification_id": "n.Notification.1"
    }


@pytest.mark.asyncio
async def test_mark_notification_read_direct_dispatch(monkeypatch):
    async def _fake_mark(user_id: str, notification_id: str):
        assert user_id == "o.User.test"
        assert notification_id == "n.Notification.9"
        return {"ok": True}

    async def _allow_policy(*_a, **_kw):
        return None

    monkeypatch.setattr(
        "app.agentive.services.direct_tools.mark_notification_read_for_dispatch",
        _fake_mark,
    )
    monkeypatch.setattr(
        "app.agentive.tooling.dispatch.enforce_tool_policy",
        _allow_policy,
    )
    result = await dispatch_tool(
        "integral_mark_notification_read",
        {"notification_id": "n.Notification.9"},
        principal_id="o.User.test",
        scope=None,
    )
    assert isinstance(result, ToolResult)
    assert not result.is_error
    assert result.data == {"ok": True}
