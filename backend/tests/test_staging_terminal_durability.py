"""Terminal transitions must reach the store and the ledger (B6 / B7 / C1).

B6 — the re-stage path retired a stale blessed token in memory only: no
durable-row removal, no ledger row, no push. After a restart the row
rehydrated as ``blessed`` and could execute alongside the replacement card.

B7 — ``blessed_at`` was not persisted, so after a restart the grace window was
measured from ``created_at`` and a card approved late looked stale at once.

C1 — the ledger's ``decided_at`` always fell back to ``created_at`` because
nothing stamped ``resolved_at``.
"""

from __future__ import annotations

from datetime import timedelta

import pytest

from app.agentive import decision_ledger, staging, staging_store
from app.agentive.staging import (
    StagingBlockedError,
    bless_token,
    consume_token,
    create_staged_change,
    get_token,
    revoke_token,
)


@pytest.fixture(autouse=True)
def _clean():
    staging._reset_for_tests()
    yield
    staging._reset_for_tests()


def _simulate_restart() -> None:
    staging._tokens.clear()
    staging._autonomy.clear()
    staging._open_batches.clear()


async def _mint(*, title: str = "A"):
    return await create_staged_change(
        user_id="u1",
        session_id="s1",
        kind="update_entry",
        summary=f"Set title to {title}",
        diff_human="- change",
        diff_machine={"entry_id": "n.Entry.1"},
        payload={"entry_id": "n.Entry.1", "title": title},
    )


@pytest.fixture
def ledger_spy(monkeypatch):
    """Capture ledger writes without needing the Personal Context App."""
    seen: list = []

    async def fake_record(sc):
        seen.append((sc.token, sc.state))
        return ""

    monkeypatch.setattr(decision_ledger, "record_decision", fake_record)
    return seen


# --- B6 -----------------------------------------------------------------------


@pytest.mark.smoke
@pytest.mark.asyncio
async def test_a_stale_blessed_revoke_is_durable_and_ledgered(monkeypatch, ledger_spy):
    pushed: list = []

    async def fake_push(sc):
        pushed.append((sc.token, sc.state))

    monkeypatch.setattr(staging, "_push_state_event", fake_push)

    first = await _mint()
    await bless_token(user_id="u1", token=first.token)
    first.blessed_at = first.blessed_at - timedelta(
        seconds=staging._BLESSED_GRACE_SECONDS + 1
    )
    assert await staging_store._find_record(first.token) is not None

    again = await _mint()
    assert again.token != first.token
    assert first.state == "revoked"

    # Durable row gone — not merely reported absent because of its state.
    assert await staging_store._find_record(first.token) is None
    # A `rejected` Decision was recorded for the retired token.
    assert (first.token, "revoked") in ledger_spy
    assert decision_ledger.outcome_for_state("revoked") == "rejected"
    # And the surfaces learned about it.
    assert (first.token, "revoked") in pushed


@pytest.mark.smoke
@pytest.mark.asyncio
async def test_a_stale_blessed_token_cannot_execute_after_a_restart(ledger_spy):
    first = await _mint()
    await bless_token(user_id="u1", token=first.token)
    first.blessed_at = first.blessed_at - timedelta(
        seconds=staging._BLESSED_GRACE_SECONDS + 1
    )
    await _mint()  # retires `first`

    _simulate_restart()
    # Before the fix the row rehydrated as `blessed` here.
    assert await get_token(first.token) is None


# --- B7 -----------------------------------------------------------------------


@pytest.mark.smoke
@pytest.mark.asyncio
async def test_blessed_at_survives_a_restart():
    sc = await _mint()
    blessed = await bless_token(user_id="u1", token=sc.token)
    assert blessed.blessed_at is not None

    _simulate_restart()
    loaded = await get_token(sc.token)
    assert loaded is not None
    assert loaded.blessed_at == blessed.blessed_at


@pytest.mark.smoke
@pytest.mark.asyncio
async def test_grace_is_measured_from_the_bless_after_a_restart(ledger_spy):
    """An old card approved just now is a FRESH bless, restart or not."""
    sc = await _mint()
    sc.created_at = sc.created_at - timedelta(
        seconds=staging._BLESSED_GRACE_SECONDS + 60
    )
    await staging_store.persist(sc)
    await bless_token(user_id="u1", token=sc.token)

    _simulate_restart()
    await staging.get_pending_for_user("u1")  # rehydrate the cache

    with pytest.raises(StagingBlockedError) as exc:
        await _mint()
    assert exc.value.blocker.token == sc.token
    assert exc.value.blocker.state == "blessed"


# --- C1 -----------------------------------------------------------------------


@pytest.mark.smoke
@pytest.mark.asyncio
async def test_decided_at_is_the_revoke_not_the_mint(ledger_spy):
    sc = await _mint()
    sc.created_at = sc.created_at - timedelta(minutes=5)

    await revoke_token(user_id="u1", token=sc.token)

    assert sc.resolved_at is not None
    assert decision_ledger._decided_at(sc) == sc.resolved_at.isoformat()
    assert decision_ledger._decided_at(sc) != sc.created_at.isoformat()


@pytest.mark.smoke
@pytest.mark.asyncio
async def test_bless_and_consume_stamp_resolved_at(ledger_spy):
    sc = await _mint()
    await bless_token(user_id="u1", token=sc.token)
    assert sc.resolved_at == sc.blessed_at

    await consume_token(user_id="u1", token=sc.token, expected_kind="update_entry")
    assert sc.resolved_at is not None
    assert sc.resolved_at >= sc.blessed_at
    assert decision_ledger._decided_at(sc) == sc.resolved_at.isoformat()


@pytest.mark.smoke
@pytest.mark.asyncio
async def test_expiry_sweep_stamps_resolved_at():
    sc = await _mint()
    sc.expires_at = sc.created_at - timedelta(seconds=1)

    staging._sweep_expired_locked()

    assert sc.state == "expired"
    assert sc.resolved_at is not None


@pytest.mark.smoke
@pytest.mark.asyncio
async def test_resolved_at_survives_a_restart():
    sc = await _mint()
    blessed = await bless_token(user_id="u1", token=sc.token)

    _simulate_restart()
    loaded = await get_token(sc.token)
    assert loaded is not None
    assert loaded.resolved_at == blessed.resolved_at
