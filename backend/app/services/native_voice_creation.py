"""Exact voice review of simple creation through the canonical Entry writer."""

import hashlib
import json
from contextlib import contextmanager
from contextvars import ContextVar

from app.agentive.staging import StagingError

_confirmed_creation = ContextVar("confirmed_voice_creation", default=None)


def _digest(value):
    return hashlib.sha256(
        json.dumps(
            value, sort_keys=True, separators=(",", ":"), allow_nan=False
        ).encode()
    ).hexdigest()


@contextmanager
def confirmed_creation(review):
    """Bind the exact independently confirmed projection to this effect task."""
    token = _confirmed_creation.set(review if review.creation else None)
    try:
        yield
    finally:
        _confirmed_creation.reset(token)


def permits_creation(proposal_id):
    """Require a matching private confirmation before voice can decide creation."""
    review = _confirmed_creation.get()
    return review is not None and review.proposal_id == proposal_id


def check_creation_schema(track, entry_type, spec):
    """Admit only creation whose scalar defaults can be reviewed before execution."""
    from app.services.hooks.registry import get_workspace_hooks

    fields = spec.get("fields") or []
    if get_workspace_hooks(track.workspace_id, "entry.create") or any(
        field.get("type", "text")
        not in {"text", "markdown", "date", "number", "boolean", "select"}
        or field.get("composite")
        or (field.get("validation") or {}).get("preview_default_tool")
        or str(field.get("key", "")).startswith("_")
        for field in fields
    ):
        raise StagingError(
            "strong_confirmation_required",
            "This record's defaults or integrations need its approval card.",
        )
    return _digest({"track": track.id, "type": entry_type.id, "spec": spec})


async def read_creation_review(item, work_id, run_id):
    """Project the canonical form and validated effective values without writing."""
    from app.agentive.services.entry_proposal_revision import _contexts
    from app.models.nodes import EntryType, Track
    from app.services.native_voice_review import VoiceReviewReadout
    from app.services.operational_model_runtime import (
        resolve_entry_type_spec,
        resolve_track_runtime_profile,
        validate_and_materialize_entry_custom_fields,
    )

    if set(item.payload) - {
        "track_id",
        "title",
        "fields",
        "entry_type",
        "strict_fields",
        "body",
        "tags",
    }:
        return None
    if item.payload.get("tags"):
        return None
    rows = await _contexts(item)
    if len(rows) != 1:
        return None
    row = rows[0]
    entry = row["entry"]
    track = await Track.get(entry["track_id"])
    entry_type = await EntryType.get(entry["type_id"])
    if track is None or entry_type is None:
        return None
    _, tier, _ = await resolve_track_runtime_profile(track)
    spec = resolve_entry_type_spec(entry_type, tier)
    schema = check_creation_schema(track, entry_type, spec)
    declared = {field["key"] for field in spec.get("fields") or []}
    if set(entry["custom_fields"]) - declared:
        return None
    fields, refs = await validate_and_materialize_entry_custom_fields(
        track=track,
        entry_type=entry_type,
        custom_fields=entry["custom_fields"],
        runtime_tier=tier,
        actor_user_id=item.user_id,
        source_entry_title=entry["title"],
        materialize=False,
    )
    if (
        refs
        or any(
            fields.get(f["key"])
            for f in spec.get("fields") or []
            if f.get("type") == "markdown"
        )
        or not entry["title"]
        or len(entry["title"]) > 200
        or len(entry["body"]) > 200
    ):
        return None
    labels = {
        f["key"]: str(f.get("label") or f.get("name") or f["key"])
        for f in spec.get("fields") or []
    }
    values = [
        f"{labels[key]}: {value}."
        for key, value in fields.items()
        if value is not None and key in labels
    ]
    text = (
        f"Create {entry['title']} in {row['track'].get('title', 'the selected Track')}. "
        + (f"Notes: {entry['body']}. " if entry["body"] else "")
        + " ".join(values)
        + " This has not been applied."
    )
    if len(text) > 1800:
        return None
    creation = {
        "schema": schema,
        "track": track.id,
        "type": entry_type.id,
        "title": entry["title"],
        "body": entry["body"],
        "fields": fields,
        "payload": item.payload,
    }
    return VoiceReviewReadout(
        text,
        work_id,
        run_id,
        item.token,
        track.id,
        _digest({"payload": item.payload, "creation": creation}),
        (
            ("title", "Title"),
            *tuple(
                (f"fields.{key}", label)
                for key, label in labels.items()
                if key in fields
            ),
        ),
        creation,
        tuple(f["key"] for f in spec.get("fields") or [] if f.get("type") == "date"),
    )


def validate_confirmed_creation(
    track, entry_type, spec, title, body, fields, tags, attachments
):
    """Run in the owning Entry transaction before any creation side effect."""
    review = _confirmed_creation.get()
    if review is None:
        return
    expected = review.creation
    actual = {
        "schema": check_creation_schema(track, entry_type, spec),
        "track": track.id,
        "type": entry_type.id,
        "title": title,
        "body": body,
        "fields": fields,
    }
    if fields is None:
        actual["fields"] = expected["fields"]
    if (
        tags
        or attachments
        or actual != {key: value for key, value in expected.items() if key != "payload"}
    ):
        raise StagingError(
            "proposal_changed",
            "The reviewed record or schema changed. Request a fresh proposal.",
        )
