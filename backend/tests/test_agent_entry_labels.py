"""Batch label resolution preserves workspace and per-entry access."""

from types import SimpleNamespace

import pytest

from app.api.errors import BadRequestError
from app.services.agent_entry_labels import resolve_entry_labels
from app.services.agent_scope import current_scope_workspace_id


@pytest.mark.asyncio
async def test_batch_resolves_only_authorized_local_entries(monkeypatch):
    """Denied, foreign and missing IDs have the same non-disclosing outcome."""
    calls = []

    async def locations(_kind, _ids, **kwargs):
        assert kwargs["workspace_hints"] == ["ws-a"]
        return {
            "n.Entry.visible": SimpleNamespace(workspace_id="ws-a", track_id="t-a"),
            "n.Entry.denied": SimpleNamespace(workspace_id="ws-a", track_id="t-a"),
            "n.Entry.foreign": SimpleNamespace(workspace_id="ws-b", track_id="t-b"),
        }

    async def policy(*, subject, action, resource):
        assert subject.id == "user-a" and action == "entry.read"
        return SimpleNamespace(allowed=resource.id != "n.Entry.denied")

    async def get_entry(identity):
        calls.append(identity)
        return SimpleNamespace(title="Allowed label", track_id="t-a")

    monkeypatch.setattr("app.services.agent_entry_labels.locate_resources", locations)
    monkeypatch.setattr("app.services.agent_entry_labels.evaluate", policy)
    monkeypatch.setattr("app.services.agent_entry_labels.Entry.get", get_entry)
    token = current_scope_workspace_id.set("ws-a")
    try:
        result = await resolve_entry_labels(
            user_id="user-a",
            entry_ids=[
                "n.Entry.visible",
                "n.Entry.denied",
                "n.Entry.foreign",
                "n.Entry.missing",
                "n.Entry.visible",
            ],
        )
    finally:
        current_scope_workspace_id.reset(token)
    assert calls == ["n.Entry.visible"]
    assert [item["title"] for item in result["entries"]] == ["Allowed label"]
    assert result["unresolved_count"] == 3


@pytest.mark.asyncio
async def test_batch_excludes_entries_moved_after_policy_check(monkeypatch):
    """A changed parent track cannot reuse the earlier authorization."""

    async def locations(_kind, _ids, **_kwargs):
        return {"n.Entry.moved": SimpleNamespace(workspace_id="ws-a", track_id="old")}

    async def policy(**_kwargs):
        return SimpleNamespace(allowed=True)

    async def get_entry(_identity):
        return SimpleNamespace(title="Do not disclose", track_id="new")

    monkeypatch.setattr("app.services.agent_entry_labels.locate_resources", locations)
    monkeypatch.setattr("app.services.agent_entry_labels.evaluate", policy)
    monkeypatch.setattr("app.services.agent_entry_labels.Entry.get", get_entry)
    token = current_scope_workspace_id.set("ws-a")
    try:
        result = await resolve_entry_labels(user_id="u", entry_ids=["n.Entry.moved"])
    finally:
        current_scope_workspace_id.reset(token)
    assert result == {"entries": [], "unresolved_count": 1}


@pytest.mark.asyncio
async def test_batch_rejects_unscoped_or_unbounded_requests():
    """The model cannot supply scope or request an unbounded lookup."""
    token = current_scope_workspace_id.set(None)
    try:
        with pytest.raises(BadRequestError):
            await resolve_entry_labels(user_id="u", entry_ids=["n.Entry.x"])
    finally:
        current_scope_workspace_id.reset(token)
    token = current_scope_workspace_id.set("ws-a")
    try:
        with pytest.raises(BadRequestError):
            await resolve_entry_labels(user_id="u", entry_ids=["n.Entry.x"] * 51)
    finally:
        current_scope_workspace_id.reset(token)
