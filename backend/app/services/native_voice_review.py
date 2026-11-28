"""Read the canonical, current review for one exact native voice task.

This is a read-only projection. Speech-model output is never a human decision.
"""

import hashlib
import json
import math
import re
from dataclasses import dataclass, field
from datetime import date

from app.agentive.services.approval_decisions import proposal_from_staged
from app.agentive.staging import list_pending_tokens
from app.middleware.permissions_cache import isolated_permissions_cache
from app.models.nodes import Entry, EntryType, Track
from app.schemas.policy import Resource, Subject
from app.services.policy_engine import evaluate
from app.services.resource_location import DATA_APP_STATES, locate_resource

_VOICE_CAPABILITIES = {
    "create_entry": "integral_create_entry",
    "delete_entry": "integral_delete_entry",
    "update_entry": "integral_update_entry",
    "batch": "integral_commit_batch",
    "routine_task_create": "integral_schedule_task",
    "update_app": "integral_update_app",
    "create_dashboard": "integral_create_dashboard",
    "update_dashboard": "integral_update_dashboard",
}


async def pending_review_work(thread, principal, workspace):
    """Recover only the exact supported proposal still offered by this owned chat after reconnect."""
    from app.agentive.staging import get_token
    from app.services.prompt_queue import get_queue

    if thread.user_id != principal or thread.workspace_id != workspace:
        return None
    queue = get_queue(thread)
    pending = [i for i in queue["items"] if i.get("status") == "pending"]
    if queue["status"] != "open" or len(pending) != 1:
        return None
    item = pending[0]
    if (
        item.get("kind") != "staged_write"
        or item.get("write_kind") not in _VOICE_CAPABILITIES
        or not item.get("token")
    ):
        return None
    change = await get_token(item["token"])
    if (
        change is None
        or change.kind != item["write_kind"]
        or change.token != item["token"]
        or change.state != "pending"
        or change.is_expired()
        or (change.user_id, change.workspace_id, change.session_id)
        != (principal, workspace, thread.id)
    ):
        return None
    proof = change.origin_proof or {}
    if (
        proof.get("version") != "work-proposal-v1"
        or proof.get("capability_key") != _VOICE_CAPABILITIES[change.kind]
        or not proof.get("work_item_id")
        or not proof.get("run_id")
        or (
            proof.get("principal_id"),
            proof.get("workspace_id"),
            proof.get("thread_id"),
        )
        != (principal, workspace, thread.id)
    ):
        return None
    return proof["work_item_id"], change.token


def spoken_review_action(action: str, *literal_values: object) -> str:
    """Remove display-only quotation delimiters, never literal quoted data.

    Voice cannot pronounce typographic formatting on its own. Callers must
    supply the actual data used to construct the action. If any literal
    contains quotes, retain the strict original text; this is not permission
    to normalize a literal value, identifier, number or other punctuation.
    """

    def literal_quotes(value):
        if isinstance(value, str):
            return any(mark in value for mark in ('"', "“", "”"))
        if isinstance(value, dict):
            return any(literal_quotes(item) for item in value.values())
        if isinstance(value, (list, tuple)):
            return any(literal_quotes(item) for item in value)
        return False

    if any(literal_quotes(value) for value in literal_values):
        return action
    return action.replace("“", "").replace("”", "")


@dataclass(frozen=True)
class VoiceReviewReadout:
    action: str = field(repr=False)
    work_id: str = field(repr=False)
    run_id: str = field(repr=False)
    proposal_id: str = field(repr=False)
    target_id: str = field(repr=False)
    fingerprint: str = field(repr=False)
    fields: tuple[tuple[str, str], ...] = field(default=(), repr=False)
    creation: dict | None = field(default=None, repr=False)
    creation_date_keys: tuple[str, ...] = field(default=(), repr=False)
    reminder: dict | None = field(default=None, repr=False)
    presentation: dict | None = field(default=None, repr=False)
    deletion: dict | None = field(default=None, repr=False)
    creation_text_keys: tuple[str, ...] = field(default=(), repr=False)

    destination_label: str = field(default="", repr=False)
    children: tuple["VoiceReviewReadout", ...] = field(default=(), repr=False)
    update_field_types: tuple[tuple[str, str], ...] = field(default=(), repr=False)
    speech_action: str = field(default="", repr=False)

    def _question(self, action: str) -> str:
        if self.deletion:
            return f"Would you like me to {action}? This also removes its comments and sharing links. Attached files will no longer be accessible through this record. This cannot be undone. Say yes, delete it, or no to keep it."
        return f"Would you like me to {action}?"

    @property
    def text(self) -> str:
        return self._question(self.action)

    @property
    def speech_text(self) -> str:
        return self._question(self.speech_action or self.action)

    def matches_focus(self, focus):
        if self.children:
            return all(child.matches_focus(focus) for child in self.children)
        return (
            self.reminder is not None
            or (
                self.presentation is not None
                and focus is not None
                and focus.id == self.target_id
            )
            or focus is None
            or (
                focus.kind == ("track" if self.creation else "entry")
                and focus.id == self.target_id
            )
        )


async def pending_work_proposal(
    *, principal, workspace, thread, work_id, run_id, replacement=None
):
    """Resolve one current proposal with the exact authenticated work origin."""
    pending = []
    for item in await list_pending_tokens(principal, thread):
        if (
            item.user_id != principal
            or item.session_id != thread
            or item.workspace_id != workspace
            or item.state != "pending"
            or item.is_expired()
        ):
            continue
        proof = item.origin_proof or {}
        if replacement is not None:
            if (
                item.token != replacement[0]
                or item.supersedes_token != replacement[1]
                or item.origin_proof is not None
                or "0" not in item.entry_edit_fences
            ):
                continue
        elif (
            proof.get("version") != "work-proposal-v1"
            or proof.get("capability_key") != _VOICE_CAPABILITIES.get(item.kind)
            or proof.get("work_item_id") != work_id
            or proof.get("run_id") != run_id
            or proof.get("principal_id") != principal
            or proof.get("workspace_id") != workspace
            or proof.get("thread_id") != thread
        ):
            continue
        pending.append(item)
    if len(pending) != 1:
        return None
    return pending[0]


def _simple_field_text(spec, value, *, previous=False):
    """Speak a bounded scalar without resolving references or executing hooks.

    The writer still validates the actual submitted value and schema. Reject
    composite, derived, protected or reference fields rather than expanding a
    private-list standing grant to an unknown effect.
    """
    from app.services.native_voice_creation import _spoken_value, is_spoken_note

    kind = spec.get("type")
    if (
        kind not in {"text", "select", "number", "boolean", "date"}
        or spec.get("composite")
        or spec.get("readonly")
        or (spec.get("validation") or {}).get("preview_default_tool")
    ):
        return None
    if value is None:
        return "empty" if previous else None
    if kind == "date":
        if not isinstance(value, str):
            return None
        try:
            return value if date.fromisoformat(value).isoformat() == value else None
        except ValueError:
            return None
    if kind == "boolean":
        return str(value).lower() if type(value) is bool else None
    if kind == "number":
        if type(value) not in {int, float} or (
            type(value) is float and not math.isfinite(value)
        ):
            return None
        text = str(value)
        return text if len(text) <= 200 else None
    if not isinstance(value, str) or not is_spoken_note(value):
        return None
    if kind == "select" and not previous:
        choices = spec.get("enum") or []
        if choices and value not in choices:
            return None
    return _spoken_value(value) if value else "empty"


async def read_entry_review(
    *,
    principal,
    workspace,
    thread,
    work_id,
    run_id,
    replacement=None,
    allow_description_update=False,
    allow_deletion=False,
):
    """Read one exact pending record change or one-time reminder for this work attempt."""
    with isolated_permissions_cache():
        item = await pending_work_proposal(
            principal=principal,
            workspace=workspace,
            thread=thread,
            work_id=work_id,
            run_id=run_id,
            replacement=replacement,
        )
        if item is None:
            return None
        if item.kind == "delete_entry" and allow_deletion and replacement is None:
            from app.services.native_voice_deletion import deletion_review

            return await deletion_review(item, work_id, run_id)
        if (
            item.kind in {"update_app", "create_dashboard", "update_dashboard"}
            and replacement is None
        ):
            from app.services.native_voice_presentation import presentation_review

            return await presentation_review(item, work_id, run_id)
        if item.kind == "batch" and replacement is None:
            from app.services.native_voice_batch import read_batch_review

            return await read_batch_review(item, work_id, run_id)
        if item.kind == "routine_task_create" and replacement is None:
            from app.services.native_voice_reminder import reminder_review

            return reminder_review(item, work_id, run_id)
        if item.kind == "create_entry":
            from app.agentive.staging import StagingError
            from app.services.native_voice_creation import read_creation_review

            try:
                return await read_creation_review(item, work_id, run_id)
            except StagingError:
                return None
        allowed_keys = {
            "entry_id",
            "title",
            "status",
            "fields",
            "expected_record_revision",
            "expected_schema_revision",
        }
        if replacement is not None:
            allowed_keys.update(("body", "tags"))
        elif allow_description_update:
            allowed_keys.add("body")
        if item.kind != "update_entry" or set(item.payload) - allowed_keys:
            return None
        proposal = proposal_from_staged(item)
        if (
            not proposal.target_id
            or not proposal.human_diff
            or len(proposal.human_diff) > 1800
        ):
            return None
        location = await locate_resource(
            "entry", proposal.target_id, allowed_app_states=DATA_APP_STATES
        )
        if location is None or location.workspace_id != workspace:
            return None
        decision = await evaluate(
            subject=Subject(kind="human", id=principal),
            action="entry.read",
            resource=Resource(
                kind="entry", id=proposal.target_id, scope=f"track:{location.track_id}"
            ),
        )
        if not decision.allowed:
            return None
        from jvspatial.core.context import get_default_context

        from app.services.authority_facts import primary_node_record

        record = await primary_node_record(
            get_default_context().database, Entry, proposal.target_id
        )
        entry = record["context"] if record else None
        if (
            entry is None
            or type(entry.get("record_revision")) is not int
            or entry["record_revision"] != item.payload.get("expected_record_revision")
        ):
            return None
        # Limit the first spoken projection to simple record edits whose
        # values do not require dereferencing other protected resources.
        fields = item.payload.get("fields") or {}
        if not isinstance(fields, dict):
            return None
        if replacement is not None:
            if "body" in item.payload and (item.payload["body"] or "") != (
                entry.get("body") or ""
            ):
                return None
            if "tags" in item.payload:
                from app.models.edges import TAGGED_WITH

                current_entry = await Entry.get(proposal.target_id)
                if current_entry is None or not isinstance(item.payload["tags"], list):
                    return None
                actual_tags = await current_entry.nodes(
                    edge=[TAGGED_WITH], node=["Tag"], limit=501
                )
                if len(actual_tags) > 500 or set(item.payload["tags"]) != {
                    tag.id for tag in actual_tags
                }:
                    return None
            fields = {
                key: value
                for key, value in fields.items()
                if value != (entry.get("custom_fields") or {}).get(key)
            }
        changes = []
        spoken_fields = []
        for key, label in (("title", "Title"), ("status", "Status")):
            if key not in item.payload:
                continue
            before, after = entry.get(key), item.payload[key]
            if before == after:
                continue
            if (
                not isinstance(before, str)
                or not isinstance(after, str)
                or max(len(before), len(after)) > 200
            ):
                return None
            changes.append(f"{label} from {before or 'empty'} to {after}")
            spoken_fields.append((key, label))
        if (
            allow_description_update
            and replacement is None
            and "body" in item.payload
            and (entry.get("body") or "") != (item.payload["body"] or "")
        ):
            from app.services.native_voice_creation import spoken_description

            body = item.payload["body"]
            spoken = spoken_description(body)
            if spoken is None:
                return None
            changes.append(
                f"description to “{spoken}”" if spoken else "clear the description"
            )
            spoken_fields.append(("body", "Description"))
        update_field_types = []
        if fields:
            from app.contracts.information import schema_revision_from_profile_version
            from app.services.operational_model_runtime import (
                resolve_entry_type_spec,
                resolve_track_runtime_profile,
            )

            entry_type = await EntryType.get(entry.get("type_id"))
            track = await Track.get(location.track_id)
            if (
                entry_type is None
                or track is None
                or entry_type.track_id != location.track_id
            ):
                return None
            model, tier, _ = await resolve_track_runtime_profile(track)
            if schema_revision_from_profile_version(
                getattr(model, "version_number", None)
            ) != item.payload.get("expected_schema_revision"):
                return None
            declared = {
                field["key"]: field
                for field in resolve_entry_type_spec(entry_type, tier).get("fields", [])
                if isinstance(field, dict) and isinstance(field.get("key"), str)
            }
            for key, value in fields.items():
                spec = declared.get(key)
                if not spec or key.startswith("_"):
                    return None
                previous = (entry.get("custom_fields") or {}).get(key)
                before_text = _simple_field_text(spec, previous, previous=True)
                after_text = _simple_field_text(spec, value)
                if before_text is None or after_text is None:
                    return None
                if type(previous) is type(value) and previous == value:
                    continue
                label = spec.get("label") or spec.get("name") or key
                if not isinstance(label, str) or len(label) > 100:
                    return None
                changes.append(f"{label} from {before_text} to {after_text}")
                spoken_fields.append((f"fields.{key}", label))
                update_field_types.append((key, spec["type"]))
        if (
            not changes
            or not isinstance(entry.get("title"), str)
            or len(entry["title"]) > 200
        ):
            return None
        # Generate the spoken fields from the *actual* payload and current
        # record, never from the conversational model's paraphrase.
        if len(changes) == 1 and spoken_fields[0][0] == "title":
            text = f'rename “{entry["title"]}” to “{item.payload["title"]}”'
        else:
            text = f'update “{entry["title"]}” by changing ' + ", and ".join(changes)
        # Refuse unresolved identifiers in the spoken review.
        if len(text) > 1800 or re.search(
            r"\b[one]\.[A-Z][A-Za-z0-9_]*\.[A-Za-z0-9_-]+",
            text,
        ):
            return None
        # The human diff is canonical presentation, never execution input;
        # retain a private fingerprint of the actual unchanged proposal.
        encoded = json.dumps(
            {"proposal": proposal.model_dump(), "payload": item.payload},
            sort_keys=True,
            ensure_ascii=False,
            allow_nan=False,
        ).encode()
        return VoiceReviewReadout(
            text,
            work_id,
            run_id,
            proposal.proposal_id,
            proposal.target_id,
            hashlib.sha256(encoded).hexdigest(),
            tuple(spoken_fields),
            update_field_types=tuple(update_field_types),
            speech_action=spoken_review_action(
                text, entry, item.payload, spoken_fields
            ),
        )


@dataclass(frozen=True)
class VoiceAppliedReadout:
    text: str = field(repr=False)
    review: VoiceReviewReadout = field(repr=False)
    title: str = field(repr=False)
    track_id: str = field(repr=False)
    complete: bool = True
    brief: str = field(default="", repr=False)


async def read_applied_values(review, *, principal, workspace, expected_creation=None):
    """Read the actual saved fields following a consumed canonical decision."""
    if review.deletion:
        from app.services.native_voice_deletion import read_saved_deletion

        return await read_saved_deletion(review, principal, workspace)
    if review.presentation:
        from app.services.native_voice_presentation import read_saved_presentation

        return await read_saved_presentation(review, principal, workspace)
    if review.children:
        verified = set()
        for child in review.children:
            if child.reminder:
                from app.services.native_voice_reminder import read_saved_reminder

                saved = await read_saved_reminder(
                    child, principal, workspace, source_proposal=review.proposal_id
                )
            else:
                saved = await read_applied_values(
                    child, principal=principal, workspace=workspace
                )
            if saved is not None:
                verified.add("reminder" if child.reminder else "record")
        if not verified:
            return None
        if len(verified) == 1:
            text = (
                "The record was created. I couldn't verify that the reminder was scheduled."
                if "record" in verified
                else "The reminder was scheduled. I couldn't verify that the record was created."
            )
            return VoiceAppliedReadout(text, review, "", "", complete=False)
        return VoiceAppliedReadout(
            "The record was created and the reminder is scheduled in this chat.",
            review,
            "",
            "",
        )
    if review.reminder is not None:
        from app.services.native_voice_reminder import read_saved_reminder

        return await read_saved_reminder(review, principal, workspace)
    if review.creation:
        from dataclasses import replace

        from app.agentive.services.staging_execution import (
            load_staging_execution_result,
        )

        result = await load_staging_execution_result(
            token=review.proposal_id,
            principal_id=principal,
            workspace_id=workspace,
            kind="create_entry",
            payload=review.creation["payload"],
        )
        entry_id = (result or {}).get("entry_id")
        if not entry_id:
            return None
        actual = await read_applied_values(
            replace(review, target_id=entry_id, creation=None),
            principal=principal,
            workspace=workspace,
            expected_creation=review.creation,
        )
        if actual is None or actual.track_id != review.creation["track"]:
            return None
        return VoiceAppliedReadout(
            actual.text.replace("record edit", "new record"),
            review,
            actual.title,
            actual.track_id,
            brief=actual.brief,
        )
    from jvspatial.core.context import get_default_context

    from app.services.authority_facts import primary_node_record

    with isolated_permissions_cache():
        location = await locate_resource(
            "entry", review.target_id, allowed_app_states=DATA_APP_STATES
        )
        if location is None or location.workspace_id != workspace:
            return None
        decision = await evaluate(
            subject=Subject(kind="human", id=principal),
            action="entry.read",
            resource=Resource(
                kind="entry", id=review.target_id, scope=f"track:{location.track_id}"
            ),
        )
        if not decision.allowed:
            return None
        record = await primary_node_record(
            get_default_context().database, Entry, review.target_id
        )
        if not record:
            return None
        entry = record["context"]
        title = entry.get("title")
        if not isinstance(title, str) or len(title) > 200:
            return None
        values = []
        for key, label in review.fields:
            value = (
                (entry.get("custom_fields") or {}).get(key.removeprefix("fields."))
                if key.startswith("fields.")
                else entry.get(key)
            )
            if key == "body":
                from app.services.native_voice_creation import spoken_description

                spoken = spoken_description(value)
                if spoken is None:
                    return None
                values.append(f"{label}: {spoken or 'empty'}.")
                continue
            if value is not None and (
                type(value) not in (str, bool, int, float) or len(str(value)) > 200
            ):
                return None
            display = "empty" if value is None or value == "" else str(value)
            values.append(f"{label}: {display}.")
        text = (
            "Integral confirmed that the record edit was applied. Current saved values: "
            + " ".join(values)
        )
        if re.search(r"\b[one]\.[A-Z][A-Za-z0-9_]*\.[A-Za-z0-9_-]+", text):
            return None
        brief = ""
        if (
            expected_creation
            and title == expected_creation["title"]
            and (entry.get("body") or "") == expected_creation["body"]
            and all(
                (entry.get("custom_fields") or {}).get(k) == v
                for k, v in expected_creation["fields"].items()
            )
        ):
            brief = (
                f"I added “{title}” to {review.destination_label}."
                if review.destination_label
                else f"I added “{title}”."
            )
        return VoiceAppliedReadout(text, review, title, location.track_id, brief=brief)


async def revise_voice_review(review, field_number, value, *, principal, workspace):
    """Create an independently reviewed replacement through the existing form."""
    from app.agentive.services.entry_proposal_revision import (
        entry_proposal_form,
        revise_entry_proposal,
    )
    from app.schemas.agentive.staging import (
        EntryProposalDraft,
        ReviseEntryProposalRequest,
    )

    if isinstance(field_number, str):
        label = field_number.strip().casefold()
        matches = [
            index
            for index, (key, display) in enumerate(review.fields, start=1)
            if label in {display.casefold(), key.removeprefix("fields.").casefold()}
        ]
        if len(matches) != 1:
            return None
        field_number = matches[0]
    if type(field_number) is not int or not 1 <= field_number <= len(review.fields):
        return None
    key, _label = review.fields[field_number - 1]
    if not isinstance(value, str) or not 0 < len(value) <= 200:
        return None
    if key.startswith("fields."):
        field_key = key.removeprefix("fields.")
        update_kind = dict(review.update_field_types).get(field_key)
        if (
            review.creation and field_key in review.creation_text_keys
        ) or update_kind == "text":
            from app.services.native_voice_creation import is_spoken_note

            if not is_spoken_note(value):
                return None
        else:
            if (review.creation and field_key not in review.creation_date_keys) or (
                not review.creation and update_kind not in {None, "date"}
            ):
                return None
            try:
                if date.fromisoformat(value).isoformat() != value:
                    return None
            except ValueError:
                return None
    elif key != "title":
        return None
    form = await entry_proposal_form(
        token=review.proposal_id, user_id=principal, workspace_id=workspace
    )
    rows = form.get("entries") or []
    if len(rows) != 1 or rows[0]["operation_index"] != 0:
        return None
    row = rows[0]
    entry = row["entry"]
    if (not review.creation and entry.get("id") != review.target_id) or (
        review.creation and entry.get("track_id") != review.target_id
    ):
        return None
    draft = EntryProposalDraft(
        title=entry["title"],
        body=entry.get("body") or "",
        fields=dict(entry.get("custom_fields") or {}),
        tags=[tag["id"] for tag in entry.get("tags") or []],
    )
    if key == "title":
        draft.title = value
    else:
        draft.fields[key.removeprefix("fields.")] = value
    return await revise_entry_proposal(
        token=review.proposal_id,
        user_id=principal,
        workspace_id=workspace,
        request=ReviseEntryProposalRequest(
            operation_index=0, revision=row["revision"], draft=draft
        ),
        decision_source="voice",
    )


async def recover_revised_voice_review(
    *, principal, workspace, thread, target_id, target_kind="entry"
):
    """Recover one revised edit for the selected record or creation in its Track."""
    if target_kind not in {"entry", "track"}:
        return None
    creation = target_kind == "track"
    pending = [
        item
        for item in await list_pending_tokens(principal, thread)
        if item.user_id == principal
        and item.workspace_id == workspace
        and item.session_id == thread
        and item.kind == ("create_entry" if creation else "update_entry")
        and item.state == "pending"
        and not item.is_expired()
        and item.payload.get("track_id" if creation else "entry_id") == target_id
        and item.origin_proof is None
        and item.supersedes_token
        and "0" in item.entry_edit_fences
    ]
    if len(pending) != 1:
        return None
    item = pending[0]
    return await read_entry_review(
        principal=principal,
        workspace=workspace,
        thread=thread,
        work_id="",
        run_id="",
        replacement=(item.token, item.supersedes_token),
    )
