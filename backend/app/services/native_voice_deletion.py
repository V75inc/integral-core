"""Deliberate spoken confirmation for one revision-bound leaf Entry deletion.

This is not a standing grant. All other destructive effects retain their
existing explicit review gate, and the canonical Entry writer rechecks the
leaf constraint inside the effect transaction.
"""

import asyncio
import hashlib
import json
import re
from contextlib import contextmanager
from contextvars import ContextVar

from app.models.edges import ANCHORS
from app.models.nodes import App, Entry, Track
from app.services.native_voice_creation import is_spoken_note

_confirmation = ContextVar("voice_entry_deletion_confirmation", default=None)


def _fingerprint(item):
    return hashlib.sha256(
        json.dumps(
            [
                item.token,
                item.user_id,
                item.workspace_id,
                item.session_id,
                item.kind,
                item.payload,
                item.origin_proof,
            ],
            sort_keys=True,
            ensure_ascii=False,
            allow_nan=False,
        ).encode()
    ).hexdigest()


async def _has_anchors(entry_id):
    from jvspatial.core.context import get_default_context

    return bool(
        await get_default_context().database.find(
            "edge", {"source": entry_id, "entity": ANCHORS._entity_name()}, limit=1
        )
    )


async def deletion_review(item, work_id, run_id):
    """Describe exactly one current, authorized, unambiguous leaf record."""
    from app.schemas.policy import Resource, Subject
    from app.services.native_voice_review import (
        VoiceReviewReadout,
        spoken_review_action,
    )
    from app.services.policy_engine import evaluate
    from app.services.resource_location import DATA_APP_STATES, locate_resource

    p = item.payload
    if (
        item.kind != "delete_entry"
        or not {"entry_id", "expected_record_revision"}
        <= set(p)
        <= {"entry_id", "expected_record_revision", "expected_schema_revision"}
        or type(p.get("expected_record_revision")) is not int
    ):
        return None
    target = p["entry_id"]
    location = await locate_resource(
        "entry", target, allowed_app_states=DATA_APP_STATES
    )
    if not location or location.workspace_id != item.workspace_id:
        return None
    for action in ("entry.read", "entry.delete"):
        decision = await evaluate(
            subject=Subject(kind="human", id=item.user_id),
            action=action,
            resource=Resource(
                kind="entry",
                id=target,
                scope=(
                    f"track:{location.track_id}"
                    if action == "entry.read"
                    else f"entry:{target}"
                ),
            ),
        )
        if not decision.allowed:
            return None
    entry = await Entry.get(target)
    track = await Track.get(location.track_id)
    if (
        not entry
        or not track
        or entry.record_revision != p["expected_record_revision"]
        or not entry.title
        or not is_spoken_note(entry.title)
        or not track.title
        or not is_spoken_note(track.title)
        or await _has_anchors(target)
    ):
        return None
    # A duplicate name cannot be distinguished safely by a spoken title alone.
    from jvspatial.core.context import get_default_context

    matches = await get_default_context().database.find(
        "node",
        {
            "entity": Entry._entity_name(),
            "context.track_id": track.id,
            "context.title": {
                "$regex": "^" + re.escape(entry.title) + "$",
                "$options": "i",
            },
        },
        limit=2,
    )
    if len(matches) != 1 or matches[0]["id"] != target:
        return None
    destination = f"“{track.title}”"
    if location.app_id:
        app = await App.get(location.app_id)
        if not app or not app.name or not is_spoken_note(app.name):
            return None
        destination += f" in “{app.name}”"
    action = f"permanently delete “{entry.title}” from {destination}"
    return VoiceReviewReadout(
        action,
        work_id,
        run_id,
        item.token,
        target,
        _fingerprint(item),
        deletion={"payload": p, "title": entry.title, "destination": destination},
        speech_action=spoken_review_action(
            action, entry.title, track.title, app.name if location.app_id else ""
        ),
    )


def spoken_deletion_choice(transcript: str) -> str | None:
    """A refusal or an explicit deletion instruction after the exact readback."""
    from app.services.native_voice_confirmation import spoken_review_choice

    if spoken_review_choice(transcript) == "reject":
        return "reject"
    if not isinstance(transcript, str) or len(transcript) > 180 or "?" in transcript:
        return None
    text = " ".join(
        re.sub(r"[,.!]", " ", transcript.casefold().replace("’", "'")).split()
    )
    return (
        "approve"
        if re.fullmatch(
            r"(?:(?:yes|yeah|okay|ok|sure|go ahead) )?(?:please )?(?:permanently )?(?:delete|remove) (?:it|that|this|the record)(?: please)?",
            text,
        )
        else None
    )


@contextmanager
def confirmed_deletion(review):
    """Bind only the exact independently heard deliberate decision to this task."""
    reset = _confirmation.set(
        (asyncio.current_task(), review) if review.deletion else None
    )
    try:
        yield
    finally:
        _confirmation.reset(reset)


def permits_deletion(item):
    """A voice model cannot manufacture this private confirmation binding."""
    binding = _confirmation.get()
    return bool(
        binding
        and binding[0] == asyncio.current_task()
        and binding[1].proposal_id == item.token
        and item.kind == "delete_entry"
        and binding[1].fingerprint == _fingerprint(item)
    )


async def recheck_voice_deletion(entry):
    """Recheck the leaf fence under the canonical locked Entry effect."""
    from app.api.errors import ResourceConflictError

    binding = _confirmation.get()
    if not binding or binding[0] != asyncio.current_task():
        return
    review = binding[1]
    if (
        entry.id != review.target_id
        or entry.record_revision
        != review.deletion["payload"]["expected_record_revision"]
        or await _has_anchors(entry.id)
    ):
        raise ResourceConflictError(
            message="The deletion changed since the spoken confirmation. Review it again."
        )


async def read_saved_deletion(review, principal, workspace):
    """Verify the canonical receipt and absence, not just model speech."""
    from jvspatial.core.context import get_default_context

    from app.agentive.services.staging_execution import load_staging_execution_result
    from app.services.authority_facts import primary_node_record
    from app.services.native_voice_review import VoiceAppliedReadout

    result = await load_staging_execution_result(
        token=review.proposal_id,
        principal_id=principal,
        workspace_id=workspace,
        kind="delete_entry",
        payload=review.deletion["payload"],
    )
    if (
        not result
        or result.get("entry_id") != review.target_id
        or not result.get("deleted")
        or await primary_node_record(
            get_default_context().database, Entry, review.target_id
        )
        is not None
    ):
        return None
    text = f'I deleted “{review.deletion["title"]}”.'
    return VoiceAppliedReadout(text, review, "", "", brief=text)
