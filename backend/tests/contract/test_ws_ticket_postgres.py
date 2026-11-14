"""The shared websocket ticket store works on real PostgreSQL, across processes."""

import pytest
from jvspatial.core.context import GraphContext, scoped_default_context_async

from app.services import ws_ticket

pytestmark = [pytest.mark.contract, pytest.mark.postgres, pytest.mark.asyncio]


async def test_ticket_round_trip_and_single_use_on_postgres(
    postgres_raw_db, monkeypatch
):
    monkeypatch.setattr(ws_ticket, "_shared_database", lambda: postgres_raw_db)
    ws_ticket._reset_for_tests()
    async with scoped_default_context_async(GraphContext(database=postgres_raw_db)):
        ticket = await ws_ticket.mint_ws_ticket_shared("user-pg")
        ws_ticket._reset_for_tests()  # a second process has no memory of it
        stored = await postgres_raw_db.find("object", {"entity": "WsTicket"}, limit=5)
        assert len(stored) == 1 and ticket not in str(stored[0])
        assert await ws_ticket.redeem_ws_ticket_shared(ticket) == "user-pg"
        assert await ws_ticket.redeem_ws_ticket_shared(ticket) is None
        assert (
            await postgres_raw_db.find("object", {"entity": "WsTicket"}, limit=5) == []
        )


async def test_expired_tickets_are_pruned_on_the_next_mint(
    postgres_raw_db, monkeypatch
):
    monkeypatch.setattr(ws_ticket, "_shared_database", lambda: postgres_raw_db)
    monkeypatch.setattr(ws_ticket, "TTL_SECONDS", -1)
    async with scoped_default_context_async(GraphContext(database=postgres_raw_db)):
        old = await ws_ticket.mint_ws_ticket_shared("user-old")
        monkeypatch.setattr(ws_ticket, "TTL_SECONDS", 60)
        await ws_ticket.mint_ws_ticket_shared("user-new")
        rows = await postgres_raw_db.find("object", {"entity": "WsTicket"}, limit=10)
        assert len(rows) == 1  # the expired one is gone, only the new one remains
        assert await ws_ticket.redeem_ws_ticket_shared(old) is None
