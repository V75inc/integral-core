"""Apply a canonical command once and recover by current record readback."""

from __future__ import annotations

from typing import Any, Awaitable, Callable

from app.agentive.services.staging_execution import (
    BatchStepApproval,
    bind_staging_command_execution,
    claim_staging_execution,
    execution_record_id,
    finish_staging_external_dispatch,
    load_staging_execution_result,
    mark_staging_external_dispatch,
    release_unapplied_staging_claim,
)
from app.agentive.staging import StagedChange, StagingError


async def _identity(sc: StagedChange, request: Any) -> dict[str, Any]:
    from app.agentive.staging_executors import resolve_executor_workspace_id

    workspace_id = sc.workspace_id or await resolve_executor_workspace_id(
        request, sc.user_id
    )
    if not workspace_id:
        raise StagingError(
            "execution_scope_required", "An execution workspace is required"
        )
    return {
        "token": sc.token,
        "principal_id": sc.user_id,
        "workspace_id": workspace_id,
        "kind": sc.kind,
        "payload": sc.payload,
    }


async def has_applied_command_receipt(sc: StagedChange, request: Any) -> bool:
    """A completion marker can be reconciled only with exact durable evidence."""
    return (
        await load_staging_execution_result(**await _identity(sc, request)) is not None
    )


async def _readback_comment(
    sc: StagedChange, workspace_id: str, evidence: dict[str, Any]
) -> dict[str, Any]:
    from jvspatial.core.context import get_default_context

    from app.agentive.staging_executors import _call_endpoint
    from app.api.entries import get_entry
    from app.api.errors import InsufficientPermissionsError, ResourceNotFoundError
    from app.api.utils import attach_author_exports, export_node
    from app.models.nodes import Comment
    from app.services.agent_scope import current_scope_workspace_id
    from app.services.authority_facts import primary_node_record
    from app.services.comment_creation import comment_parent_entry
    from app.services.release_admission import release_admission
    from app.services.resource_location import DATA_APP_STATES, locate_resource

    entry_id, comment_id = evidence.get("entry_id", ""), evidence.get("comment_id", "")
    result: dict[str, Any] = {
        "ok": True,
        "applied": True,
        "result_access": "withheld",
        "message": "This approved comment already applied. Current content is unavailable.",
    }
    if evidence.get("deleted"):
        result["deleted"] = True
        result["message"] = "This approved comment deletion already applied."
    location = await locate_resource(
        "entry", entry_id, allowed_app_states=DATA_APP_STATES
    )
    if (
        not evidence.get("deleted")
        and location is not None
        and location.workspace_id == workspace_id
    ):
        reset = current_scope_workspace_id.set(workspace_id)
        try:
            async with release_admission(sc.user_id, workspace_id):
                observed = await _call_endpoint(
                    get_entry, sc.user_id, entry_id=entry_id
                )
                if not observed.get("error"):
                    context = get_default_context()
                    row = await primary_node_record(
                        context.database, Comment, comment_id
                    )
                    if row is not None:
                        comment = await context._deserialize_entity(Comment, row)
                        if (await comment_parent_entry(comment)).id == entry_id:
                            exported = await export_node(comment)
                            await attach_author_exports([exported])
                            result = {
                                "comment": exported,
                                "message": "This approved comment already applied. Current comment returned.",
                            }
        except (InsufficientPermissionsError, ResourceNotFoundError):
            # The durable effect remains applied even if current access or
            # containment is gone. Availability failures must still propagate.
            pass
        finally:
            current_scope_workspace_id.reset(reset)
    result["execution_receipt"] = {
        "id": execution_record_id(sc.token),
        "status": "applied",
        "replayed": True,
    }
    return result


async def _readback(
    sc: StagedChange, workspace_id: str, evidence: dict[str, Any]
) -> dict[str, Any]:
    if sc.kind in {"add_comment", "edit_comment", "delete_comment"}:
        return await _readback_comment(sc, workspace_id, evidence)

    from app.agentive.staging_executors import _call_endpoint
    from app.api.apps import get_app
    from app.api.entries import get_entry
    from app.api.tracks import get_track
    from app.services.agent_scope import current_scope_workspace_id
    from app.services.resource_location import locate_resource

    kind = {
        "create_app": "app",
        "create_track": "track",
        "update_app": "app",
        "update_track": "track",
        "delete_track": "track",
        "delete_app": "app",
    }.get(sc.kind, "entry")
    resource_id = evidence.get(f"{kind}_id")
    location = await locate_resource(kind, resource_id or "")
    result: dict[str, Any] = {
        "ok": True,
        "applied": True,
        "result_access": "withheld",
        "message": "This approved change already applied. Its current record is unavailable.",
    }
    if evidence.get("uninstalled"):
        result["uninstalled"] = True
        result["archived"] = bool(evidence.get("archived"))
        result["message"] = (
            "This approved uninstall already applied. Current unavailable content is withheld."
        )
    if evidence.get("deleted"):
        result["deleted"] = True
        result["message"] = (
            "This approved deletion already applied. The deleted record is not returned."
        )
    if location is not None and location.workspace_id == workspace_id:
        reset = current_scope_workspace_id.set(workspace_id)
        try:
            handler = {"app": get_app, "track": get_track, "entry": get_entry}[kind]
            observed = await _call_endpoint(
                handler, sc.user_id, **{f"{kind}_id": resource_id}
            )
            if not observed.get("error"):
                result = observed
                result["message"] = (
                    "This approved change already applied. Current record returned."
                )
        finally:
            current_scope_workspace_id.reset(reset)
    result["execution_receipt"] = {
        "id": execution_record_id(sc.token),
        "status": "applied",
        "replayed": True,
    }
    if evidence.get("_external_outcome") == "unknown":
        result.update(
            {
                "ok": False,
                "applied": True,
                "application_state": "reconciliation_required",
                "error_code": "external_outcome_reconciliation_required",
                "message": "The Entry change applied, but a hook outcome needs reconciliation. Do not replay this change.",
            }
        )
    return result


async def apply_staged_command_once(
    *,
    sc: StagedChange,
    request: Any,
    execute: Callable[[], Awaitable[dict[str, Any]]],
    batch_step: BatchStepApproval | None = None,
) -> dict[str, Any]:
    """Claim a command, or reconcile its saved effect without replaying writes.

    The canonical Entry service owns graph/result commit. API projections,
    notifications and token completion happen after it. A failure before that
    commit is safe to retry; a later failure must use the exact receipt.
    """
    identity = await _identity(sc, request)
    saved = await load_staging_execution_result(**identity)
    if saved is not None:
        return await _readback(sc, identity["workspace_id"], saved)
    if sc.execution_contract_version != "durable-staging-v1":
        raise StagingError(
            "legacy_execution_reconciliation_required",
            "This earlier approved proposal may have unrecorded effects. Reconcile it before retrying.",
        )
    try:
        claim = await claim_staging_execution(**identity)
    except StagingError as exc:
        if exc.code == "application_reconciliation_required":
            # Another worker may commit between our read and claim attempt.
            saved = await load_staging_execution_result(**identity)
            if saved is not None:
                return await _readback(sc, identity["workspace_id"], saved)
        raise
    from app.services.hooks.registry import get_workspace_hooks

    points = (
        ()
        if sc.kind
        in {
            "delete_entry",
            "delete_track",
            "delete_app",
            "add_comment",
            "edit_comment",
            "delete_comment",
        }
        else (
            ()
            if sc.kind in {"update_app", "update_track"}
            else (
                ("entry.create", "entry.validate", "entry.update", "entry.transform")
                if sc.kind in {"create_app", "create_track"}
                else (
                    ("entry.create", "entry.validate")
                    if sc.kind == "create_entry"
                    else (
                        "entry.update",
                        "entry.validate",
                        "entry.transform",
                    )
                )
            )
        )
    )
    external = any(
        binding.get("mode") == "tool"
        for point in points
        for binding in get_workspace_hooks(identity["workspace_id"], point)
    )
    if external:
        await mark_staging_external_dispatch(claim)
    bound = None
    try:
        async with bind_staging_command_execution(
            claim, sc.token, external=external, batch_step=batch_step
        ) as bound:
            result = await execute()
    except BaseException:
        saved = await load_staging_execution_result(**identity)
        if saved is None and (
            not external or (bound is not None and not bound.hooks_started)
        ):
            await release_unapplied_staging_claim(claim, dispatched=external)
        # A dispatched/interrupted hook remains held, even when graph writes
        # rolled back. Losing its outcome cannot confer retry authority.
        raise
    saved = await load_staging_execution_result(**identity)
    if saved is None:
        if external and bound is not None and bound.hooks_started:
            raise StagingError(
                "external_outcome_reconciliation_required",
                "A hook was invoked but the Entry change did not commit. Reconcile its outcome before further action.",
            )
        await release_unapplied_staging_claim(claim, dispatched=external)
        if not (
            result.get("error")
            or result.get("ok") is False
            or result.get("skipped") is True
        ):
            raise StagingError(
                "execution_receipt_required",
                "Entry change did not commit its effect receipt",
            )
        return result
    if external:
        if bound is not None and not bound.hooks_failed and not bound.hooks_pending:
            await finish_staging_external_dispatch(claim)
            saved = await load_staging_execution_result(**identity)
        else:
            return await _readback(sc, identity["workspace_id"], saved)
    if result.get("error") or result.get("ok") is False:
        return await _readback(sc, identity["workspace_id"], saved)
    result["execution_receipt"] = {
        "id": claim.object_id,
        "status": "applied",
        "replayed": False,
    }
    return result
