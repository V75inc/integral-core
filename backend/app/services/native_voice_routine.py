"""Canonical verbal review of scheduled read-only routines.

The proposal is the same staged write that the card would decide. A spoken
decision never widens a routine's ongoing write authority. Only exact, bounded
review projections are permitted; malformed, unreviewable, or privileged
effects stay pending for the standard visual review.
"""

import hashlib
import json
import re
from contextlib import contextmanager
from contextvars import ContextVar
from datetime import datetime
from zoneinfo import ZoneInfo

from app.agentive.services.routine_tasks import compute_next_run_at, parse_run_at
from app.api.errors import BadRequestError
from app.services.native_voice_review import VoiceReviewReadout

_confirmed = ContextVar("voice_routine_review", default=None)
_BASE = {
    "thread_id",
    "workspace_id",
    "agent_id",
    "instruction",
    "delivery_mode",
    "write_scope",
    "max_runs",
    "cron",
    "timezone",
}
_DAYS = ("Sunday", "Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday")


def _clock(hour, minute):
    return f"{hour % 12 or 12}:{minute:02d} {'AM' if hour < 12 else 'PM'}"


def _cron_description(cron):
    """Describe common exact cron schedules. Complex expressions stay visual."""
    if not isinstance(cron, str):
        return None
    fields = cron.split()
    if len(fields) != 5:
        return None
    minute, hour, dom, month, dow = fields
    if dom != "*" or month != "*":
        return None
    if hour == "*" and dow == "*":
        if minute == "*":
            return "every minute"
        if re.fullmatch(r"\*/[1-9][0-9]?", minute):
            step = int(minute[2:])
            # Cron steps restart each hour, so only factors of sixty are
            # equivalent to a regular N-minute interval.
            if step <= 60 and 60 % step == 0:
                return f"every {step} minutes"
        if minute.isdecimal() and 0 <= int(minute) <= 59:
            return f"every hour at minute {int(minute)}"
    if not minute.isdecimal() or not hour.isdecimal():
        return None
    m, h = int(minute), int(hour)
    if not (0 <= m <= 59 and 0 <= h <= 23):
        return None
    if dow == "*":
        return f"every day at {_clock(h, m)}"
    if dow.upper() in {"1-5", "MON-FRI"}:
        return f"weekdays at {_clock(h, m)}"
    if dow.isdecimal() and 0 <= int(dow) <= 7:
        return f"every {_DAYS[int(dow) % 7]} at {_clock(h, m)}"
    return None


def _fingerprint(item):
    return hashlib.sha256(
        json.dumps(
            [
                item.token,
                item.user_id,
                item.workspace_id,
                item.session_id,
                item.kind,
                item.origin_proof,
                item.payload,
            ],
            sort_keys=True,
            ensure_ascii=False,
            allow_nan=False,
        ).encode()
    ).hexdigest()


def routine_review(item, work_id, run_id):
    """Read the actual staged schedule and its complete, non-writing scope."""
    p = item.payload
    # The established one-shot literal reminder has its own exact adapter.
    # Do not broaden its admissible bounds by falling through.
    if p.get("delivery_mode") == "reminder" and p.get("run_at"):
        return None
    if (
        item.kind != "routine_task_create"
        or set(p) not in (_BASE, _BASE | {"run_at"})
        or p["thread_id"] != item.session_id
        or p["workspace_id"] != item.workspace_id
        or p["delivery_mode"] not in {"agent", "reminder", "assignment_digest"}
        or p["write_scope"] != []
        or not isinstance(p["instruction"], str)
        or not 0 < len(p["instruction"].strip()) <= 600
        or not p["instruction"].isprintable()
        or re.search(r"\b[one]\.[A-Z][A-Za-z0-9_]*\.[A-Za-z0-9_-]+", p["instruction"])
        or not isinstance(p["timezone"], str)
        or not isinstance(p["cron"], str)
        or (
            p["max_runs"] is not None
            and (type(p["max_runs"]) is not int or p["max_runs"] <= 0)
        )
    ):
        return None
    try:
        zone = ZoneInfo(p["timezone"])
        run_at = p.get("run_at")
        if run_at:
            if p["cron"] or p["max_runs"] not in (None, 1):
                return None
            instant = datetime.fromisoformat(
                parse_run_at(run_at, p["timezone"])
            ).astimezone(zone)
            # Include the local date and offset for repeated DST hours.
            local_offset = (
                f", UTC{instant:%z}"
                if instant.replace(fold=0).utcoffset()
                != instant.replace(fold=1).utcoffset()
                else ""
            )
            cadence = f"on {instant:%B} {instant.day}, {instant.year} at {_clock(instant.hour, instant.minute)}"
            if instant.second:
                return None
            limit = "run once"
        else:
            if not p["cron"]:
                return None
            cadence = _cron_description(p["cron"])
            if not cadence:
                return None
            compute_next_run_at(p["cron"], p["timezone"])
            local_offset = ""
            limit = (
                f"stop after {p['max_runs']} successful runs"
                if p["max_runs"] is not None
                else "continue until you pause or stop it"
            )
    except (BadRequestError, ValueError, TypeError, KeyError):
        return None
    zone_label = p["timezone"].replace("_", " ").replace("/", " ")
    delivery = {
        "agent": "run this task with fresh workspace context and post its result here",
        "reminder": "post this exact reminder in this chat",
        "assignment_digest": "check for new review-ready assignments you sponsored and post the digest here",
    }[p["delivery_mode"]]
    scope = (
        "It has no permission to automatically apply workspace writes. Any proposed changes still need review."
        if p["delivery_mode"] == "agent"
        else "It cannot automatically change workspace records."
    )
    action = (
        f'{delivery}: {p["instruction"]}. Schedule: {cadence}, {zone_label} time{local_offset}. '
        f"It will {limit}. {scope}"
    )
    return VoiceReviewReadout(
        action,
        work_id,
        run_id,
        item.token,
        item.session_id,
        _fingerprint(item),
        routine=dict(p),
    )


@contextmanager
def confirmed_routine(review):
    token = _confirmed.set(review if review.routine is not None else None)
    try:
        yield
    finally:
        _confirmed.reset(token)


def permits_routine(item):
    reviewed = _confirmed.get()
    return (
        reviewed is not None
        and routine_review(item, reviewed.work_id, reviewed.run_id) == reviewed
    )


async def read_saved_routine(review, principal, workspace):
    """Confirm the exact persisted schedule and owner using its staged receipt."""
    from app.agentive.services.staging_execution import load_staging_execution_result
    from app.agentive.services.staging_routine_commands import admit_owned_routine
    from app.agentive.staging import StagingError
    from app.api.errors import InsufficientPermissionsError
    from app.services.native_voice_review import VoiceAppliedReadout
    from app.services.release_admission import release_admission

    p = review.routine
    receipt = await load_staging_execution_result(
        token=review.proposal_id,
        principal_id=principal,
        workspace_id=workspace,
        kind="routine_task_create",
        payload=p,
    )
    routine_id = (receipt or {}).get("routine_id")
    if not routine_id:
        return None
    try:
        async with release_admission(principal, workspace):
            row = await admit_owned_routine(principal, workspace, routine_id)
    except (StagingError, InsufficientPermissionsError):
        return None
    if (
        row is None
        or row.user_id != principal
        or row.workspace_id != workspace
        or row.source_staging_token != review.proposal_id
        or row.thread_id != p["thread_id"]
        or row.agent_id != p["agent_id"]
        or row.instruction != p["instruction"].strip()
        or row.delivery_mode != p["delivery_mode"]
        or row.cron != p["cron"]
        or row.write_scope
        or row.timezone != p["timezone"]
        or row.max_runs != (p["max_runs"] if not p.get("run_at") else 1)
        or row.status != "active"
        or (p.get("run_at") and row.next_run_at != p["run_at"])
        or not row.next_run_at
    ):
        return None
    return VoiceAppliedReadout(
        "Your routine is scheduled in this chat.", review, "", ""
    )
