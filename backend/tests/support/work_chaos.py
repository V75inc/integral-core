"""Chaos helpers for work-kernel restart boundaries (Task 11)."""

from __future__ import annotations

from typing import Dict, Optional, Set

CRASH_POINTS = [
    "after_enqueue",
    "after_claim",
    "after_agent_run_create",
    "before_capability_effect",
    "after_effect_receipt_before_completion",
    "before_approval_decision",
    "after_approval_decision",
    "before_outbox_delivery",
    "after_outbox_delivery",
    "during_routine_provider_turn",
    "during_cancellation",
    "during_deadline_expiry",
    "during_event_enqueue_before_checkpoint",
]


class DurableFakeAdapter:
    """Process-local durable fake keyed by effect id (no duplicate effects)."""

    def __init__(self) -> None:
        self.effects: Dict[str, int] = {}

    def apply(self, effect_id: str) -> int:
        self.effects[effect_id] = self.effects.get(effect_id, 0) + 1
        return self.effects[effect_id]

    def count(self, effect_id: str) -> int:
        return int(self.effects.get(effect_id, 0))


class DurableOutboxConsumer:
    """Dedup deliveries by outbox_id."""

    def __init__(self) -> None:
        self.seen: Set[str] = set()
        self.deliveries: list[str] = []

    async def __call__(self, entry) -> None:
        oid = getattr(entry, "outbox_id", "") or ""
        if oid in self.seen:
            return
        self.seen.add(oid)
        self.deliveries.append(oid)
