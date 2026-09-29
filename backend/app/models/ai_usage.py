"""AI usage ledger + day buckets (I-GRAPH-02 Objects).

Plan-agnostic metering only. Caps and plan keys live in the commercial
billing module, which registers a limit resolver with Core.
"""

from __future__ import annotations

from typing import Optional

from jvspatial.core import Object
from jvspatial.core.annotations import attribute, compound_index


@compound_index(
    [("idempotency_key", 1)],
    name="usage_ledger_idempotency",
    unique=True,
    partial_filter_expression={
        "entity": "UsageLedgerEvent",
        "context.idempotency_key": {"$gt": ""},
    },
)
class UsageLedgerEvent(Object):
    """Immutable append-only record of one metered model call."""

    workspace_id: str = attribute(default="", indexed=True)
    user_id: str = attribute(default="", indexed=True)
    # platform | byok — only platform credits count toward quota.
    source: str = attribute(default="platform", indexed=True)
    meter: str = attribute(default="ai_credits", indexed=True)
    credits: int = attribute(default=0)
    input_tokens: int = attribute(default=0)
    output_tokens: int = attribute(default=0)
    model_id: str = attribute(default="")
    model_weight: float = attribute(default=2.0)
    run_id: str = attribute(default="", indexed=True)
    thread_id: str = attribute(default="")
    idempotency_key: str = attribute(default="", indexed=True)
    recorded_at: Optional[str] = attribute(default=None)
    # UTC calendar day YYYY-MM-DD for bucket alignment.
    day_utc: str = attribute(default="", indexed=True)


@compound_index(
    [("workspace_id", 1), ("meter", 1), ("day_utc", 1)],
    name="usage_day_bucket_ws_meter_day",
    unique=True,
    partial_filter_expression={
        "entity": "UsageDayBucket",
        "context.workspace_id": {"$gt": ""},
        "context.meter": {"$gt": ""},
        "context.day_utc": {"$gt": ""},
    },
)
class UsageDayBucket(Object):
    """Denormalized per-day credit totals for rolling-window O(days) reads."""

    workspace_id: str = attribute(default="", indexed=True)
    meter: str = attribute(default="ai_credits", indexed=True)
    day_utc: str = attribute(default="", indexed=True)
    credits_platform: int = attribute(default=0)
    credits_byok: int = attribute(default=0)
    updated_at: Optional[str] = attribute(default=None)
