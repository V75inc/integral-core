"""Narrow revisions of approved partial scaffolds; never recreate successful work."""

from __future__ import annotations

import copy
from typing import Any, Dict


def repaired_operations(
    prior: Dict[str, Any], revised: Dict[str, Any], staged: Any
) -> list:
    """Only field values of not-yet-created seeds may change in a partial design."""
    before, after = copy.deepcopy(prior), copy.deepcopy(revised)
    old_seeds, new_seeds = before.pop("seeds", []), after.pop("seeds", [])
    if before != after or len(old_seeds) != len(new_seeds):
        raise ValueError(
            "A partial repair must retain the app, tracks, schema, views, routines and sample identities."
        )
    tracks = {row["id"]: row["name"] for row in prior.get("tracks", [])}
    operations = copy.deepcopy(staged.payload.get("operations") or [])
    completed = (staged.progress or {}).get("completed")
    if type(completed) is not int or not 0 < completed < len(operations):
        raise ValueError("The partial batch has no valid successful cursor.")
    for old, new in zip(old_seeds, new_seeds):
        old_identity, new_identity = dict(old), dict(new)
        old_fields = old_identity.pop("fields", {})
        new_fields = new_identity.pop("fields", {})
        if old_identity != new_identity:
            raise ValueError(
                "A partial repair cannot add, remove, rename or move samples."
            )
        if old_fields == new_fields:
            continue
        matches = [
            i
            for i, op in enumerate(operations)
            if op.get("kind") == "create_entry"
            and op.get("payload", {}).get("title") == old["title"]
            and op.get("payload", {}).get("track_id")
            == f"{{{{track.id:{tracks[old['track']]}}}}}"
        ]
        if len(matches) != 1 or matches[0] < completed:
            raise ValueError(
                "Only unapplied samples with an unambiguous original operation can change."
            )
        operation = operations[matches[0]]
        fields = copy.deepcopy(new_fields)
        track = next(row for row in revised["tracks"] if row["id"] == new["track"])
        relation_keys = {
            field["key"]
            for entry_type in track.get("entry_types", [])
            for field in entry_type.get("fields", [])
            if field.get("type") == "relation"
        }
        titles = {seed["title"].strip().casefold(): seed["title"] for seed in new_seeds}
        for key, value in fields.items():
            if (
                key in relation_keys
                and isinstance(value, str)
                and value.strip().casefold() in titles
            ):
                fields[key] = f"{{{{entry.id:{titles[value.strip().casefold()]}}}}}"
        operation["payload"]["fields"] = fields
        operation.setdefault("diff_machine", {})["fields"] = copy.deepcopy(fields)
    return operations


async def validate_partial_revision(
    marker: dict, revised: dict, *, user_id: str, session_id: str, workspace_id: str
) -> None:
    """Reject changed scope, busy cursors and revisions that would repeat work."""
    from app.agentive.staging import get_token

    partial = marker.get("partial_build") or {}
    staged = await get_token(str(partial.get("batch_token") or ""))
    if (
        staged is None
        or staged.state != "blessed"
        or staged.is_expired()
        or staged.executing
        or staged.kind != "batch"
        or staged.user_id != user_id
        or staged.session_id != session_id
        or staged.workspace_id != workspace_id
    ):
        raise ValueError(
            "The original partial batch is unavailable, expired or busy. Inspect the existing app; do not recreate it."
        )
    repaired_operations(
        partial.get("blueprint") or marker.get("blueprint") or {}, revised, staged
    )


async def apply_partial_revision(
    marker: dict, staged: Any, *, user_id: str, session_id: str, workspace_id: str
) -> Any:
    """Bind the newly approved pending fields to an atomic replacement batch."""
    from app.agentive.staging import replace_partial_batch
    from app.services.chat_threads import get_thread_by_session

    partial = marker.get("partial_build") or {}
    prior = partial.get("blueprint")
    if not prior or prior == marker.get("blueprint"):
        return staged
    operations = repaired_operations(prior, marker["blueprint"], staged)

    async def bind_revision(token: str) -> None:
        thread = await get_thread_by_session(session_id)
        if (
            thread is None
            or thread.user_id != user_id
            or thread.workspace_id != workspace_id
            or thread.design_proposed != marker
            or not marker.get("approved")
        ):
            raise ValueError("The approved repair changed; reload before executing.")
        updated = copy.deepcopy(marker)
        updated["partial_build"].update(
            batch_token=token, blueprint=copy.deepcopy(marker["blueprint"])
        )
        thread.design_proposed = updated
        await thread.save()

    return await replace_partial_batch(
        original=staged,
        operations=operations,
        user_id=user_id,
        session_id=session_id,
        workspace_id=workspace_id,
        bind_revision=bind_revision,
    )
