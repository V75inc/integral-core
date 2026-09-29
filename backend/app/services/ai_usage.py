"""Plan-agnostic AI credit metering for workspace-scoped platform-key spend.

Core records usage and exposes a gate hook. Commercial deployments register a
limit resolver (e.g. Business maps Free/Basic/Premium → ceilings). Open-source
boots leave the resolver unset → unlimited enforcement, ledger still records.
"""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Awaitable, Callable, List, Literal, Optional

from app.models.ai_usage import UsageDayBucket, UsageLedgerEvent
from app.utils.time import utc_now, utc_now_iso

logger = logging.getLogger(__name__)

METER_AI_CREDITS = "ai_credits"
WINDOW_DAYS = 7
SOFT_WARN_THRESHOLD = 0.80
DEFAULT_TOKENS_PER_CREDIT = 10_000
KeySource = Literal["platform", "byok"]

# Limit resolver: workspace_id → credit ceiling. 0 / None = unlimited.
AiUsageLimitResolver = Callable[[str], Awaitable[int]]
_limit_resolver: Optional[AiUsageLimitResolver] = None
_tokens_per_credit: int = DEFAULT_TOKENS_PER_CREDIT

# Model-weight heuristics (tune without changing ledger shape).
_LIGHT_MARKERS = (
    "mini",
    "nano",
    "haiku",
    "flash-lite",
    "flash_lite",
    "gpt-4o-mini",
    "gpt-3.5",
)
_HEAVY_MARKERS = (
    "opus",
    "o1",
    "o3",
    "o4",
    "gpt-5",
    "claude-4",
    "claude-3-opus",
    "reasoning",
    "pro",
)


def register_ai_usage_limit_resolver(fn: Optional[AiUsageLimitResolver]) -> None:
    """Register (or clear) the commercial limit resolver. Core never imports it."""
    global _limit_resolver
    _limit_resolver = fn


def register_ai_tokens_per_credit(value: int) -> None:
    """Set billable tokens per credit (commercial boot hook). Min 1."""
    global _tokens_per_credit
    try:
        n = int(value)
    except (TypeError, ValueError):
        return
    _tokens_per_credit = max(1, n)


def get_tokens_per_credit() -> int:
    return _tokens_per_credit


def effective_billable_tokens(input_tokens: int, output_tokens: int) -> int:
    """Token-equivalent volume for metering (output weighted 3×)."""
    in_tok = max(0, int(input_tokens or 0))
    out_tok = max(0, int(output_tokens or 0))
    return in_tok + (3 * out_tok)


def get_ai_usage_limit_resolver() -> Optional[AiUsageLimitResolver]:
    """Return the registered limit resolver, if any."""
    return _limit_resolver


async def get_ai_usage_limit(workspace_id: str) -> int:
    """Return the credit ceiling for ``workspace_id`` (0 = unlimited)."""
    if not workspace_id or _limit_resolver is None:
        return 0
    try:
        limit = await _limit_resolver(workspace_id)
    except Exception:  # noqa: BLE001
        logger.exception("ai usage limit resolver failed ws=%s", workspace_id)
        return 0
    try:
        return max(0, int(limit or 0))
    except (TypeError, ValueError):
        return 0


def model_weight_for(model_id: str) -> float:
    """Map a LiteLLM / provider model id to a credit weight."""
    mid = (model_id or "").strip().lower()
    if not mid:
        return 2.0
    for marker in _LIGHT_MARKERS:
        if marker in mid:
            return 1.0
    for marker in _HEAVY_MARKERS:
        if marker in mid:
            return 4.0
    return 2.0


def credits_for(
    input_tokens: int,
    output_tokens: int,
    model_id: str,
    *,
    model_weight: Optional[float] = None,
) -> int:
    """Convert token counts to integer credits (~1 credit per 10k billable tokens).

    ``model_weight`` is retained for ledger audit only; it no longer scales credits.
    """
    del model_id, model_weight  # weight stored separately on ledger events
    billable = effective_billable_tokens(input_tokens, output_tokens)
    if billable == 0:
        return 0
    per = get_tokens_per_credit()
    return max(1, int(math.ceil(billable / float(per))))


def _day_utc(moment: Optional[datetime] = None) -> str:
    now = moment or utc_now()
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    return now.astimezone(timezone.utc).date().isoformat()


def _window_day_keys(
    *, now: Optional[datetime] = None, days: int = WINDOW_DAYS
) -> List[str]:
    moment = now or utc_now()
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    moment = moment.astimezone(timezone.utc)
    return [
        (moment.date() - timedelta(days=offset)).isoformat() for offset in range(days)
    ]


@dataclass(frozen=True)
class AiUsageSnapshot:
    """Rolling-window AI credit snapshot for one workspace."""

    workspace_id: str
    used: int
    limit: int  # 0 = unlimited
    window_days: int = WINDOW_DAYS
    soft_warn_threshold: float = SOFT_WARN_THRESHOLD

    @property
    def remaining(self) -> Optional[int]:
        if self.limit <= 0:
            return None
        return max(0, self.limit - self.used)

    @property
    def percent(self) -> float:
        if self.limit <= 0:
            return 0.0
        return min(100.0, (self.used / self.limit) * 100.0)

    @property
    def is_unlimited(self) -> bool:
        return self.limit <= 0

    @property
    def enforcement_enabled(self) -> bool:
        return self.limit > 0

    @property
    def is_soft_warning(self) -> bool:
        if self.is_unlimited:
            return False
        return self.used >= int(self.limit * self.soft_warn_threshold)

    @property
    def is_exhausted(self) -> bool:
        if self.is_unlimited:
            return False
        return self.used >= self.limit

    def would_exceed(self, additional_credits: int = 1) -> bool:
        if self.is_unlimited:
            return False
        return (self.used + max(0, int(additional_credits))) > self.limit

    def to_dict(self) -> dict:
        return {
            "workspace_id": self.workspace_id,
            "used": int(self.used),
            "limit": int(self.limit),
            "remaining": self.remaining,
            "window_days": int(self.window_days),
            "percent": self.percent,
            "is_unlimited": self.is_unlimited,
            "is_soft_warning": self.is_soft_warning,
            "is_exhausted": self.is_exhausted,
            "enforcement_enabled": self.enforcement_enabled,
            "soft_warn_threshold": self.soft_warn_threshold,
            "meter": METER_AI_CREDITS,
            "unit": "credits",
        }


async def platform_credits_used(
    workspace_id: str,
    *,
    now: Optional[datetime] = None,
    window_days: int = WINDOW_DAYS,
) -> int:
    """Sum platform credits across the rolling UTC day window."""
    ws = (workspace_id or "").strip()
    if not ws:
        return 0
    days = _window_day_keys(now=now, days=window_days)
    total = 0
    for day in days:
        rows = await UsageDayBucket.find(
            {
                "context.workspace_id": ws,
                "context.meter": METER_AI_CREDITS,
                "context.day_utc": day,
            }
        )
        for row in rows or []:
            total += int(getattr(row, "credits_platform", 0) or 0)
    return total


async def snapshot(
    workspace_id: str,
    *,
    now: Optional[datetime] = None,
) -> AiUsageSnapshot:
    """Return the rolling usage snapshot (limit from registered resolver)."""
    ws = (workspace_id or "").strip()
    used = await platform_credits_used(ws, now=now) if ws else 0
    limit = await get_ai_usage_limit(ws) if ws else 0
    return AiUsageSnapshot(workspace_id=ws, used=used, limit=limit)


async def assert_within_quota(
    workspace_id: str,
    *,
    estimated_min_credits: int = 1,
) -> AiUsageSnapshot:
    """Raise ``QuotaExceededError`` when platform spend would exceed the limit.

    No-op (returns snapshot) when unlimited or workspace id is empty.
    """
    snap = await snapshot(workspace_id)
    if snap.would_exceed(estimated_min_credits):
        from app.api.errors import QuotaExceededError

        raise QuotaExceededError(
            message=(
                "This workspace has used its AI credit allowance for the "
                f"rolling {WINDOW_DAYS}-day window. Upgrade your plan, add your "
                "own model API key in Settings, or wait for older usage to roll off."
            ),
            details={
                "workspace_id": snap.workspace_id,
                "used": snap.used,
                "limit": snap.limit,
                "remaining": snap.remaining,
                "window_days": snap.window_days,
                "meter": METER_AI_CREDITS,
            },
        )
    return snap


async def _find_bucket(workspace_id: str, day_utc: str) -> Optional[UsageDayBucket]:
    rows = await UsageDayBucket.find(
        {
            "context.workspace_id": workspace_id,
            "context.meter": METER_AI_CREDITS,
            "context.day_utc": day_utc,
        }
    )
    if not rows:
        return None
    return rows[0]


async def record_ai_usage(
    *,
    workspace_id: str,
    user_id: str,
    source: KeySource,
    input_tokens: int,
    output_tokens: int,
    model_id: str,
    run_id: str = "",
    thread_id: str = "",
    idempotency_key: str = "",
    recorded_at: Optional[str] = None,
) -> Optional[UsageLedgerEvent]:
    """Append a ledger event and bump the day bucket. Idempotent on key.

    Returns ``None`` when there are no tokens to meter or the idempotency key
    was already recorded. BYOK events are stored but do not increment the
    platform quota counter.
    """
    ws = (workspace_id or "").strip()
    if not ws:
        return None
    weight = model_weight_for(model_id)
    credits = credits_for(input_tokens, output_tokens, model_id, model_weight=weight)
    if credits <= 0:
        return None

    src: KeySource = "byok" if source == "byok" else "platform"
    key = (idempotency_key or "").strip()
    if key:
        existing = await UsageLedgerEvent.find({"context.idempotency_key": key})
        if existing:
            return existing[0]

    when = recorded_at or utc_now_iso()
    day = _day_utc()
    try:
        event = await UsageLedgerEvent.create(
            workspace_id=ws,
            user_id=(user_id or "").strip(),
            source=src,
            meter=METER_AI_CREDITS,
            credits=credits,
            input_tokens=max(0, int(input_tokens or 0)),
            output_tokens=max(0, int(output_tokens or 0)),
            model_id=(model_id or "").strip() or "unknown",
            model_weight=weight,
            run_id=(run_id or "").strip(),
            thread_id=(thread_id or "").strip(),
            idempotency_key=key,
            recorded_at=when,
            day_utc=day,
        )
    except Exception as exc:  # noqa: BLE001 — unique-index race = already recorded
        if key:
            existing = await UsageLedgerEvent.find({"context.idempotency_key": key})
            if existing:
                return existing[0]
        logger.warning(
            "ai_usage: ledger write failed ws=%s key=%s: %s",
            ws,
            key,
            exc,
        )
        return None

    try:
        bucket = await _find_bucket(ws, day)
        if bucket is None:
            bucket = await UsageDayBucket.create(
                workspace_id=ws,
                meter=METER_AI_CREDITS,
                day_utc=day,
                credits_platform=0,
                credits_byok=0,
                updated_at=when,
            )
        if src == "platform":
            bucket.credits_platform = int(bucket.credits_platform or 0) + credits
        else:
            bucket.credits_byok = int(bucket.credits_byok or 0) + credits
        bucket.updated_at = when
        await bucket.save()
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "ai_usage: day bucket update failed ws=%s day=%s: %s",
            ws,
            day,
            exc,
        )

    return event
