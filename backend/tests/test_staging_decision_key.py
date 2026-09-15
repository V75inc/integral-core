"""The re-stage block keys on the DECISION, not on the container (B1 / B2).

``_staging_target_key`` took the first id it found in the payload, which for
create-shaped kinds is the CONTAINER (the track an entry goes into, the entry
a comment lands on). So every second create into the same container read as
"the same decision" and was refused — two different entries for one track,
two comments on one entry, two views on one track. Create-shaped kinds now
key on (kind, container, normalized title/name), falling back to a payload
fingerprint; update/delete-shaped kinds still key on the resource id.

And when the block does fire through ``dispatch_tool``, the model must see
``staging_blocked`` plus the blocker — not a generic ``internal_error``.
"""

from __future__ import annotations

import pytest

from app.agentive import staging
from app.agentive.staging import StagingBlockedError, create_staged_change


@pytest.fixture(autouse=True)
def _clean():
    staging._reset_for_tests()
    yield
    staging._reset_for_tests()


async def _mint(*, kind: str, payload: dict, summary: str = "card"):
    return await create_staged_change(
        user_id="u1",
        session_id="s1",
        kind=kind,
        summary=summary,
        diff_human="- change",
        diff_machine={"op": kind, **payload},
        payload=payload,
    )


# --- create-shaped kinds: a second create in the same container is a new decision


@pytest.mark.smoke
@pytest.mark.asyncio
async def test_two_create_entry_cards_for_one_track_both_mint():
    a = await _mint(
        kind="create_entry", payload={"track_id": "n.Track.1", "title": "A"}
    )
    b = await _mint(
        kind="create_entry", payload={"track_id": "n.Track.1", "title": "B"}
    )
    assert a.token != b.token
    assert len(staging._tokens) == 2


@pytest.mark.smoke
@pytest.mark.asyncio
async def test_identical_title_in_the_same_track_still_blocks():
    first = await _mint(
        kind="create_entry", payload={"track_id": "n.Track.1", "title": "Same"}
    )
    with pytest.raises(StagingBlockedError) as exc:
        # Whitespace / case differences are not a different decision.
        await _mint(
            kind="create_entry", payload={"track_id": "n.Track.1", "title": "  same "}
        )
    assert exc.value.blocker.token == first.token


@pytest.mark.smoke
@pytest.mark.asyncio
async def test_same_title_in_a_different_track_is_a_different_decision():
    await _mint(kind="create_entry", payload={"track_id": "n.Track.1", "title": "A"})
    other = await _mint(
        kind="create_entry", payload={"track_id": "n.Track.2", "title": "A"}
    )
    assert other.state == "pending"


@pytest.mark.smoke
@pytest.mark.asyncio
async def test_two_comments_on_one_entry_both_mint():
    await _mint(kind="add_comment", payload={"entry_id": "n.Entry.1", "body": "one"})
    await _mint(kind="add_comment", payload={"entry_id": "n.Entry.1", "body": "two"})
    assert len(staging._tokens) == 2


@pytest.mark.smoke
@pytest.mark.asyncio
async def test_the_same_comment_twice_still_blocks():
    await _mint(kind="add_comment", payload={"entry_id": "n.Entry.1", "body": "one"})
    with pytest.raises(StagingBlockedError):
        await _mint(
            kind="add_comment", payload={"entry_id": "n.Entry.1", "body": "one"}
        )


@pytest.mark.smoke
@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("kind", "container", "name_key"),
    [
        ("create_track", {"app_id": "n.App.1"}, "title"),
        ("save_view", {"track_id": "n.Track.1"}, "name"),
        ("create_tag", {"track_id": "n.Track.1"}, "name"),
        ("create_dashboard", {"app_id": "n.App.1"}, "name"),
    ],
)
async def test_other_create_shaped_kinds_key_on_container_plus_name(
    kind, container, name_key
):
    await _mint(kind=kind, payload={**container, name_key: "First"})
    await _mint(kind=kind, payload={**container, name_key: "Second"})
    assert len(staging._tokens) == 2
    with pytest.raises(StagingBlockedError):
        await _mint(kind=kind, payload={**container, name_key: "first"})


# --- update/delete-shaped kinds: the resource id is still the decision


@pytest.mark.smoke
@pytest.mark.asyncio
async def test_update_entry_on_the_same_entry_still_blocks():
    first = await _mint(
        kind="update_entry", payload={"entry_id": "n.Entry.1", "title": "X"}
    )
    with pytest.raises(StagingBlockedError) as exc:
        await _mint(
            kind="update_entry", payload={"entry_id": "n.Entry.1", "title": "Y"}
        )
    assert exc.value.blocker.token == first.token


@pytest.mark.smoke
@pytest.mark.asyncio
async def test_delete_entry_on_the_same_entry_still_blocks():
    await _mint(kind="delete_entry", payload={"entry_id": "n.Entry.1"})
    with pytest.raises(StagingBlockedError):
        await _mint(kind="delete_entry", payload={"entry_id": "n.Entry.1"})


def test_every_create_shaped_kind_is_driven_from_one_table():
    # The table is the single place a kind's key shape is decided.
    for kind in ("create_entry", "add_comment", "create_track", "save_view"):
        assert kind in staging._CREATE_SHAPED_KINDS
    for kind in ("update_entry", "delete_entry", "delete_track"):
        assert kind not in staging._CREATE_SHAPED_KINDS


# --- B2: the block reaches the model through dispatch_tool


@pytest.mark.smoke
@pytest.mark.asyncio
async def test_dispatch_surfaces_staging_blocked_with_the_blocker(
    bind_fresh_graph_context_for_async_tests,
):
    from app.agentive.tooling.dispatch import dispatch_tool
    from tests.test_tooling_dispatch_propose import _bootstrap_principal_and_track

    auth_user_id, workspace_id, track_id = await _bootstrap_principal_and_track()
    args = {"track_id": track_id, "title": "Drafted twice", "text": "body"}

    first = await dispatch_tool(
        "integral_create_entry",
        args,
        principal_id=auth_user_id,
        scope=workspace_id,
        session_id="s1",
    )
    assert not first.is_error, first

    second = await dispatch_tool(
        "integral_create_entry",
        args,
        principal_id=auth_user_id,
        scope=workspace_id,
        session_id="s1",
    )
    assert second.is_error
    assert second.error_code == "staging_blocked"
    assert "Do not stage it again" in second.message
    assert second.data["blocker"]["token"] == first.data["token"]

    # A different title into the same track is a different decision (B1 at
    # the dispatch level).
    third = await dispatch_tool(
        "integral_create_entry",
        {**args, "title": "Another one"},
        principal_id=auth_user_id,
        scope=workspace_id,
        session_id="s1",
    )
    assert not third.is_error, third


# --- tag membership: the decision is (entry, tag), not the entry alone


@pytest.mark.smoke
@pytest.mark.asyncio
async def test_removing_two_tags_from_one_entry_both_mint():
    """``remove_entry_tag`` is not create-shaped, so it keyed on ``entry_id``
    alone and the second untag of the same entry was refused as a duplicate
    decision — the same class of bug as the create-shaped keys above."""
    a = await _mint(
        kind="remove_entry_tag", payload={"entry_id": "n.Entry.1", "tag_id": "n.Tag.A"}
    )
    b = await _mint(
        kind="remove_entry_tag", payload={"entry_id": "n.Entry.1", "tag_id": "n.Tag.B"}
    )
    assert a.token != b.token
    assert len(staging._tokens) == 2


@pytest.mark.smoke
@pytest.mark.asyncio
async def test_removing_the_same_tag_twice_still_blocks():
    """The exact same untag is still one decision and still blocks."""
    first = await _mint(
        kind="remove_entry_tag", payload={"entry_id": "n.Entry.1", "tag_id": "n.Tag.A"}
    )
    with pytest.raises(StagingBlockedError) as exc:
        await _mint(
            kind="remove_entry_tag",
            payload={"entry_id": "n.Entry.1", "tag_id": "n.Tag.A"},
        )
    assert exc.value.blocker.token == first.token


@pytest.mark.smoke
@pytest.mark.asyncio
async def test_adding_two_tags_to_one_entry_both_mint():
    """Two different tags on one entry are two decisions."""
    await _mint(
        kind="add_entry_tag", payload={"entry_id": "n.Entry.1", "tag_id": "n.Tag.A"}
    )
    await _mint(
        kind="add_entry_tag", payload={"entry_id": "n.Entry.1", "tag_id": "n.Tag.B"}
    )
    assert len(staging._tokens) == 2


@pytest.mark.smoke
@pytest.mark.asyncio
async def test_the_same_tag_on_two_entries_is_two_decisions():
    """The entry is still part of the key — one tag, two entries, two cards."""
    await _mint(
        kind="remove_entry_tag", payload={"entry_id": "n.Entry.1", "tag_id": "n.Tag.A"}
    )
    await _mint(
        kind="remove_entry_tag", payload={"entry_id": "n.Entry.2", "tag_id": "n.Tag.A"}
    )
    assert len(staging._tokens) == 2
