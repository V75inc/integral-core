"""Spoken review of a single record creation and a one-time private reminder."""

import hashlib
import json
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import replace

from app.agentive.staging import StagingError
from app.services.native_voice_creation import read_creation_review
from app.services.native_voice_reminder import reminder_review
from app.services.native_voice_review import VoiceReviewReadout

_confirmed = ContextVar("confirmed_voice_batch", default=None)


def _fingerprint(item):
    return hashlib.sha256(
        json.dumps(
            [
                item.token,
                item.user_id,
                item.workspace_id,
                item.session_id,
                item.origin_proof,
                item.payload,
            ],
            sort_keys=True,
            ensure_ascii=False,
            allow_nan=False,
        ).encode()
    ).hexdigest()


async def read_batch_review(item, work_id, run_id):
    """Project the ordered literal actions as one spoken question."""
    if set(item.payload) - {"operations", "label", "idempotency_key"}:
        return None
    ops = item.payload.get("operations")
    if (
        not isinstance(ops, list)
        or len(ops) != 2
        or any(
            not isinstance(op, dict)
            or set(op) - {"kind", "payload", "summary", "diff_human", "diff_machine"}
            or not isinstance(op.get("payload"), dict)
            or not isinstance(op.get("kind"), str)
            for op in ops
        )
    ):
        return None
    if {op["kind"] for op in ops} != {"create_entry", "routine_task_create"}:
        return None
    children = []
    for index, op in enumerate(ops):
        # Execution can substitute positional and named references. Only
        # literal, independently reviewable child payloads qualify.
        serialized = json.dumps(op["payload"])
        if "{{" in serialized or "$batch." in serialized:
            return None
        child = replace(
            item,
            kind=op["kind"],
            payload=op["payload"],
            token=f"{item.token}:step:{index}",
        )
        if child.kind == "create_entry":
            try:
                readout = await read_creation_review(child, work_id, run_id)
            except StagingError:
                return None
        else:
            readout = reminder_review(child, work_id, run_id)
        if readout is None:
            return None
        children.append(readout)
    text = ", and also ".join(child.action for child in children)
    if len(text) > 1850:
        return None
    return VoiceReviewReadout(
        text,
        work_id,
        run_id,
        item.token,
        item.session_id,
        _fingerprint(item),
        children=tuple(children),
        speech_action=", and also ".join(
            child.speech_action or child.action for child in children
        ),
    )


@contextmanager
def confirmed_batch(review):
    """Bind only this reviewed pair during effect execution."""
    token = _confirmed.set(review if review.children else None)
    try:
        yield
    finally:
        _confirmed.reset(token)


def permits_batch(item):
    """Match the current batch against the reviewed operation fingerprint."""
    review = _confirmed.get()
    return (
        item.kind == "batch"
        and review is not None
        and _fingerprint(item) == review.fingerprint
    )
