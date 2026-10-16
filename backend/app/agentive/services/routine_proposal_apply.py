"""Apply only the exact broker proposal authorized by a live routine mandate."""

from __future__ import annotations

from typing import Any


async def apply_routine_proposal(inv: Any, data: Any, proof: dict[str, Any]) -> Any:
    """Keep human/foreign/unsupported cards pending; never scan a session queue."""
    if not isinstance(data, dict) or data.get("_kind") != "staged_change":
        return data
    from jvspatial.core.context import get_default_context

    from app.agentive.nodes import RoutineTask
    from app.agentive.services.staging_apply import bless_and_execute
    from app.agentive.services.staging_command_apply import CANONICAL_COMMAND_KINDS
    from app.agentive.services.work_execution import assert_effect_boundary_allowed
    from app.agentive.services.work_items import authorized_work_item_effect
    from app.agentive.staging_executors import _DELETE_KINDS
    from app.agentive.staging_store import StagedChangeRecord
    from app.models.nodes import ChatThread

    ctx = inv.work_execution_context
    async with authorized_work_item_effect(ctx):
        item = await assert_effect_boundary_allowed(ctx)
        if item.kind != "routine_turn":
            return data
        database = get_default_context().database
        rows = await database.find(
            "object",
            {
                "entity": StagedChangeRecord._entity_name(),
                "context.token": data.get("token"),
            },
            limit=2,
        )
        if len(rows) != 1:
            return data
        row = await database.find_one_and_update(
            "object", {"id": rows[0]["id"]}, {"$set": {}}
        )
        fields = (row or {}).get("context") or {}
        if (
            fields.get("origin_proof") != proof
            or fields.get("user_id") != inv.principal_id
            or fields.get("workspace_id") != inv.workspace_id
            or fields.get("session_id") != inv.session_id
            or fields.get("state") != "pending"
        ):
            return data
        kind = fields.get("kind")
        if kind in _DELETE_KINDS or kind not in CANONICAL_COMMAND_KINDS:
            return data  # Unsupported effect families retain explicit review.
        task = await RoutineTask.find_one({"id": item.input_payload["routine_id"]})
        thread = await ChatThread.find_one({"id": ctx.thread_id})
        if (
            task is None
            or thread is None
            or fields.get("session_id")
            not in {
                thread.id,
                thread.provider_session_id,
                thread.active_harness_session_id,
            }
        ):
            return data
        payload = fields.get("payload") or {}
        # Select the authority target from the command contract, never from
        # arbitrary extra payload keys. Unknown commands require human review.
        target_kind = {
            "create_entry": "track",
            "update_entry": "entry",
            "update_track": "track",
            "save_view": "track",
            "add_entry_tag": "entry",
            "remove_entry_tag": "entry",
        }.get(kind)
        if target_kind is None:
            return data
        target_id = payload.get(f"{target_kind}_id")
        if not target_id or not any(
            grant.get("resource_type") == target_kind
            and grant.get("resource_id") == target_id
            for grant in task.write_scope
        ):
            return data
        # Outbound save hooks need a separately qualified dispatch contract.
        from app.services.hooks.registry import get_workspace_hooks

        points = {
            "create_entry": ("entry.create", "entry.validate"),
            "update_entry": ("entry.update", "entry.validate", "entry.transform"),
        }.get(kind, ())
        if any(
            binding.get("mode") == "tool"
            for point in points
            for binding in get_workspace_hooks(inv.workspace_id, point)
        ):
            return data
        applied = await bless_and_execute(
            user_id=inv.principal_id,
            token=str(data["token"]),
            decision_source="routine_mandate",
        )
        observed = dict(data)
        observed.update(
            state=applied["staged_change"]["state"],
            consumed=bool(applied.get("consumed")),
            execute_result=applied.get("execute_result"),
            decision_source="routine_mandate",
        )
        return observed


async def recover_routine_proposal(inv: Any, token: str) -> Any:
    """Observe a committed exact proposal under current authority; never restage."""
    import json

    from jvspatial.core.context import get_default_context

    from app.agentive.services.proposal_origin import origin_for
    from app.agentive.services.staging_command_apply import _readback
    from app.agentive.services.staging_execution import (
        execution_record_id,
        load_staging_execution_result,
    )
    from app.agentive.services.work_execution import assert_effect_boundary_allowed
    from app.agentive.staging_store import StagedChangeRecord, _record_to_sc
    from app.schemas.agentive.work import WorkError
    from app.services.credential_crypto import decrypt_secret_from_storage

    ctx = inv.work_execution_context
    item = await assert_effect_boundary_allowed(ctx)
    if item.kind != "routine_turn":
        return None
    oid = execution_record_id(token)
    row = await get_default_context().database.get("object", oid)
    fields = (row or {}).get("context") or {}
    if (
        fields.get("principal_id") != inv.principal_id
        or fields.get("workspace_id") != inv.workspace_id
    ):
        raise WorkError(
            "work.non_replayable_effect",
            "Proposal receipt ownership requires reconciliation",
        )
    try:
        proof = json.loads(
            decrypt_secret_from_storage(
                fields["origin_ciphertext"], aad=oid + ":proposal-origin"
            )
        )
        snapshot = json.loads(
            decrypt_secret_from_storage(
                fields["proposal_ciphertext"], aad=oid + ":proposal"
            )
        )
    except Exception as exc:
        raise WorkError(
            "work.non_replayable_effect",
            "Original proposal proof requires reconciliation",
        ) from exc
    expected = origin_for(
        ctx,
        inv.capability_key,
        str(item.input_payload.get("routine_mandate_fingerprint") or ""),
    )
    if proof != expected or snapshot.get("origin_proof") != expected:
        raise WorkError(
            "work.non_replayable_effect", "Original proposal origin changed"
        )
    sc = _record_to_sc(StagedChangeRecord(**snapshot))
    if (
        sc is None
        or sc.token != token
        or sc.user_id != inv.principal_id
        or sc.workspace_id != inv.workspace_id
    ):
        raise WorkError(
            "work.non_replayable_effect", "Original proposal identity changed"
        )
    evidence = await load_staging_execution_result(
        token=token,
        principal_id=sc.user_id,
        workspace_id=inv.workspace_id,
        kind=sc.kind,
        payload=sc.payload,
    )
    if evidence is None:
        raise WorkError(
            "work.non_replayable_effect",
            "Original proposal effect requires reconciliation",
        )
    observed = await _readback(sc, inv.workspace_id, evidence)
    return {
        # The saved proposal may describe records no longer visible. Only
        # freshly authorized readback belongs in a recovered public result.
        "_kind": "staged_change",
        "token": token,
        "kind": sc.kind,
        "state": "consumed",
        "consumed": True,
        "execute_result": observed,
        "decision_source": "routine_mandate",
    }
