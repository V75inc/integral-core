"""Reaction selection preserves legacy data and the entry permission boundary."""

import asyncio

import pytest

from app.api.errors import InsufficientPermissionsError
from app.models.edges import COLLABORATES_ON, EXCLUDED_FROM, HAS_COMMENT
from app.models.nodes import Comment, Entry
from app.services.reactions import mutate_reaction, reaction_summary
from tests.test_comment_permission_matrix import _member, _org_fixture

pytestmark = [pytest.mark.smoke, pytest.mark.asyncio]


async def fixture(label):
    """Fixture."""
    ws, _, track, entry = await _org_fixture(label)
    user = await _member(ws, label, "member")
    await user.connect(track, edge=COLLABORATES_ON, role="commenter")
    return ws, track, entry, user


async def test_select_replaces_and_retry_is_idempotent():
    """Test select replaces and retry is idempotent."""
    _, _, entry, user = await fixture("reaction-select")
    from app.models.edges import REACTS_TO

    for emoji in ["👍", "👎", "❤️", "😊", "😢"]:
        for _ in range(2):
            await mutate_reaction(user_id=user.id, target_id=entry.id, emoji=emoji)
        assert await reaction_summary(await Entry.get(entry.id)) == {emoji: [user.id]}
        context = await user.get_context()
        edges = await context.find_edges_between(
            user.id, entry.id, edge_class=REACTS_TO
        )
        assert len(edges) == 1
        assert edges[0].emoji == emoji


async def test_legacy_multi_reactions_survive_until_actor_selects():
    """Test legacy multi reactions survive until actor selects."""
    _, _, entry, user = await fixture("reaction-legacy")
    entry.reactions = {"🔥": [user.id, "historical-user"], "👍": [user.id]}
    await entry.save()
    assert len(await reaction_summary(entry)) == 2
    await mutate_reaction(user_id=user.id, target_id=entry.id, emoji="👎")
    assert await reaction_summary(await Entry.get(entry.id)) == {
        "🔥": ["historical-user"],
        "👎": [user.id],
    }


async def test_comment_selection_and_duplicate_calls():
    """Test comment selection and duplicate calls."""
    _, _, entry, user = await fixture("reaction-comment")
    comment = await Comment.create(author_id=user.id, text="Hello")
    await entry.connect(comment, edge=HAS_COMMENT)
    await asyncio.gather(
        *[
            mutate_reaction(
                user_id=user.id, target_id=comment.id, target_kind="comment", emoji="👎"
            )
            for _ in range(4)
        ]
    )
    assert await reaction_summary(comment) == {"👎": [user.id]}


async def test_viewer_cannot_select_but_can_retract_and_exclusion_blocks():
    """Test viewer cannot select but can retract and exclusion blocks."""
    _, track, entry, user = await fixture("reaction-viewer")
    await mutate_reaction(user_id=user.id, target_id=entry.id, emoji="👍")
    context = await user.get_context()
    for edge in await context.find_edges_between(
        user.id, track.id, edge_class=COLLABORATES_ON
    ):
        await edge.delete()
    await user.connect(track, edge=COLLABORATES_ON, role="viewer")
    with pytest.raises(InsufficientPermissionsError):
        await mutate_reaction(user_id=user.id, target_id=entry.id, emoji="👎")
    result = await mutate_reaction(
        user_id=user.id, target_id=entry.id, emoji="👍", mode="remove"
    )
    assert result["reactions"] == {}
    await user.connect(entry, edge=EXCLUDED_FROM)
    with pytest.raises(InsufficientPermissionsError):
        await mutate_reaction(
            user_id=user.id, target_id=entry.id, emoji="👍", mode="remove"
        )


async def test_nonmember_cannot_react():
    """Test nonmember cannot react."""
    _, _, entry, _ = await fixture("reaction-target")
    _, _, _, outsider = await fixture("reaction-outsider")
    with pytest.raises(InsufficientPermissionsError):
        await mutate_reaction(user_id=outsider.id, target_id=entry.id, emoji="👍")


async def test_duplicate_edges_are_reconciled_on_selection():
    """Test duplicate edges are reconciled on selection."""
    from app.models.edges import REACTS_TO

    _, _, entry, user = await fixture("reaction-duplicates")
    for emoji in ["👍", "❤️"]:
        await user.connect(entry, edge=REACTS_TO, emoji=emoji, principal_id=user.id)
    await mutate_reaction(user_id=user.id, target_id=entry.id, emoji="👎")
    context = await user.get_context()
    edges = await context.find_edges_between(user.id, entry.id, edge_class=REACTS_TO)
    assert len(edges) == 1
    assert edges[0].emoji == "👎"


async def test_multiple_principals_concurrently_select_without_lost_updates():
    """Test multiple principals concurrently select without lost updates."""
    ws, track, entry, first = await fixture("reaction-concurrent")
    second = await _member(ws, "reaction-second", "member")
    await second.connect(track, edge=COLLABORATES_ON, role="commenter")
    await asyncio.gather(
        mutate_reaction(user_id=first.id, target_id=entry.id, emoji="👍"),
        mutate_reaction(user_id=second.id, target_id=entry.id, emoji="👎"),
    )
    result = await reaction_summary(await Entry.get(entry.id))
    assert result == {"👍": [first.id], "👎": [second.id]}


async def test_explicit_scope_cannot_redirect_reaction():
    """Test explicit scope cannot redirect reaction."""
    from starlette.requests import Request

    _, _, entry, user = await fixture("reaction-scoped")
    other, _, _, _ = await fixture("reaction-other-scope")
    # Membership in both workspaces still cannot authorize a write through
    # an explicitly selected workspace other than the target's workspace.
    from app.models.edges import IS_MEMBER_OF

    await user.connect(other, edge=IS_MEMBER_OF, role="member")
    request = Request(
        {"type": "http", "headers": [(b"x-integral-scope", f"ws:{other.id}".encode())]}
    )
    with pytest.raises(InsufficientPermissionsError):
        await mutate_reaction(
            user_id=user.id, target_id=entry.id, emoji="👍", request=request
        )


@pytest.mark.parametrize("emoji", ["😂", "😮", "😡", "🔥"])
async def test_retired_select_does_not_mutate_legacy_data(emoji):
    """Retired choices are readable history but cannot become new selections."""
    from app.api.errors import BadRequestError

    _, _, entry, user = await fixture("reaction-invalid")
    entry.reactions = {
        "😂": [user.id, "historical-user"],
        "😮": [user.id],
        "😡": [user.id],
        "🔥": [user.id],
    }
    await entry.save()
    before = await reaction_summary(entry)
    with pytest.raises(BadRequestError):
        await mutate_reaction(user_id=user.id, target_id=entry.id, emoji=emoji)
    assert await reaction_summary(await Entry.get(entry.id)) == before


async def test_legacy_post_still_accepts_retired_emoji_before_down_selection():
    """Old clients retain toggles while explicit selection replaces history."""
    _, _, entry, user = await fixture("reaction-toggle-compatibility")
    await mutate_reaction(
        user_id=user.id, target_id=entry.id, emoji="😂", mode="toggle"
    )
    assert await reaction_summary(await Entry.get(entry.id)) == {"😂": [user.id]}
    await mutate_reaction(user_id=user.id, target_id=entry.id, emoji="👎")
    assert await reaction_summary(await Entry.get(entry.id)) == {"👎": [user.id]}


@pytest.mark.parametrize("emoji", ["👍", "👎", "❤️", "😊", "😢"])
async def test_entry_and_comment_json_selection_contract(
    authenticated_client, test_user, emoji
):
    """PUT accepts the frontend JSON shape and exports both target summaries."""
    from tests.test_crud_entries import TestEntriesCRUD

    client = authenticated_client
    track_id = await TestEntriesCRUD()._create_track(client)
    response = await client.post(
        "/api/entries", json={"track_id": track_id, "title": "Reaction contracts"}
    )
    entry_id = response.json()["entry"]["id"]
    response = await client.post(
        f"/api/entries/{entry_id}/comments", json={"text": "React here"}
    )
    comment_id = response.json()["comment"]["id"]
    for target in [f"entries/{entry_id}", f"comments/{comment_id}"]:
        for _ in range(2):
            response = await client.put(
                f"/api/{target}/reactions", json={"emoji": emoji}
            )
            assert response.status_code == 200, response.text
            assert len(response.json()["reactions"][emoji]) == 1
    response = await client.get(f"/api/entries/{entry_id}/comments")
    assert response.json()["comments"][0]["reactions"][emoji]


async def test_postgres_reaction_and_projection_rollback_with_outbox_failure(
    monkeypatch,
):
    """A failed durable event cannot leave a committed reaction or projection."""
    from app.services import resource_events
    from app.services.app_operations.transaction_scope import (
        graph_transaction_available,
    )

    if not graph_transaction_available():
        pytest.skip("Requires PostgreSQL graph transactions")
    _, _, entry, user = await fixture("reaction-rollback")

    async def fail(*args: object, **kwargs: object):
        raise RuntimeError("outbox unavailable")

    monkeypatch.setattr(resource_events, "queue_resource_event", fail)
    with pytest.raises(RuntimeError, match="outbox unavailable"):
        await mutate_reaction(user_id=user.id, target_id=entry.id, emoji="👍")
    assert await reaction_summary(await Entry.get(entry.id)) == {}


async def test_batch_export_reads_reaction_edges_once(monkeypatch):
    """One list window uses one reaction-edge query independent of item count."""
    from jvspatial.core.context import get_default_context

    from app.api.utils import export_nodes
    from app.models.edges import REACTS_TO

    _, _, entry, user = await fixture("reaction-batch")
    comment = await Comment.create(author_id=user.id, text="Batch export")
    await entry.connect(comment, edge=HAS_COMMENT)
    await mutate_reaction(user_id=user.id, target_id=entry.id, emoji="👍")
    await mutate_reaction(
        user_id=user.id, target_id=comment.id, target_kind="comment", emoji="👎"
    )
    context = get_default_context()
    original = context.find
    calls = []

    async def counted(cls, *args: object, **kwargs: object):
        if cls is REACTS_TO:
            calls.append(args)
        return await original(cls, *args, **kwargs)

    monkeypatch.setattr(context, "find", counted)
    exports = await export_nodes([await Entry.get(entry.id), comment])
    assert exports[0]["reactions"] == {"👍": [user.id]}
    assert exports[1]["reactions"] == {"👎": [user.id]}
    assert len(calls) == 1
