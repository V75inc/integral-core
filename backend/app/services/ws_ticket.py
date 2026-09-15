"""Short-lived WebSocket connection tickets (avoid JWT in query strings)."""

from __future__ import annotations

import secrets
import time
from typing import Dict, Optional, Tuple

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


def _reset_for_tests() -> None:
    _TICKETS.clear()
