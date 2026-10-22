"""Content-free, task-local latency markers for one native provider invocation."""

from __future__ import annotations

import asyncio
import time
from contextvars import ContextVar
from dataclasses import dataclass, field

LATENCY_FIELDS = frozenset(
    {
        "preparationMs",
        "providerFirstTokenMs",
        "firstToolRequestedMs",
        "firstToolStartedMs",
        "firstOperationalToolMs",
        "firstTokenMs",
        "totalMs",
        "modelPassMs",
        "queueMs",
    }
)


@dataclass
class TurnLatency:
    started: float = field(default_factory=time.monotonic)
    markers: dict[str, float] = field(default_factory=dict)

    model_settings: dict = field(default_factory=dict)
    deadline: asyncio.Timeout | None = None

    def mark(self, name: str) -> None:
        if name not in LATENCY_FIELDS:
            raise ValueError("Unknown chat latency marker")
        self.markers.setdefault(name, (time.monotonic() - self.started) * 1000)


current_latency: ContextVar[TurnLatency | None] = ContextVar(
    "chat_latency", default=None
)


def mark_latency(name: str) -> None:
    """Record a first-occurrence marker in the current turn, if present."""
    timing = current_latency.get()
    if timing is not None:
        timing.mark(name)


def turn_usage_limits(
    *, adaptive_requests: int = 0, adaptive_tokens: int = 0, light: bool = False
):
    """Host-enforced budgets shared by routing and operational model passes."""
    from app.agentive.harness.pydantic_ai_compat import UsageLimits
    from app.config import settings

    timing = current_latency.get()
    profile = timing.model_settings if timing else {}
    requests = min(
        settings.INTEGRAL_NATIVE_TURN_REQUEST_LIMIT,
        profile.get("model_request_limit", settings.INTEGRAL_NATIVE_TURN_REQUEST_LIMIT),
    )
    tokens = min(
        settings.INTEGRAL_NATIVE_TURN_TOKEN_LIMIT,
        profile.get("turn_token_limit", settings.INTEGRAL_NATIVE_TURN_TOKEN_LIMIT),
    )
    return UsageLimits(
        request_limit=(
            min(1, requests) if light else max(0, requests - adaptive_requests)
        ),
        tool_calls_limit=min(32, profile.get("tool_call_limit", 32)),
        total_tokens_limit=max(0, tokens - adaptive_tokens),
    )
