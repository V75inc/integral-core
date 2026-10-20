"""Ephemeral evidence of stalled work, never a result or authorization cache."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from typing import Any


@dataclass
class TurnProgress:
    """Observe outcomes only after execution; retain fingerprints, not records.

    Three identical read outcomes indicate a stalled approach. Writes invalidate
    observations so a read after a mutation is always fresh. This state belongs
    to one invocation and is deliberately absent from durable checkpoints.
    """

    reason: str = ""
    _reads: dict[str, int] = field(default_factory=dict, repr=False)
    _failures: int = 0
    _discovery_rounds: int = 0
    _available: frozenset[str] = frozenset()
    _seen_discovery: set[str] = field(default_factory=set, repr=False)

    @property
    def should_finalize(self) -> bool:
        return bool(self.reason)

    def observe(self, name: str, arguments: Any, result: Any, *, is_read: bool):
        failed = isinstance(result, dict) and bool(
            result.get("error") or result.get("refused")
        )
        self._failures = self._failures + 1 if failed else 0
        if self._failures >= 4:
            self.reason = "Repeated tool failures have not established an answer."
        if not is_read:
            self._reads.clear()
            return
        self._discovery_rounds = 0
        # Receipt identities and replay bookkeeping are not new evidence.
        evidence = (
            {
                k: v
                for k, v in result.items()
                if k not in {"_receipt", "replayed", "_observation_ref"}
            }
            if isinstance(result, dict)
            else result
        )
        fingerprint = hashlib.sha256(
            json.dumps(
                [name, arguments, evidence], sort_keys=True, default=str
            ).encode()
        ).hexdigest()
        count = self._reads.get(fingerprint, 0) + 1
        # Execution is already bounded by UsageLimits; also bound retained state.
        if len(self._reads) >= 128:
            self._reads.clear()
        self._reads[fingerprint] = count
        if count >= 3:
            self.reason = "Repeated reads returned the same evidence."

    def observe_discovery(self, call_id: str, available: frozenset[str]):
        if call_id in self._seen_discovery:
            return
        self._seen_discovery.add(call_id)
        self._discovery_rounds = (
            0 if available - self._available else self._discovery_rounds + 1
        )
        self._available = available
        if self._discovery_rounds >= 4:
            self.reason = "Repeated discovery did not expose a new capability."


FINAL_RESPONSE_FALLBACK = (
    "I ran into a problem and couldn't finish your request. "
    "Before trying again, check this chat for any changes awaiting your approval "
    "and review anything already saved. You can ask me to pick up where we left off."
)
