"""Durable staging store — outstanding staged changes survive a restart.

The staging primitive keeps live state in an in-memory dict for speed, with a
write-through durable mirror (:mod:`app.agentive.staging_store`) so a process
restart no longer orphans pending/blessed approval cards. These tests simulate
a restart by clearing ONLY the in-memory caches (``staging._tokens`` etc.) and
asserting the token is still resolvable — i.e. it was reloaded from the store.

Terminal states (consumed / revoked / expired) are intentionally NOT durable:
the store holds only still-actionable cards, so a terminal transition removes
the row.
"""

from __future__ import annotations

import pytest

from app.agentive import staging, staging_store
from app.agentive.staging import (
    StagingError,
    bless_token,
    consume_token,
    create_staged_change,
    get_pending_for_user,
    revoke_token,
)


@pytest.fixture(autouse=True)
def _clean_staging():
    """Reset the in-memory staging caches around each test."""
    staging._reset_for_tests()
    yield
    staging._reset_for_tests()


def _simulate_restart() -> None:
    """Drop the in-memory caches, mimicking a fresh process boot.

    The durable store rows persist (per-test JSON DB), so anything the store
    holds must be reloadable after this call.
    """
    staging._tokens.clear()
    staging._autonomy.clear()
    staging._open_batches.clear()


async def _mint(user_id: str = "u1", session_id: str = "s1", kind: str = "batch"):
    return await create_staged_change(
        user_id=user_id,
        session_id=session_id,
        kind=kind,
        summary="Capture — Contoso sync",
        diff_human="- meeting\n- 2 tasks",
        diff_machine={"op_count": 3},
        payload={"operations": [{"kind": "create_entry"}, {"kind": "create_entry"}]},
    )


@pytest.mark.asyncio
async def test_bless_survives_restart():
    """A pending card can still be blessed after a restart (the reported bug)."""
    sc = await _mint()
    _simulate_restart()
    assert staging._tokens == {}  # cache genuinely empty

    blessed = await bless_token(user_id="u1", token=sc.token)
    assert blessed.state == "blessed"


@pytest.mark.asyncio
async def test_consume_survives_restart_and_returns_payload():
    """Bless, restart, then consume — the original payload is intact."""
    sc = await _mint()
    await bless_token(user_id="u1", token=sc.token)
    _simulate_restart()

    payload = await consume_token(user_id="u1", token=sc.token, expected_kind="batch")
    assert len(payload["operations"]) == 2
    # Terminal → durable row removed.
    assert await staging_store.load(sc.token) is None


@pytest.mark.asyncio
async def test_pending_inbox_rehydrates_after_restart():
    """get_pending_for_user repopulates the cache from the store on cold boot."""
    sc = await _mint()
    _simulate_restart()

    pending = await get_pending_for_user("u1")
    tokens = {p.token for p in pending}
    assert sc.token in tokens


@pytest.mark.asyncio
async def test_revoke_survives_restart_and_clears_store():
    """A pending card can be revoked after a restart; the row is then gone."""
    sc = await _mint()
    _simulate_restart()

    revoked = await revoke_token(user_id="u1", token=sc.token)
    assert revoked.state == "revoked"
    assert await staging_store.load(sc.token) is None


@pytest.mark.asyncio
async def test_consumed_token_blocks_second_consume_after_restart():
    """Terminal-state enforcement holds across a restart boundary."""
    sc = await _mint()
    await bless_token(user_id="u1", token=sc.token)
    await consume_token(user_id="u1", token=sc.token, expected_kind="batch")

    _simulate_restart()
    # Store row was removed on consume; a reload finds nothing → unknown_token.
    with pytest.raises(StagingError) as exc:
        await consume_token(user_id="u1", token=sc.token, expected_kind="batch")
    assert exc.value.code in ("unknown_token", "already_consumed")


@pytest.mark.asyncio
async def test_wrong_user_rejected_after_restart():
    """Ownership check still applies to a store-reloaded token."""
    sc = await _mint(user_id="owner")
    _simulate_restart()

    with pytest.raises(StagingError) as exc:
        await bless_token(user_id="intruder", token=sc.token)
    assert exc.value.code == "wrong_user"
