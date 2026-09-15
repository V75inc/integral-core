"""Tests for chat @ / # entity reference resolution."""

import pytest

from app.schemas.chat_entity_refs import EntityRef
from app.services.chat_entity_refs import (
    extract_hash_tokens,
    focused_ids_from_resolved,
    resolve_entity_refs,
)
from app.services.mentions import extract_mention_tokens


def test_extract_hash_tokens_dedupes():
    text = "check #CRM and #CRM again"
    assert extract_hash_tokens(text) == ["CRM"]


def test_extract_mention_tokens_unchanged():
    assert extract_mention_tokens("hey @jason and @Eldon_Marks") == [
        "jason",
        "Eldon_Marks",
    ]


def test_focused_ids_from_resolved_single_each():
    from app.schemas.chat_entity_refs import ResolvedEntityRef

    resolved = [
        ResolvedEntityRef(kind="app", entity_id="n.App.1", label="CRM"),
        ResolvedEntityRef(kind="track", entity_id="n.Track.1", label="Tasks"),
    ]
    track_id, app_id = focused_ids_from_resolved(resolved)
    assert track_id == "n.Track.1"
    assert app_id == "n.App.1"


def test_focused_ids_from_resolved_multiple_skips():
    from app.schemas.chat_entity_refs import ResolvedEntityRef

    resolved = [
        ResolvedEntityRef(kind="app", entity_id="n.App.1", label="A"),
        ResolvedEntityRef(kind="app", entity_id="n.App.2", label="B"),
    ]
    track_id, app_id = focused_ids_from_resolved(resolved)
    assert track_id is None
    assert app_id is None


@pytest.mark.asyncio
async def test_resolve_explicit_ref_validates_kind(monkeypatch):
    """Explicit refs are trusted when validation passes."""

    class FakeUser:
        id = "n.User.abc"
        display_name = "Jason Smith"
        email = "jason@example.com"

    async def fake_get(_id):
        return FakeUser()

    async def fake_apps(_uid):
        return []

    async def fake_tracks(_uid):
        return []

    monkeypatch.setattr(
        "app.services.chat_entity_refs._load_user",
        fake_get,
    )
    monkeypatch.setattr(
        "app.services.chat_entity_refs.get_user_accessible_apps",
        fake_apps,
    )
    monkeypatch.setattr(
        "app.services.chat_entity_refs.get_user_accessible_tracks",
        fake_tracks,
    )

    explicit = [
        EntityRef(kind="user", id="n.User.abc", label="jason"),
    ]
    result = await resolve_entity_refs(
        "hey @jason",
        explicit,
        user_id="n.User.self",
        workspace_id="ws-1",
    )
    assert len(result.resolved) == 1
    assert result.resolved[0].entity_id == "n.User.abc"
    assert "Jason Smith" in result.context_preamble
