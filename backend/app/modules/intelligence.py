"""Availability state for optional intelligence runtime adapters."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class IntelligenceRuntimeStatus:
    """Current resident-harness availability without exposing internals."""

    available: bool
    reason: str = ""


_status = IntelligenceRuntimeStatus(available=False, reason="not_started")


def mark_intelligence_available() -> None:
    """Record a successfully initialized resident-harness adapter."""
    global _status
    _status = IntelligenceRuntimeStatus(available=True)


def mark_intelligence_unavailable(reason: str) -> None:
    """Record a bounded, operator-safe reason for unavailable intelligence."""
    global _status
    _status = IntelligenceRuntimeStatus(
        available=False, reason=str(reason or "unknown")
    )


def intelligence_runtime_status() -> IntelligenceRuntimeStatus:
    """Return the current optional-runtime availability state."""
    return _status


__all__ = [
    "IntelligenceRuntimeStatus",
    "intelligence_runtime_status",
    "mark_intelligence_available",
    "mark_intelligence_unavailable",
]
