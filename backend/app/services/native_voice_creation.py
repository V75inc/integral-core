"""Exact voice review of simple creation through the canonical Entry writer."""

import hashlib
import json
import re
from contextlib import contextmanager
from contextvars import ContextVar

from markdown_it import MarkdownIt

from app.agentive.staging import StagingError

_note_parser = MarkdownIt("commonmark").enable("strikethrough")
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
    review = next((child for child in review.children if child.creation), review)
    token = _confirmed_creation.set(review if review.creation else None)
    try:
        yield
    finally:
        _confirmed_creation.reset(token)


def permits_creation(proposal_id):
    """Require a matching private confirmation before voice can decide creation."""
    review = _confirmed_creation.get()
    return review is not None and review.proposal_id == proposal_id


def check_creation_schema(track, entry_type, spec, values=None):
    """Admit only creation whose scalar defaults can be reviewed before execution."""
    from app.services.hooks.registry import get_workspace_hooks

    fields = spec.get("fields") or []
    values = values or {}

    def spoken_field(field):
        if field.get("type", "text") == "member":
            # An empty optional assignment has no recipient or membership effect.
            return (
                values.get(field["key"]) is None
                and field.get("default") is None
                and not field.get("default_from_setting")
                and not field.get("required")
                and not field.get("required_on_create")
            )
        return field.get("type", "text") in {
            "text",
            "markdown",
            "date",
            "number",
            "boolean",
            "select",
        }

    if get_workspace_hooks(track.workspace_id, "entry.create") or any(
        not spoken_field(field)
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


def is_spoken_note(value):
    """A short, single line of literal prose can be read back in full.

    Markup, links, controls and structured editor content require visual review.
    """
    if value is None or value == "":
        return True
    if (
        not isinstance(value, str)
        or len(value) > 200
        or not value.isprintable()
        or re.search(r"(?i)\b(?:[a-z][a-z0-9+.-]*://|www\.)|\S@\S", value)
    ):
        return False
    blocks = _note_parser.parse(value)
    if [block.type for block in blocks] != [
        "paragraph_open",
        "inline",
        "paragraph_close",
    ]:
        return False
    parts = blocks[1].children or []
    return len(parts) == 1 and parts[0].type == "text" and parts[0].content == value


def _spoken_value(value):
    # Numeric text can be an identifier with leading zeros or repeated digits.
    if isinstance(value, str) and re.fullmatch(r"[0-9]{2,}", value):
        digits = (
            "zero",
            "one",
            "two",
            "three",
            "four",
            "five",
            "six",
            "seven",
            "eight",
            "nine",
        )
        return ", ".join(digits[int(char)] for char in value)
    return value


def _spoken_fields(display):
    # Share a value once while keeping every field in the audible proposal.
    grouped = {}
    for label, value in display:
        key = (type(value), value)
        grouped.setdefault(key, []).append(label)
    return [
        f"{' and '.join(labels)} set to {_spoken_value(value)}"
        for (_, value), labels in grouped.items()
    ]


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
    schema = check_creation_schema(track, entry_type, spec, entry["custom_fields"])
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
            not is_spoken_note(fields.get(f["key"]))
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
    destination = row["track"].get("title") or "the selected track"
    text = f'add “{entry["title"]}” to {destination}'
    display = []
    if entry["body"] and entry["body"] != entry["title"]:
        display.append(("Description", entry["body"]))
    display.extend(
        (labels[key], value)
        for key, value in fields.items()
        if value is not None and key in labels
    )
    details = _spoken_fields(display)
    if details:
        text += (
            ", with "
            + (", ".join(details[:-1]) + " and " if len(details) > 1 else "")
            + details[-1]
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
        destination_label=row["track"].get("title") or "the selected Track",
        creation_text_keys=tuple(
            f["key"]
            for f in spec.get("fields") or []
            if f.get("type") in {"text", "markdown"}
        ),
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
        "schema": check_creation_schema(
            track, entry_type, spec, expected["fields"] if fields is None else fields
        ),
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
