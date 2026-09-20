"""An unresolved approval card is live turn state, not a one-shot event.

The [SYSTEM:STAGING-RESOLVED] marker only ever fired when the user ACTED. A
card the user ignored produced nothing at all, and nothing carried it into the
next turn, so the model saw its own "I've staged X" with no outcome — and
re-proposed the same write, or reported it as done. These tests pin both
halves: the server refuses the duplicate, and the outstanding card is carried
forward.
"""

from datetime import timedelta

import pytest

from app.agentive import staging
from app.agentive.staging import (
    StagingBlockedError,
    _format_staging_closure_marker,
    create_staged_change,
    format_staging_pending_marker,
    list_unresolved_for_session,
)


@pytest.fixture(autouse=True)
def _clean():
    staging._tokens.clear()
    staging._autonomy.clear()
    yield
    staging._tokens.clear()


async def _mint(*, kind="update_entry", entry_id="n.Entry.1", summary="Set title"):
    return await create_staged_change(
        user_id="u1",
        session_id="s1",
        kind=kind,
        summary=summary,
        diff_human="- change",
        diff_machine={"entry_id": entry_id},
        payload={"entry_id": entry_id},
    )


@pytest.mark.asyncio
async def test_restaging_the_same_decision_is_blocked():
    """The reported bug: user ignores the card, agent proposes it again."""
    first = await _mint()
    with pytest.raises(StagingBlockedError) as exc:
        await _mint()
    assert exc.value.blocker.token == first.token
    message = str(exc.value)
    assert "already waiting for the user's approval" in message
    # The message must tell the model what to do instead of just failing.
    assert "Do not stage it again" in message
    assert len(staging._tokens) == 1  # no second card was minted


@pytest.mark.asyncio
async def test_a_different_target_is_not_blocked():
    await _mint(entry_id="n.Entry.1")
    other = await _mint(entry_id="n.Entry.2")
    assert other.state == "pending"
    assert len(staging._tokens) == 2


@pytest.mark.asyncio
async def test_a_resolved_card_stops_blocking():
    first = await _mint()
    await staging.revoke_token(user_id="u1", token=first.token)
    again = await _mint()
    assert again.token != first.token


@pytest.mark.asyncio
async def test_blessed_but_unconsumed_still_blocks():
    """Approved-but-not-written is still an open decision; re-staging would
    double the write."""
    first = await _mint()
    await staging.bless_token(user_id="u1", token=first.token)
    with pytest.raises(StagingBlockedError):
        await _mint()


@pytest.mark.asyncio
async def test_unresolved_listing_carries_pending_and_blessed():
    a = await _mint(entry_id="n.Entry.1")
    b = await _mint(entry_id="n.Entry.2")
    await staging.bless_token(user_id="u1", token=b.token)

    items = await list_unresolved_for_session("u1", "s1")
    tokens = {sc.token for sc in items}
    assert tokens == {a.token, b.token}
    # Oldest first, so the model sees them in the order they were proposed.
    assert items[0].token == a.token


@pytest.mark.asyncio
async def test_unresolved_listing_is_scoped_to_the_session():
    await _mint()
    assert await list_unresolved_for_session("u1", "other-session") == []
    assert await list_unresolved_for_session("someone-else", "s1") == []
    assert await list_unresolved_for_session("u1", None) == []


@pytest.mark.asyncio
async def test_pending_marker_is_machine_parseable():
    sc = await _mint(summary="Set title to Head of Platform")
    marker = format_staging_pending_marker([sc])
    assert marker.startswith("[SYSTEM:STAGING-PENDING] ")
    assert "kind=update_entry" in marker
    assert 'summary="Set title to Head of Platform"' in marker
    # Single line — it lands as one utterance in the interaction chain.
    assert "\n" not in marker


def test_pending_marker_is_empty_when_nothing_is_outstanding():
    assert format_staging_pending_marker([]) == ""


@pytest.mark.asyncio
async def test_consumed_profile_revision_marker_requires_publish_lifecycle():
    """A revision approval changes a draft, never the live profile itself."""
    sc = await _mint(kind="propose_profile_revision")
    sc.payload = {"draft_id": "draft-123"}
    sc.state = "consumed"

    marker = _format_staging_closure_marker(sc)

    assert 'draft_id="draft-123"' in marker
    assert "NOT published" in marker
    assert "integral_diff_profile_draft" in marker
    assert "integral_publish_profile_draft" in marker
    assert "\n" not in marker


# --- blessed-but-unconsumed must not wedge the decision -------------------


@pytest.mark.asyncio
async def test_a_stale_blessed_card_stops_blocking_and_is_revoked():
    """A bless whose execute never arrived (agent crashed, tool errored) would
    otherwise block every retry until TTL — the user approved something,
    nothing happened, and there is no way out."""
    first = await _mint()
    await staging.bless_token(user_id="u1", token=first.token)
    # Age the bless past the grace window.
    first.blessed_at = first.blessed_at - timedelta(
        seconds=staging._BLESSED_GRACE_SECONDS + 1
    )

    again = await _mint()
    assert again.token != first.token
    # The stale token must be REVOKED, not merely ignored: a token left
    # blessed could still be consumed later, which is the double write the
    # block exists to prevent.
    assert first.state == "revoked"


@pytest.mark.asyncio
async def test_a_fresh_blessed_card_still_blocks():
    """Inside the window the agent is simply expected to consume it."""
    first = await _mint()
    await staging.bless_token(user_id="u1", token=first.token)
    with pytest.raises(StagingBlockedError):
        await _mint()
    assert first.state == "blessed"


@pytest.mark.asyncio
async def test_blessed_blocker_tells_the_agent_to_execute_not_wait():
    first = await _mint()
    await staging.bless_token(user_id="u1", token=first.token)
    with pytest.raises(StagingBlockedError) as exc:
        await _mint()
    message = str(exc.value)
    assert "already been APPROVED" in message
    assert "execute the existing token" in message
    assert first.token in message
    # Must NOT tell the model to wait for an approval that already happened.
    assert "waiting for the user's approval" not in message


@pytest.mark.asyncio
async def test_pending_is_never_retired_by_the_grace_window():
    """The grace window is about the AGENT's obligation to consume. A pending
    card is the USER's decision and ages into expiry, never into a re-stage."""
    first = await _mint()
    first.created_at = first.created_at - timedelta(
        seconds=staging._BLESSED_GRACE_SECONDS * 10
    )
    with pytest.raises(StagingBlockedError):
        await _mint()
    assert first.state == "pending"
