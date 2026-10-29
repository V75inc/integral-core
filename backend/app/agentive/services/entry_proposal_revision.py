"""Human edits create a fresh review through Integral's staging boundary.

This is substrate integration, not an alternate agent loop. The existing
stagers, canonical Entry writers, graph transaction and decision fence remain
authoritative. A revision never inherits an agent capability's origin proof.
"""

from __future__ import annotations

import copy
import hashlib
import json
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from typing import Any

from app.agentive import staging, staging_store
from app.agentive.staging import StagedChange, StagingError
from app.schemas.agentive.staging import ReviseEntryProposalRequest
from app.services.app_operations.transaction_scope import (
    graph_transaction_available,
    postgres_graph_transaction,
)


def _digest(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(
            value, sort_keys=True, separators=(",", ":"), allow_nan=False
        ).encode()
    ).hexdigest()


def editable_operations(sc: StagedChange) -> list[tuple[int, dict[str, Any]]]:
    """Only canonical Entry writes with an already existing, fixed destination."""
    if sc.kind == "batch":
        from app.agentive.services.staging_command_batch import supports_canonical_batch

        if not supports_canonical_batch(sc):
            return []
        operations = sc.payload.get("operations") or []
    else:
        operations = [{"kind": sc.kind, "payload": sc.payload}]
    return [
        (index, op)
        for index, op in enumerate(operations)
        if (
            op.get("kind") in {"create_entry", "update_entry"}
            or (
                sc.kind == "file_content"
                and set(sc.payload)
                <= {
                    "track_id",
                    "title",
                    "body",
                    "entry_type",
                    "fields",
                    "tags",
                    "strict_fields",
                    "idempotency_key",
                }
            )
        )
        and isinstance(op.get("payload"), dict)
        and str(
            op["payload"].get("track_id")
            if op["kind"] in {"create_entry", "file_content"}
            else op["payload"].get("entry_id") or ""
        ).startswith("n.")
    ]


def _admit(sc: StagedChange | None, user_id: str, workspace_id: str) -> StagedChange:
    if sc is None or sc.user_id != user_id or sc.workspace_id != workspace_id:
        raise StagingError(
            "proposal_unavailable",
            "This proposed change isn't available in the current workspace.",
        )
    if sc.state != "pending" or sc.is_expired() or sc.executing or sc.progress:
        raise StagingError(
            "proposal_changed",
            "This change is no longer awaiting review. Reload the conversation.",
        )
    return sc


async def _entry_context(sc: StagedChange, index: int, op: dict) -> dict:
    """Authorize before reading private data, then seed the full standard form."""
    from app.agentive.staging_executors import _resolve_entry_type_id
    from app.agentive.tooling import bindings
    from app.api.utils import export_node
    from app.models.nodes import Entry, EntryType, Tag, Track
    from app.schemas.policy import Resource, Subject
    from app.services.operational_model_entry_fields import resolve_entry_type_spec
    from app.services.operational_model_runtime import resolve_track_runtime_profile
    from app.services.policy_engine import evaluate

    payload = op["payload"]
    current = None
    if op["kind"] == "update_entry":
        try:
            await bindings._authorize_entry_stage(payload["entry_id"], "entry.update")
        except PermissionError as exc:
            raise StagingError(
                "access_denied", "You can no longer edit this record."
            ) from exc
        current = await Entry.find_one({"id": payload["entry_id"]})
        if current is None:
            raise StagingError("proposal_changed", "The record is no longer available.")
        if payload.get("expected_record_revision") != current.record_revision:
            raise StagingError(
                "proposal_changed",
                "This record changed since the proposal was prepared. Ask for a fresh proposal.",
            )
        track_id = current.track_id
    else:
        track_id = payload["track_id"]
        decision = await evaluate(
            subject=Subject(kind="human", id=sc.user_id),
            action="entry.create",
            resource=Resource(kind="entry", id="", scope=f"track:{track_id}"),
        )
        if not decision.allowed:
            raise StagingError(
                "access_denied", "You can no longer add records to this track."
            )
    track = await Track.find_one({"id": track_id})
    if track is None or track.workspace_id != sc.workspace_id:
        raise StagingError(
            "proposal_unavailable",
            "The destination is no longer available in this workspace.",
        )
    type_id = await _resolve_entry_type_id(
        track_id,
        str(payload.get("entry_type") or (current.type_id if current else "")),
        exact=True,
    )
    if not type_id:
        from app.services.view_create_resolution import load_entry_types_for_track

        types = await load_entry_types_for_track(track)
        if not current and not payload.get("entry_type") and len(types) == 1:
            type_id = types[0].id
    entry_type = await EntryType.find_one({"id": type_id}) if type_id else None
    if entry_type is None:
        raise StagingError(
            "schema_required",
            "A current record type is required to edit this proposal.",
        )
    _, tier, _ = await resolve_track_runtime_profile(track)
    spec = resolve_entry_type_spec(entry_type, tier)
    from app.services.query_boundary import parent_app_for_track

    parent = await parent_app_for_track(track)
    tag_scope = [{"context.track_id": track_id}]
    if parent:
        tag_scope.append({"context.app_id": parent.id})
    tags = await Tag.find({"$or": tag_scope})
    by_name = {str(t.name or "").casefold(): t.id for t in tags}
    allowed_ids = {t.id for t in tags}
    current_tags = []
    if current:
        from app.models.edges import TAGGED_WITH

        current_tags = [
            t.id
            for t in await current.nodes(edge=[TAGGED_WITH], node=["Tag"], limit=501)
        ]
    tag_ids = []
    for tag in payload.get("tags", current_tags) or []:
        resolved = tag if tag in allowed_ids else by_name.get(str(tag).casefold())
        if not resolved:
            raise StagingError(
                "proposal_changed",
                "A proposed tag is no longer available. Prepare a fresh proposal.",
            )
        tag_ids.append(resolved)
    before = await export_node(current) if current else {}
    fields = {**(before.get("custom_fields") or {}), **(payload.get("fields") or {})}
    seed = {
        **before,
        "id": current.id if current else "",
        "track_id": track_id,
        "type_id": entry_type.id,
        "type": entry_type.name,
        "title": payload.get("title", before.get("title", "")),
        "body": payload.get("body", before.get("body", "")) or "",
        "custom_fields": fields,
        "tags": [{"id": tag_id} for tag_id in tag_ids],
    }
    fence = {"track_id": track_id, "type_id": entry_type.id, "schema": _digest(spec)}
    return {
        "operation_index": index,
        "track": await export_node(track),
        "entry": seed,
        "revision": _digest([sc.token, payload, spec, before]),
        "fence": fence,
        "_fields": spec.get("fields") or [],
    }


async def _contexts(sc: StagedChange) -> list[dict]:
    from app.agentive.tooling import bindings
    from app.services.agent_scope import current_scope_workspace_id

    scope = current_scope_workspace_id.set(sc.workspace_id)
    principal = bindings._propose_principal.set(sc.user_id)
    try:
        return [
            await _entry_context(sc, index, op) for index, op in editable_operations(sc)
        ]
    finally:
        bindings._propose_principal.reset(principal)
        current_scope_workspace_id.reset(scope)


async def entry_proposal_form(*, token: str, user_id: str, workspace_id: str) -> dict:
    """Return the current authorized draft and an optimistic read stamp."""
    if not graph_transaction_available():
        raise StagingError(
            "transaction_required",
            "Editing proposals requires transactional storage. Reject this proposal and request a corrected one instead.",
        )
    sc = _admit(await staging_store.load_current(token), user_id, workspace_id)
    return {
        "ok": True,
        "entries": [
            {k: v for k, v in row.items() if k != "fence" and not k.startswith("_")}
            for row in await _contexts(sc)
        ],
    }


async def _replace_prompt(database: Any, old: StagedChange, new: StagedChange) -> None:
    """Replace the queue and append its human-edit message in the graph commit."""
    if not old.session_id:
        return
    from app.models.nodes import ChatThread
    from app.services.chat_threads import append_message
    from app.services.prompt_queue import empty_queue, get_queue

    query: dict[str, Any] = {"entity": ChatThread._entity_name()}
    # Match the canonical chat_threads.get_thread_by_session contract: native
    # proposals use the durable thread ID, never the rotating model session.
    if old.session_id.startswith("n.ChatThread."):
        query.update(id=old.session_id, **{"context.provider_id": "integral_native"})
    else:
        query["context.provider_session_id"] = old.session_id
    rows = await database.find("node", query, limit=2)
    if len(rows) != 1:
        raise StagingError(
            "proposal_unavailable", "The original conversation is unavailable."
        )
    row = await database.find_one_and_update(
        "node", {"id": rows[0]["id"]}, {"$set": {}}
    )
    data = (row or {}).get("context") or {}
    if (
        data.get("user_id") != old.user_id
        or data.get("workspace_id") != old.workspace_id
    ):
        raise StagingError(
            "proposal_unavailable", "The original conversation is unavailable."
        )
    # Load through the public entity API in the transaction-local identity map.
    # The locked row cannot change underneath this load until our unit finishes.
    thread = await ChatThread.get(row["id"])
    if thread is None:
        raise StagingError(
            "proposal_unavailable", "The original conversation is unavailable."
        )
    queue = copy.deepcopy(get_queue(thread))
    matching = [
        item
        for item in queue["items"]
        if item.get("token") == old.token and item.get("kind") == "staged_write"
    ]
    if len(matching) > 1 or matching and matching[0].get("status") != "pending":
        raise StagingError(
            "proposal_changed", "The review changed. Reload the conversation."
        )
    if not matching:
        if queue["status"] != "open":
            queue = empty_queue()
        item: dict[str, Any] = {}
        queue["items"].append(item)
    else:
        item = matching[0]
    now = datetime.now(timezone.utc).isoformat()
    item.update(
        id=new.token,
        kind="staged_write",
        token=new.token,
        status="pending",
        created_at=now,
        resolved_at=None,
        write_kind=new.kind,
        summary=new.summary,
        diff_human=new.diff_human,
        diff_machine=new.diff_machine,
        staged_state="pending",
        expires_at=new.expires_at.isoformat(),
        supersedes_token=old.token,
        can_edit_entries=bool(editable_operations(new)),
    )
    item.pop("last_error", None)
    queue.update(status="open", closed_at=None, close_reason=None)
    thread.prompt_queue = queue
    await thread.save()
    await append_message(
        thread=thread,
        role="assistant",
        parts=[
            {
                "type": "text",
                "text": "Your edited change is ready for review. It has not been applied.",
            },
            {
                "type": "tool-call",
                "toolCallId": new.token,
                "toolName": "human_revise_entry_proposal",
                "args": {},
                "result": new.to_dict(),
            },
        ],
        provider_metadata={
            "source": "human_proposal_revision",
            "supersedes_token": old.token,
            "interactPayload": {"origin": "human_proposal_revision"},
        },
    )


async def revise_entry_proposal(
    *, token: str, user_id: str, workspace_id: str, request: ReviseEntryProposalRequest
) -> StagedChange:
    """One winner against approval/revision; publish no executable cache on rollback."""
    from app.agentive.services import work_approvals
    from app.agentive.services.staging_decision_store import _write_locked_decision
    from app.agentive.services.staging_execution import execution_record_id
    from app.agentive.tooling import bindings
    from app.services.agent_scope import current_scope_workspace_id

    if not graph_transaction_available():
        raise StagingError(
            "transaction_required", "Editing proposals requires transactional storage."
        )
    async with postgres_graph_transaction() as database:
        rows = await database.find(
            "object",
            {
                "entity": staging_store.StagedChangeRecord._entity_name(),
                "context.token": token,
            },
            limit=2,
        )
        if len(rows) != 1:
            raise StagingError(
                "proposal_unavailable", "This proposal is no longer available."
            )
        row = await database.find_one_and_update(
            "object", {"id": rows[0]["id"]}, {"$set": {}}
        )
        data = (row or {}).get("context") or {}
        sc = _admit(
            (
                staging_store._record_to_sc(
                    staging_store.StagedChangeRecord(id=row["id"], **data)
                )
                if row
                else None
            ),
            user_id,
            workspace_id,
        )
        if await database.get("object", execution_record_id(token)) is not None:
            raise StagingError(
                "proposal_changed",
                "Execution has started; this proposal cannot be edited.",
            )
        contexts = await _contexts(sc)
        context = next(
            (
                item
                for item in contexts
                if item["operation_index"] == request.operation_index
            ),
            None,
        )
        if context is None or context["revision"] != request.revision:
            raise StagingError(
                "proposal_changed",
                "The record or its schema changed. Reload the form before editing.",
            )
        original_op = dict(editable_operations(sc))[request.operation_index]
        payload = copy.deepcopy(original_op["payload"])
        payload.update(request.draft.model_dump())
        seed_fields = context["entry"]["custom_fields"]
        declared = {
            str(field.get("key")): field
            for field in context["_fields"]
            if isinstance(field, dict) and field.get("key")
        }
        for key in set(seed_fields) | set(payload["fields"]):
            value = payload["fields"].get(key)
            spec = declared.get(key)
            if spec is None and key not in seed_fields:
                raise StagingError(
                    "invalid_entry_draft",
                    f"Field '{key}' is not available in this form.",
                )
            composite = (spec or {}).get("composite") or {}
            primitive = composite.get("base") if isinstance(composite, dict) else None
            locked = (
                key.startswith("_")
                or bool((spec or {}).get("readonly"))
                or (primitive or (spec or {}).get("type"))
                in {"file", "files", "computed"}
            )
            if locked and key in payload["fields"] and value != seed_fields.get(key):
                raise StagingError(
                    "invalid_entry_draft",
                    f"Field '{key}' must retain its proposed value. Add or change files after applying the record.",
                )
            if locked and key in seed_fields:
                # Omitting a locked field cannot silently remove its binding
                # from a create proposal's otherwise complete form payload.
                payload["fields"][key] = copy.deepcopy(seed_fields[key])
        # Content edits cannot retarget the proposal or switch its record type.
        if (
            original_op["kind"] in {"create_entry", "file_content"}
            or "entry_type" in payload
        ):
            payload["entry_type"] = context["fence"]["type_id"]
        scope = current_scope_workspace_id.set(workspace_id)
        principal = bindings._propose_principal.set(user_id)
        try:
            stager = (
                bindings._stage_create_entry
                if original_op["kind"] in {"create_entry", "file_content"}
                else bindings._stage_update_entry
            )
            try:
                operation = await stager(payload)
            except ValueError as exc:
                raise StagingError("invalid_entry_draft", str(exc)) from exc
        finally:
            bindings._propose_principal.reset(principal)
            current_scope_workspace_id.reset(scope)
        operation["diff_human"] = operation["diff_human"].replace(
            f"- **Type:** {context['fence']['type_id']}",
            f"- **Type:** {context['entry']['type']}",
        )
        # Keep execution metadata and attachment bindings; only form content is revised.
        preserved = {
            key: value
            for key, value in original_op["payload"].items()
            if key
            not in {"title", "body", "fields", "tags", "entry_type", "idempotency_key"}
        }
        operation["payload"] = {**operation["payload"], **preserved}
        operation["diff_machine"] = {"op": operation["kind"], **operation["payload"]}
        now = datetime.now(timezone.utc)
        if sc.is_expired(now=now):
            raise StagingError(
                "proposal_changed",
                "This proposal expired while the form was open. Request a fresh proposal.",
            )
        new = replace(
            sc,
            token=staging._new_token(),
            created_at=now,
            expires_at=now + timedelta(seconds=staging._DEFAULT_TTL_SECONDS),
            state="pending",
            autonomy_grant_used=False,
            blessed_at=None,
            resolved_at=None,
            last_error=None,
            progress=None,
            executing=False,
            execute_started_at=None,
            origin_proof=None,
            interaction_id=None,
            decision_source=None,
            idempotency_key=None,
            supersedes_token=sc.token,
            superseded_by=None,
            entry_edit_fences=copy.deepcopy(sc.entry_edit_fences),
        )
        new.entry_edit_fences[str(request.operation_index)] = context["fence"]
        if sc.kind == "batch":
            new.payload = copy.deepcopy(sc.payload)
            new.payload.pop("idempotency_key", None)
            new.payload["operations"][request.operation_index] = operation
            new.diff_machine = {
                "op": "batch",
                "operations": copy.deepcopy(new.payload["operations"]),
            }
            new.diff_human = "\n\n".join(
                op.get("diff_human") or op.get("summary") or ""
                for op in new.payload["operations"]
            )
        else:
            new.kind = operation["kind"]
            new.payload = operation["payload"]
            new.summary, new.diff_human, new.diff_machine = (
                operation["summary"],
                operation["diff_human"],
                operation["diff_machine"],
            )
        approvals = await database.find(
            "object",
            {
                "entity": work_approvals.WorkApproval._entity_name(),
                "context.staging_token": token,
            },
            limit=2,
        )
        if (
            len(approvals) > 1
            or approvals
            and (approvals[0].get("context") or {}).get("status") != "pending"
        ):
            raise StagingError(
                "proposal_changed", "The original review has already been decided."
            )
        if approvals:
            work = await database.get(
                "object", f"o.WorkItem.{approvals[0]['context']['work_item_id']}"
            )
            facts = (work or {}).get("context") or {}
            from app.services.identity_admission import resolve_current_profile

            work_principal = await resolve_current_profile(
                str(facts.get("principal_id") or "")
            )
            owner = await resolve_current_profile(user_id)
            if (
                work is None
                or work.get("entity") != work_approvals.WorkItem._entity_name()
                or facts.get("workspace_id") != workspace_id
                or work_principal is None
                or owner is None
                or work_principal.id != owner.id
            ):
                raise StagingError(
                    "proposal_unavailable",
                    "The linked review's scope could not be verified.",
                )
            # Public jvspatial graph_transaction isolates the entity identity
            # map and evicts touched parent-cache keys at commit/rollback.
            # Keep the normal approval cache refresh inside that context.
            await work_approvals.decide_work_approval_unit(
                work_approval_id=approvals[0]["context"]["work_approval_id"],
                decision="rejected",
                decider_id=user_id,
                reason="Superseded by the user's edited proposal",
                transaction=database,
            )
        closed = await _write_locked_decision(
            database,
            row=row,
            data=data,
            updates={
                "state": "revoked",
                "resolved_at": now.isoformat(),
                "decision_source": "card",
                "superseded_by": new.token,
            },
            expires=sc.expires_at,
        )
        await staging_store.StagedChangeRecord.create(
            **staging_store._sc_to_fields(new)
        )
        await _replace_prompt(database, sc, new)
    await staging._cache_durable_decision(closed)
    await staging._cache_durable_decision(new)
    await staging._push_state_and_record_closure(closed)
    await staging._push_created_event(new)
    return new


async def validate_entry_edit_fence(decision: dict, index: int) -> None:
    """Inside the canonical effect transaction, refuse schema changes since review."""
    fence = (decision.get("entry_edit_fences") or {}).get(str(index))
    if not fence:
        return
    from app.models.nodes import EntryType, Track
    from app.services.operational_model_entry_fields import resolve_entry_type_spec
    from app.services.operational_model_runtime import resolve_track_runtime_profile

    track = await Track.find_one({"id": fence["track_id"]})
    entry_type = await EntryType.find_one({"id": fence["type_id"]})
    if track is None or entry_type is None:
        raise StagingError(
            "proposal_changed", "The reviewed schema is no longer available."
        )
    _, tier, _ = await resolve_track_runtime_profile(track)
    if _digest(resolve_entry_type_spec(entry_type, tier)) != fence["schema"]:
        raise StagingError(
            "proposal_changed",
            "The record schema changed after your edit. Prepare a fresh proposal.",
        )
