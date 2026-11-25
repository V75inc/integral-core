"""Read the canonical, current review for one exact native voice task.

This is a read-only projection. Speech-model output is never a human decision.
"""

import hashlib
import json
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


@dataclass(frozen=True)
class VoiceReviewReadout:
    text: str = field(repr=False)
    work_id: str = field(repr=False)
    run_id: str = field(repr=False)
    proposal_id: str = field(repr=False)
    target_id: str = field(repr=False)
    fingerprint: str = field(repr=False)
    fields: tuple[tuple[str, str], ...] = field(default=(), repr=False)
    creation: dict | None = field(default=None, repr=False)
    creation_date_keys: tuple[str, ...] = field(default=(), repr=False)

    def matches_focus(self, focus):
        return focus is None or (
            focus.kind == ("track" if self.creation else "entry")
            and focus.id == self.target_id
        )


async def read_entry_review(
    *, principal, workspace, thread, work_id, run_id, replacement=None
):
    """Return one readable, bounded, exact pending record edit, or no projection."""
    with isolated_permissions_cache():
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
                or proof.get("capability_key")
                != {
                    "update_entry": "integral_update_entry",
                    "create_entry": "integral_create_entry",
                }.get(item.kind)
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
        item = pending[0]
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
            changes.append(f"{label}: from {before or 'empty'} to {after}.")
            spoken_fields.append((key, label))
        if fields:
            entry_type = await EntryType.get(entry.get("type_id"))
            if entry_type is None or entry_type.track_id != location.track_id:
                return None
            declared = {
                field["key"]: field
                for field in (entry_type.form_schema or {}).get("fields", [])
                if isinstance(field, dict) and isinstance(field.get("key"), str)
            }
            if not isinstance(fields, dict) or any(
                (declared.get(key) or {}).get("type") != "date"
                or not isinstance(value, str)
                or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", value)
                or (
                    (entry.get("custom_fields") or {}).get(key) is not None
                    and (
                        not isinstance(entry["custom_fields"][key], str)
                        or not re.fullmatch(
                            r"\d{4}-\d{2}-\d{2}", entry["custom_fields"][key]
                        )
                    )
                )
                for key, value in fields.items()
            ):
                return None
            for key, value in fields.items():
                previous = (entry.get("custom_fields") or {}).get(key)
                try:
                    date.fromisoformat(value)
                    if previous is not None:
                        date.fromisoformat(previous)
                except ValueError:
                    return None
                label = declared[key].get("label") or declared[key].get("name") or key
                if not isinstance(label, str) or len(label) > 100:
                    return None
                changes.append(f"{label}: from {previous or 'empty'} to {value}.")
                spoken_fields.append((f"fields.{key}", label))
            from app.contracts.information import schema_revision_from_profile_version
            from app.services.operational_model_runtime import (
                resolve_track_runtime_profile,
            )

            track = await Track.get(location.track_id)
            if track is None:
                return None
            model, _, _ = await resolve_track_runtime_profile(track)
            if schema_revision_from_profile_version(
                getattr(model, "version_number", None)
            ) != item.payload.get("expected_schema_revision"):
                return None
        if (
            not changes
            or not isinstance(entry.get("title"), str)
            or len(entry["title"]) > 200
        ):
            return None
        # Generate the spoken fields from the *actual* payload and current
        # record, never from the conversational model's paraphrase.
        text = (
            "Update record "
            + entry["title"]
            + ". "
            + " ".join(changes)
            + " This has not been applied."
        )
        # Refuse unresolved identifiers in the spoken review.
        if re.search(
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
        )


@dataclass(frozen=True)
class VoiceAppliedReadout:
    text: str = field(repr=False)
    review: VoiceReviewReadout = field(repr=False)
    title: str = field(repr=False)
    track_id: str = field(repr=False)


async def read_applied_values(review, *, principal, workspace):
    """Read the actual saved fields following a consumed canonical decision."""
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
        )
        if actual is None or actual.track_id != review.creation["track"]:
            return None
        return VoiceAppliedReadout(
            actual.text.replace("record edit", "new record"),
            review,
            actual.title,
            actual.track_id,
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
        return VoiceAppliedReadout(text, review, title, location.track_id)


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
        if (
            review.creation
            and key.removeprefix("fields.") not in review.creation_date_keys
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
