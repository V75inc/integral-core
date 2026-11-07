"""Short-lived WebSocket connection tickets (avoid JWT in query strings)."""

from __future__ import annotations

import hashlib
import logging
import secrets
import time
from typing import Any, Dict, Optional, Tuple

logger = logging.getLogger(__name__)

# ticket -> (user_id, expires_at monotonic-ish via time.time())
_TICKETS: Dict[str, Tuple[str, float]] = {}
TTL_SECONDS = 60


def _prune_expired(now: Optional[float] = None) -> None:
    ts = now if now is not None else time.time()
    expired = [k for k, (_, exp) in _TICKETS.items() if exp <= ts]
    for key in expired:
        _TICKETS.pop(key, None)


def mint_ws_ticket(user_id: str) -> str:
    """Mint a single-use ticket valid for ``TTL_SECONDS``."""
    _prune_expired()
    ticket = secrets.token_urlsafe(32)
    _TICKETS[ticket] = (user_id, time.time() + TTL_SECONDS)
    return ticket


def redeem_ws_ticket(ticket: str) -> Optional[str]:
    """Redeem a ticket; returns user_id or None. Single-use."""
    if not ticket:
        return None
    _prune_expired()
    entry = _TICKETS.pop(ticket, None)
    if entry is None:
        return None
    user_id, expires_at = entry
    if time.time() > expires_at:
        return None
    return user_id


# ---------------------------------------------------------------------------
# Shared store. With several server processes a ticket minted by one must be
# redeemable by another, so on a database that supports it (PostgreSQL) the ticket
# lives in the object table. Only the SHA-256 of the ticket is stored (the secret is
# never at rest), redeeming is one atomic find-and-delete (single use, even across
# processes), and expired rows are pruned on mint. A store without that support, or
# any failure to reach it, falls back to the in-process dictionary above, which is
# exactly the previous behaviour for a single process.
# ---------------------------------------------------------------------------

_ENTITY = "WsTicket"


def _shared_database() -> Any:
    """The prime database when it can be a shared ticket store, else None."""
    try:
        from jvspatial.db import get_prime_database

        from app.services.app_operations.transaction_scope import (
            graph_transaction_available,
        )

        database = get_prime_database()
        return database if graph_transaction_available(database) else None
    except Exception:  # noqa: BLE001 - no usable shared store: stay in-process
        return None


def _ticket_id(ticket: str) -> str:
    return f"o.{_ENTITY}." + hashlib.sha256(ticket.encode("utf-8")).hexdigest()


async def mint_ws_ticket_shared(user_id: str) -> str:
    """Mint a single-use ticket any server process can redeem."""
    database = _shared_database()
    if database is None:
        return mint_ws_ticket(user_id)
    ticket = secrets.token_urlsafe(32)
    now = time.time()
    try:
        await database.save(
            "object",
            {
                "id": _ticket_id(ticket),
                "entity": _ENTITY,
                "context": {"user_id": user_id, "expires_at": now + TTL_SECONDS},
            },
        )
        stale = await database.find(
            "object",
            {"entity": _ENTITY, "context.expires_at": {"$lt": now}},
            limit=50,
        )
        for row in stale:
            await database.delete("object", row["id"])
    except Exception:  # noqa: BLE001 - never lose the ticket over housekeeping
        logger.warning(
            "ws ticket store unavailable; using in-process ticket", exc_info=True
        )
        return mint_ws_ticket(user_id)
    return ticket


async def redeem_ws_ticket_shared(ticket: str) -> Optional[str]:
    """Redeem a ticket once, whichever process minted it; returns the user id or None."""
    if not ticket:
        return None
    # A ticket minted in this process while the shared store was unavailable.
    local = redeem_ws_ticket(ticket)
    if local:
        return local
    database = _shared_database()
    if database is None:
        return None
    try:
        row = await database.find_one_and_delete(
            "object", {"id": _ticket_id(ticket), "entity": _ENTITY}
        )
    except Exception:  # noqa: BLE001
        logger.warning("ws ticket store unavailable at redeem", exc_info=True)
        return None
    if not row:
        return None
    context = row.get("context") or {}
    if time.time() > float(context.get("expires_at") or 0):
        return None
    user_id = context.get("user_id")
    return user_id if isinstance(user_id, str) and user_id else None


def _reset_for_tests() -> None:
    _TICKETS.clear()
