"""Deterministic query plan for an insights question (W3.5).

The plan names the instrument, the window, and the limits. It does not
read entries. A later refusal stays a refusal.
"""

from __future__ import annotations

import re
from datetime import datetime, time, timedelta, timezone
from typing import Any, Dict, Optional
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

_ON_FAILURE = (
    "Surface the tool error, refusal, or over_budget result. "
    "Do not rewrite it as no records or not found."
)
_RANK = (
    "highest",
    "lowest",
    "biggest",
    "largest",
    "smallest",
    "oldest",
    "newest",
    "top",
    "rank",
    "lucrative",
    "highest-value",
)
_RANK_ASC = ("lowest", "smallest", "oldest", "least")
_COUNT = ("how many", "count", "breakdown", "broken down")
_AGG = ("average", "avg", "sum", "total")
_RELATED = ("connected", "related to", "points at", "anchored", "what links")
_DIGEST = ("what's happening", "what is happening", "digest", "catch me up", "activity")
_FIND = ("find ", "about ", "mention", "search")


def _has(text: str, needles: tuple) -> bool:
    return any(needle in text for needle in needles)


def _field_hint(question: str) -> str:
    match = re.search(r"\bby\s+([a-z][a-z0-9_]*)", question)
    return match.group(1) if match else ""


def _top_n(question: str) -> int:
    match = re.search(r"\btop\s+(\d+)\b", question)
    if not match:
        return 1
    return max(1, min(int(match.group(1)), 50))


def _bounds(start, end, tz: ZoneInfo) -> Dict[str, str]:
    since = datetime.combine(start, time.min, tzinfo=tz).astimezone(timezone.utc)
    until = datetime.combine(end, time(23, 59, 59), tzinfo=tz).astimezone(timezone.utc)
    return {
        "since": since.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "until": until.strftime("%Y-%m-%dT%H:%M:%SZ"),
    }


def _window(question: str, tz: ZoneInfo, now: datetime) -> Optional[Dict[str, Any]]:
    local = now.astimezone(tz)
    day = local.date()
    text = question.casefold()
    label = ""
    start = end = day
    if "yesterday" in text:
        label, start, end = (
            "yesterday",
            day - timedelta(days=1),
            day - timedelta(days=1),
        )
    elif "today" in text:
        label = "today"
    elif "last week" in text:
        this_monday = day - timedelta(days=day.weekday())
        start, end = this_monday - timedelta(days=7), this_monday - timedelta(days=1)
        label = "last week"
    elif "this week" in text or "recently" in text:
        start = day - timedelta(days=day.weekday() if "this week" in text else 6)
        label = "this week" if "this week" in text else "recently"
    elif "last month" in text:
        first = day.replace(day=1)
        end = first - timedelta(days=1)
        start = end.replace(day=1)
        label = "last month"
    elif "this month" in text:
        start = day.replace(day=1)
        label = "this month"
    else:
        return None
    body = _bounds(start, end, tz)
    body["label"] = label
    body["timezone"] = str(tz)
    return body


def build_query_plan(
    question: str,
    *,
    timezone_name: str = "UTC",
    now: Optional[datetime] = None,
) -> Dict[str, Any]:
    """Propose one instrument. Does not look up records."""
    text = (question or "").strip()
    if not text:
        return {"error": "question_required", "detail": "plan_query needs a question"}
    try:
        tz = ZoneInfo(timezone_name or "UTC")
    except ZoneInfoNotFoundError:
        return {
            "error": "invalid_timezone",
            "detail": f"unknown timezone {timezone_name!r}",
        }
    moment = now or datetime.now(tz)
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    folded = text.casefold()
    window = _window(folded, tz, moment)
    hint = _field_hint(folded)
    plan: Dict[str, Any] = {
        "question": text,
        "filters": [],
        "window": window,
        "aggregation": None,
        "traversal": None,
        "sort": None,
        "limit": None,
        "limits": [
            "Ground the track with integral_list_tracks before filtering by name.",
            "Read field keys from integral_get_track_schema. A display label is not a key.",
            _ON_FAILURE,
        ],
        "on_failure": _ON_FAILURE,
    }
    ranking = _has(folded, _RANK) or (
        "most " in folded
        and _has(folded, ("value", "revenue", "lucrative", "expensive"))
    )
    if ranking and not folded.startswith("how many"):
        direction = "asc" if _has(folded, _RANK_ASC) else "desc"
        plan.update(
            instrument="integral_query_spec",
            reason="A value ranking is a sort over the full authorized set, not a semantic search.",
            sort=[
                {
                    "field": "custom_fields.<key>",
                    "direction": direction,
                    "hint": hint,
                }
            ],
            limit=_top_n(folded),
            filters=[{"field": "track_id", "op": "eq", "value": "<track id>"}],
        )
        plan["limits"].insert(
            0,
            "Do not call integral_query for this. A semantic miss is the wrong instrument, not an empty track.",
        )
        return plan
    if (
        (_has(folded, _AGG) or "minimum" in folded or "maximum" in folded)
        and "how many" not in folded
        and "total number" not in folded
    ):
        if "minimum" in folded:
            op = "min"
        elif "maximum" in folded:
            op = "max"
        elif _has(folded, ("average", "avg", "mean")):
            op = "avg"
        else:
            op = "sum"
        plan.update(
            instrument="integral_aggregate",
            reason="A total or average is integral_aggregate, not a page of rows added by hand.",
            aggregation={"op": op, "field": hint or "<key>"},
            filters=[{"field": "track_id", "op": "eq", "value": "<track id>"}],
        )
        plan["limits"].insert(
            0,
            "over_budget and incompatible_currency are the answer. Do not return a partial total.",
        )
        return plan
    if _has(folded, _COUNT) or "number of" in folded:
        group_by = "track"
        if "status" in folded:
            group_by = "status"
        elif "tag" in folded:
            group_by = "tag"
        elif "day" in folded or "date" in folded:
            group_by = "date"
        plan.update(
            instrument="integral_count_entries",
            reason="A how-many question is a count, not a fetched page.",
            aggregation={"op": "count", "group_by": group_by},
        )
        return plan
    if _has(folded, _RELATED):
        direction = "in" if "points at" in folded else "both"
        plan.update(
            instrument="integral_get_related",
            reason="Connections are relation hops, including anchors when asked what this links to.",
            traversal={
                "direction": direction,
                "include_anchors": "points at" not in folded,
            },
        )
        return plan
    if _has(folded, _DIGEST) or "been happening" in folded:
        itemized = _has(folded, ("who", "itemized", "what changed"))
        plan.update(
            instrument=(
                "integral_get_digest" if itemized else "integral_activity_digest"
            ),
            reason="Activity is a digest. An empty digest is empty activity, not a missing track.",
        )
        return plan
    if _has(folded, _FIND):
        plan.update(
            instrument="integral_query",
            reason="Open-ended wording is hybrid retrieval. It does not rank by a stored value.",
        )
        plan["limits"].insert(
            0,
            "If the question is actually a ranking or a total, discard this plan and re-plan. Do not answer not found.",
        )
        return plan
    plan.update(
        instrument="integral_query_entries",
        reason="A filtered list uses query_entries. Sort before paging when order matters.",
        limit=20,
    )
    return plan


async def plan_query(
    question: str,
    timezone: str = "UTC",
    workspace_id: Optional[str] = None,
) -> Dict[str, Any]:
    """Plan a question in the caller's timezone. Reads nothing."""
    del workspace_id
    return build_query_plan(question, timezone_name=timezone or "UTC")
