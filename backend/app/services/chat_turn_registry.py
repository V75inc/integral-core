"""In-flight chat turn registry — one active user turn per thread (I-CHAT-PAR-01)."""

from __future__ import annotations

import asyncio
import time
import uuid
from dataclasses import dataclass, field
from typing import Callable, Dict, Optional

from app.api.errors import ResourceConflictError

_registry_lock = asyncio.Lock()
_in_flight: Dict[str, "InFlightTurn"] = {}


@dataclass
class InFlightTurn:
    turn_id: str
    thread_id: str
    user_id: str
    started_at: float
    origin: str = ""
    cancel_event: asyncio.Event = field(default_factory=asyncio.Event)
    _cancel_hook: Optional[Callable[[], None]] = None

    def register_cancel_hook(self, hook: Callable[[], None]) -> None:
        self._cancel_hook = hook

    def cancel(self) -> None:
        self.cancel_event.set()
        if self._cancel_hook is not None:
            try:
                self._cancel_hook()
            except Exception:
                pass


def _active_turns_for_user(user_id: str) -> int:
    return sum(1 for h in _in_flight.values() if h.user_id == user_id)


async def acquire_turn(
    *, thread_id: str, user_id: str, origin: str = ""
) -> InFlightTurn:
    """Reserve the thread for a single in-flight turn or raise 409.

    Two admission checks, and they fail for different reasons:

    * one turn per thread (I-CHAT-PAR-01) — the thread is already answering;
    * a ceiling on simultaneous turns per USER. ``user_id`` was recorded on
      the handle from the start but never consulted, so the only limit was the
      client's own cap of 5. A scripted or misbehaving client could open turns
      without bound, each holding an LLM stream. The default matches the
      client, so an honest one is never refused.

    ``details.reason`` distinguishes them: both are 409, but "this thread is
    already responding" and "you have too many conversations running" call for
    different things from the user.

    ``origin`` tags the turn so callers (e.g. routine Stop) can abort only a
    matching in-flight run without killing a live human chat on the same
    thread — see ``cancel_turn_if_origin``.
    """
    from app.config import settings

    async with _registry_lock:
        if thread_id in _in_flight:
            raise ResourceConflictError(
                message="A turn is already in progress on this thread",
                details={"thread_id": thread_id, "reason": "thread_busy"},
            )
        limit = max(int(getattr(settings, "MAX_CONCURRENT_TURNS_PER_USER", 5)), 1)
        active = _active_turns_for_user(user_id)
        if active >= limit:
            raise ResourceConflictError(
                message=(
                    f"You already have {active} conversations responding "
                    f"(limit {limit}). Wait for one to finish, or stop it."
                ),
                details={
                    "reason": "user_turn_limit",
                    "limit": limit,
                    "active": active,
                },
            )
        handle = InFlightTurn(
            turn_id=str(uuid.uuid4()),
            thread_id=thread_id,
            user_id=user_id,
            started_at=time.monotonic(),
            origin=(origin or "").strip(),
        )
        _in_flight[thread_id] = handle
        return handle


async def release_turn(thread_id: str) -> None:
    """Drop the in-flight turn handle for a thread after stream completion."""
    async with _registry_lock:
        _in_flight.pop(thread_id, None)


async def cancel_turn(thread_id: str) -> bool:
    """Signal cancellation on the active turn; return False if none is in flight."""
    async with _registry_lock:
        handle = _in_flight.get(thread_id)
        if handle is None:
            return False
        handle.cancel()
        return True


async def cancel_turn_if_origin(thread_id: str, origin: str) -> bool:
    """Cancel the active turn only when its ``origin`` matches.

    Returns False when nothing is in flight or the origin does not match —
    so stopping a routine never aborts a live human turn on the same thread.
    """
    wanted = (origin or "").strip()
    async with _registry_lock:
        handle = _in_flight.get(thread_id)
        if handle is None:
            return False
        if (handle.origin or "").strip() != wanted:
            return False
        handle.cancel()
        return True


def get_turn(thread_id: str) -> Optional[InFlightTurn]:
    """Return the active in-flight turn handle for a thread, if any."""
    return _in_flight.get(thread_id)


async def reset_registry_for_tests() -> None:
    """Test helper — clear all in-flight turns."""
    async with _registry_lock:
        _in_flight.clear()
