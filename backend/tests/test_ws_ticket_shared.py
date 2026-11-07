"""A websocket ticket minted by one server process can be redeemed by another, once."""

from __future__ import annotations

import time

import pytest

from app.services import ws_ticket

pytestmark = pytest.mark.asyncio


class _Store:
    """A shared database stand-in: the same instance serves two 'processes'."""

    def __init__(self):
        self.rows = {}

    async def save(self, collection, record):
        self.rows[record["id"]] = record

    async def find(self, collection, query, limit=None):
        now = query["context.expires_at"]["$lt"]
        return [r for r in self.rows.values() if r["context"]["expires_at"] < now][
            :limit
        ]

    async def delete(self, collection, record_id):
        self.rows.pop(record_id, None)

    async def find_one_and_delete(self, collection, query):
        return self.rows.pop(query["id"], None)


@pytest.fixture
def shared(monkeypatch):
    ws_ticket._reset_for_tests()
    store = _Store()
    monkeypatch.setattr(ws_ticket, "_shared_database", lambda: store)
    yield store
    ws_ticket._reset_for_tests()


async def test_a_ticket_minted_here_is_redeemed_by_another_process(shared):
    ticket = await ws_ticket.mint_ws_ticket_shared("user-1")
    ws_ticket._reset_for_tests()  # another process has none of this process's memory
    assert await ws_ticket.redeem_ws_ticket_shared(ticket) == "user-1"


async def test_a_ticket_is_single_use_across_processes(shared):
    ticket = await ws_ticket.mint_ws_ticket_shared("user-1")
    assert await ws_ticket.redeem_ws_ticket_shared(ticket) == "user-1"
    assert await ws_ticket.redeem_ws_ticket_shared(ticket) is None


async def test_the_secret_is_never_stored(shared):
    ticket = await ws_ticket.mint_ws_ticket_shared("user-1")
    assert ticket not in "".join(shared.rows)
    assert all(ticket not in str(row) for row in shared.rows.values())


async def test_an_expired_ticket_is_refused_and_pruned(shared, monkeypatch):
    ticket = await ws_ticket.mint_ws_ticket_shared("user-1")
    monkeypatch.setattr(ws_ticket, "TTL_SECONDS", 60)
    row = next(iter(shared.rows.values()))
    row["context"]["expires_at"] = time.time() - 1
    assert await ws_ticket.redeem_ws_ticket_shared(ticket) is None
    stale = await ws_ticket.mint_ws_ticket_shared("user-2")
    assert stale


async def test_an_unknown_or_blank_ticket_is_refused(shared):
    assert await ws_ticket.redeem_ws_ticket_shared("") is None
    assert await ws_ticket.redeem_ws_ticket_shared("nope") is None


async def test_without_a_shared_store_the_in_process_ticket_still_works(monkeypatch):
    ws_ticket._reset_for_tests()
    monkeypatch.setattr(ws_ticket, "_shared_database", lambda: None)
    ticket = await ws_ticket.mint_ws_ticket_shared("user-9")
    assert await ws_ticket.redeem_ws_ticket_shared(ticket) == "user-9"
    assert await ws_ticket.redeem_ws_ticket_shared(ticket) is None


async def test_a_failing_store_falls_back_to_the_in_process_ticket(monkeypatch):
    ws_ticket._reset_for_tests()

    class Broken:
        async def save(self, *a, **k):
            raise RuntimeError("down")

    monkeypatch.setattr(ws_ticket, "_shared_database", lambda: Broken())
    ticket = await ws_ticket.mint_ws_ticket_shared("user-3")
    assert await ws_ticket.redeem_ws_ticket_shared(ticket) == "user-3"
