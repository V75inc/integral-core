"""Verbal review of a one-time, text-only reminder to the caller's own chat."""

import hashlib
import json
import re
from contextlib import contextmanager
from contextvars import ContextVar
from datetime import datetime
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from app.agentive.services.routine_tasks import parse_run_at
from app.api.errors import BadRequestError

_confirmation = ContextVar("voice_reminder_confirmation", default=None)


def reminder_review(item, work_id, run_id):
    """Project the exact one-time message, delivery location and local schedule."""
    from app.services.native_voice_review import VoiceReviewReadout

    p = item.payload
    if (
        item.kind != "routine_task_create"
        or set(p)
        != {
            "thread_id",
            "workspace_id",
            "agent_id",
            "instruction",
            "delivery_mode",
            "write_scope",
            "max_runs",
            "cron",
            "timezone",
            "run_at",
        }
        or p["thread_id"] != item.session_id
        or p["workspace_id"] != item.workspace_id
        or p["delivery_mode"] != "reminder"
        or p["write_scope"] != []
        or p["cron"] != ""
        or (
            p["max_runs"] is not None
            and (type(p["max_runs"]) is not int or p["max_runs"] != 1)
        )
        or not isinstance(p["instruction"], str)
        or not 0 < len(p["instruction"].strip()) <= 300
        or not p["instruction"].isprintable()
        or re.search(r"\b[one]\.[A-Z][A-Za-z0-9_]*\.[A-Za-z0-9_-]+", p["instruction"])
        or not isinstance(p["run_at"], str)
        or not isinstance(p["timezone"], str)
    ):
        return None
    try:
        when = datetime.fromisoformat(parse_run_at(p["run_at"], p["timezone"]))
        local = when.astimezone(ZoneInfo(p["timezone"]))
    except (BadRequestError, ValueError, ZoneInfoNotFoundError):
        return None
    clock = str(local.hour % 12 or 12)
    if local.minute or local.second:
        clock += f":{local.minute:02d}"
    if local.second:
        clock += f":{local.second:02d}"
    zone = p["timezone"].rsplit("/", 1)[-1].replace("_", " ")
    if local.replace(fold=0).utcoffset() != local.replace(fold=1).utcoffset():
        zone += f" (UTC{local:%z})"
    text = (
        f'send you the reminder “{p["instruction"]}” in this chat on '
        f"{local:%B} {local.day}, {local.year} at {clock} {local:%p} {zone} time"
    )
    fingerprint = hashlib.sha256(
        json.dumps([p, item.origin_proof], sort_keys=True).encode()
    ).hexdigest()
    return VoiceReviewReadout(
        text,
        work_id,
        run_id,
        item.token,
        item.session_id,
        fingerprint,
        reminder=dict(p),
    )


@contextmanager
def confirmed_reminder(review):
    """Bind the reviewed reminder for the duration of the decision."""
    token = _confirmation.set(review if review.reminder is not None else None)
    try:
        yield
    finally:
        _confirmation.reset(token)


def permits_reminder(item):
    """Require an identical projection of the pending reminder."""
    review = _confirmation.get()
    return (
        review is not None
        and reminder_review(item, review.work_id, review.run_id) == review
    )


async def read_saved_reminder(review, principal, workspace, *, source_proposal=None):
    """Verify the saved schedule against its execution receipt and current owner."""
    from app.agentive.services.staging_execution import load_staging_execution_result
    from app.agentive.services.staging_routine_commands import admit_owned_routine
    from app.agentive.staging import StagingError
    from app.api.errors import InsufficientPermissionsError
    from app.services.native_voice_review import VoiceAppliedReadout
    from app.services.release_admission import release_admission

    p = review.reminder
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
        or row.source_staging_token != (source_proposal or review.proposal_id)
        or row.thread_id != p["thread_id"]
        or row.agent_id != p["agent_id"]
        or row.instruction != p["instruction"].strip()
        or row.delivery_mode != "reminder"
        or row.cron
        or row.write_scope
        or row.max_runs != 1
        or row.status != "active"
        or row.timezone != p["timezone"]
        or row.next_run_at != p["run_at"]
    ):
        return None
    return VoiceAppliedReadout(
        "Your reminder is scheduled for this chat.", review, "", ""
    )
