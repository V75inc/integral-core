"""Content-free, task-local latency markers for one native provider invocation."""

from __future__ import annotations

import asyncio
import time
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from typing import Any

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
        "lightPassMs",
        "heavyStartedMs",
        "lightResponseMs",
        "heavyFirstTokenMs",
        "escalationMs",
    }
)

PHASE_FIELDS = frozenset(
    {
        "routeResolution",
        "modelAdmission",
        "dispatchReceipt",
        "providerStart",
        "routingDiscovery",
        "workspaceProfile",
        "skillMaterialization",
        "runtimeBuild",
    }
)


@dataclass
class TurnLatency:
    started: float = field(default_factory=time.monotonic)
    markers: dict[str, float] = field(default_factory=dict)

    model_settings: dict = field(default_factory=dict)
    deadline: asyncio.Timeout | None = None
    route_plan: Any = None
    budget_settings: dict | None = None
    physical_requests: int = 0
    physical_tokens: dict[str, int] = field(default_factory=dict)
    clients: list[Any] = field(default_factory=list)
    roles: list[dict[str, str]] = field(default_factory=list)
    phase_totals: dict[str, float] = field(default_factory=dict)
    phase_counts: dict[str, int] = field(default_factory=dict)

    def freeze_budget(self, value: dict) -> None:
        """A later model can only tighten an already established turn ceiling."""
        from app.config import settings

        if self.budget_settings is None:
            self.budget_settings = dict(value)
        else:
            for key, item in value.items():
                self.budget_settings[key] = min(
                    self.budget_settings.get(key, item), item
                )
        if self.deadline is not None:
            limit = min(
                settings.INTEGRAL_HARNESS_CHAT_TURN_TIMEOUT_SECONDS,
                self.budget_settings.get(
                    "turn_timeout", settings.INTEGRAL_HARNESS_CHAT_TURN_TIMEOUT_SECONDS
                ),
            )
            # asyncio deadlines use the running loop's clock. uvloop's epoch
            # can differ from time.monotonic() (notably after macOS sleep).
            elapsed = time.monotonic() - self.started
            self.clamp_remaining_deadline(limit - elapsed)

    def remaining_deadline_seconds(self) -> float | None:
        """Read a timeout only in its owning event loop's clock domain."""
        if self.deadline is None or self.deadline.when() is None:
            return None
        return self.deadline.when() - asyncio.get_running_loop().time()

    def clamp_remaining_deadline(self, seconds: float) -> None:
        """Tighten a loop-owned deadline without extending its allowance."""
        if self.deadline is not None:
            self.deadline.reschedule(
                min(
                    self.deadline.when() or float("inf"),
                    asyncio.get_running_loop().time() + seconds,
                )
            )

    def reserve_dispatch(self) -> None:
        """Count physical retries as well as model switches before SDK entry."""
        from app.agentive.harness.pydantic_ai_compat import UsageLimitExceeded
        from app.config import settings

        limits = self.budget_settings or self.model_settings
        requests = min(
            settings.INTEGRAL_NATIVE_TURN_REQUEST_LIMIT,
            limits.get(
                "model_request_limit", settings.INTEGRAL_NATIVE_TURN_REQUEST_LIMIT
            ),
        )
        tokens = min(
            settings.INTEGRAL_NATIVE_TURN_TOKEN_LIMIT,
            limits.get("turn_token_limit", settings.INTEGRAL_NATIVE_TURN_TOKEN_LIMIT),
        )
        if (
            self.physical_requests >= requests
            or sum(self.physical_tokens.values()) >= tokens
        ):
            raise UsageLimitExceeded("The shared turn model budget is exhausted")
        self.physical_requests += 1

    def observe_usage(self, observation: Any) -> None:
        usage = observation.usage
        if usage is not None:
            count = (usage.input_tokens or 0) + (usage.output_tokens or 0)
            self.physical_tokens[observation.request_id] = max(
                count, self.physical_tokens.get(observation.request_id, 0)
            )

    def mark(self, name: str) -> None:
        if name not in LATENCY_FIELDS:
            raise ValueError("Unknown chat latency marker")
        self.markers.setdefault(name, (time.monotonic() - self.started) * 1000)


current_latency: ContextVar[TurnLatency | None] = ContextVar(
    "chat_latency", default=None
)


@contextmanager
def measure_latency_phase(name: str):
    """Bounded, content-free aggregate diagnostics; not wall-clock milestones.

    Route resolution is nested within model admission. Provider start measures
    SDK entry until the response/stream is obtained, not stream completion.
    Never sum nested spans or present this as time to displayed answer text.
    """
    if name not in PHASE_FIELDS:
        raise ValueError("Unknown model latency phase")
    timing = current_latency.get()
    began = time.monotonic()
    try:
        yield
    finally:
        if timing is not None:
            timing.phase_totals[name] = (
                timing.phase_totals.get(name, 0) + (time.monotonic() - began) * 1000
            )
            timing.phase_counts[name] = timing.phase_counts.get(name, 0) + 1


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
    profile = (
        (
            timing.budget_settings
            if timing.budget_settings is not None
            else timing.model_settings
        )
        if timing
        else {}
    )
    requests = min(
        settings.INTEGRAL_NATIVE_TURN_REQUEST_LIMIT,
        profile.get("model_request_limit", settings.INTEGRAL_NATIVE_TURN_REQUEST_LIMIT),
    )
    tokens = min(
        settings.INTEGRAL_NATIVE_TURN_TOKEN_LIMIT,
        profile.get("turn_token_limit", settings.INTEGRAL_NATIVE_TURN_TOKEN_LIMIT),
    )
    if timing is not None and timing.budget_settings is not None:
        adaptive_requests = max(adaptive_requests, timing.physical_requests)
        adaptive_tokens = max(adaptive_tokens, sum(timing.physical_tokens.values()))
    return UsageLimits(
        request_limit=(
            min(1, requests) if light else max(0, requests - adaptive_requests)
        ),
        tool_calls_limit=min(32, profile.get("tool_call_limit", 32)),
        total_tokens_limit=max(0, tokens - adaptive_tokens),
    )
