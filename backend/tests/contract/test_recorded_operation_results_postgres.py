"""Recovery reads original completion without re-entering a command handler."""

import pytest
from jvspatial.core.context import GraphContext, scoped_default_context_async

from app.contracts.runtime import ExecutionScope
from app.models.edges import COLLABORATES_ON, EXCLUDED_FROM, HAS_APPLICATION_DEFINITION
from app.models.nodes import ApplicationDefinition
from app.schemas.policy import Subject
from app.services.app_operations import dispatch, recorded_results
from app.services.app_operations.registry import (
    register_app_operations,
    unregister_app_operations,
)
from app.services.application_definitions import definition_fingerprint
from app.services.permissions import get_user_node, resolve_role
from tests.contract.test_operation_nested_result_postgres import operation_context
from tests.contract.test_operation_parent_result_postgres import child_reference

pytestmark = [pytest.mark.contract, pytest.mark.postgres, pytest.mark.asyncio]


async def prepared(monkeypatch):
    actor, workspace, app, visible, _ = await operation_context(role="editor")
    spec = {"key": "record", "kind": "execute", "handler_ref": "reference:record"}
    manifest = {"app": {"operations": [spec]}}
    definition = await ApplicationDefinition.create(
        app_id=app.id,
        workspace_id=workspace.id,
        revision=1,
        status="active",
        canonical_manifest=manifest,
        manifest_fingerprint=definition_fingerprint(manifest),
    )
    await app.connect(definition, edge=HAS_APPLICATION_DEFINITION)
    app.active_definition_id, app.active_definition_revision = definition.id, 1
    await app.save()
    calls = []

    async def handler(payload, context):
        calls.append("effect")
        child = await child_reference("comment", visible, actor)
        return {"created": {"kind": "comment", "id": child.id}}

    register_app_operations(workspace.id, app.id, "reference", [spec])
    monkeypatch.setattr(dispatch, "resolve_handler", lambda ref: handler)
    args = dict(
        app_id=app.id, operation_key="record", payload={}, idempotency_key="one-intent"
    )
    first = await dispatch.invoke_app_operation(
        user_id=actor.id, workspace_id=workspace.id, **args
    )
    assert calls == ["effect"]

    def no_handler(*args, **kwargs):
        pytest.fail("Recovery attempted to resolve a command handler")

    monkeypatch.setattr(dispatch, "resolve_handler", no_handler)
    scope = ExecutionScope.create(
        principal_id=actor.id,
        workspace_id=workspace.id,
        origin="recovery_qualification",
    )
    return actor, app, scope, args, first, calls


@pytest.mark.parametrize(
    "change", ["none", "excluded", "removed", "changed", "read_kind"]
)
async def test_current_output_or_opaque_completion_never_reexecutes(
    postgres_raw_db, monkeypatch, change
):
    async with scoped_default_context_async(GraphContext(database=postgres_raw_db)):
        actor, app, scope, args, first, calls = await prepared(monkeypatch)
        receipt_id = first["operation_receipt"]["id"]
        before = await postgres_raw_db.get("object", receipt_id)
        try:
            if change == "excluded":
                profile = await get_user_node(actor.id)
                await profile.connect(app, edge=EXCLUDED_FROM)
                for edge in await (await profile.get_context()).find_edges_between(
                    profile.id, app.id, edge_class=COLLABORATES_ON
                ):
                    await edge.delete()
                assert await resolve_role(actor.id, "app", app.id) is None
            elif change != "none":
                unregister_app_operations(scope.workspace_id, app.id)
                if change != "removed":
                    register_app_operations(
                        scope.workspace_id,
                        app.id,
                        "reference",
                        [
                            {
                                "key": "record",
                                "kind": "read" if change == "read_kind" else "execute",
                                "handler_ref": "reference:replacement",
                            }
                        ],
                    )
            if change in {"excluded", "removed", "read_kind"}:

                async def no_output(*args):
                    pytest.fail("Private saved output read before current eligibility")

                monkeypatch.setattr(recorded_results, "_saved_result", no_output)
            result = await recorded_results.recover_recorded_operation_response(
                scope=scope,
                subject=Subject(kind="human", id=actor.id),
                **args,
            )
            public = result.model_dump()
            assert public["operation_receipt"] == {
                "id": receipt_id,
                "status": "succeeded",
                "replayed": True,
            }
            assert public["current_state_verified"] is False
            if change == "none":
                assert public["output_disposition"] == "available"
                assert public["output"] == first["output"]
            else:
                assert set(public) == {
                    "operation_receipt",
                    "output_disposition",
                    "current_state_verified",
                }
                assert public["output_disposition"] == "withheld"
            assert calls == ["effect"]
            assert await postgres_raw_db.get("object", receipt_id) == before
        finally:
            unregister_app_operations(scope.workspace_id, app.id)


@pytest.mark.parametrize("encoded", ["{broken", "[]", '{"output": {"legacy": true}}'])
async def test_unusable_saved_output_is_withheld_without_repair(
    postgres_raw_db, monkeypatch, encoded
):
    async with scoped_default_context_async(GraphContext(database=postgres_raw_db)):
        actor, app, scope, args, first, calls = await prepared(monkeypatch)
        try:
            receipt_id = first["operation_receipt"]["id"]
            row = await postgres_raw_db.get("object", receipt_id)
            row["context"]["result_json"] = encoded
            await postgres_raw_db.save("object", row)
            before = await postgres_raw_db.get("object", receipt_id)
            result = await recorded_results.recover_recorded_operation_response(
                scope=scope, subject=Subject(kind="human", id=actor.id), **args
            )
            assert result.output_disposition == "withheld"
            assert await postgres_raw_db.get("object", receipt_id) == before
            assert calls == ["effect"]
        finally:
            unregister_app_operations(scope.workspace_id, app.id)


async def test_unexpected_output_storage_failure_is_not_success(
    postgres_raw_db, monkeypatch
):
    async with scoped_default_context_async(GraphContext(database=postgres_raw_db)):
        actor, app, scope, args, _, calls = await prepared(monkeypatch)

        async def outage(*args):
            raise RuntimeError("storage unavailable")

        monkeypatch.setattr(recorded_results, "_saved_result", outage)
        try:
            with pytest.raises(RuntimeError, match="storage unavailable"):
                await recorded_results.recover_recorded_operation_response(
                    scope=scope, subject=Subject(kind="human", id=actor.id), **args
                )
            assert calls == ["effect"]
        finally:
            unregister_app_operations(scope.workspace_id, app.id)


@pytest.mark.parametrize("native", [False, True])
async def test_recovery_refuses_ambient_effect_transaction(postgres_raw_db, native):
    from jvspatial.core.context import graph_transaction

    from app.services.app_operations.transaction_scope import (
        OperationTransactionUnavailable,
        postgres_graph_transaction,
        require_outside_graph_transaction,
    )

    async with scoped_default_context_async(GraphContext(database=postgres_raw_db)):
        require_outside_graph_transaction()
        manager = graph_transaction if native else postgres_graph_transaction
        async with manager(postgres_raw_db):
            with pytest.raises(
                OperationTransactionUnavailable, match="outside effect transactions"
            ):
                require_outside_graph_transaction()
        require_outside_graph_transaction()


@pytest.mark.parametrize("change", ["absent", "body_conflict", "incomplete"])
async def test_missing_conflicting_or_incomplete_journal_is_never_completed(
    postgres_raw_db, monkeypatch, change
):
    from app.services.app_operations.execution_receipts import (
        OperationReceiptConflict,
        OperationReceiptIncomplete,
    )

    async with scoped_default_context_async(GraphContext(database=postgres_raw_db)):
        actor, app, scope, args, first, calls = await prepared(monkeypatch)
        try:
            if change == "absent":
                result = await recorded_results.recover_recorded_operation_response(
                    scope=scope,
                    subject=Subject(kind="human", id=actor.id),
                    **{**args, "idempotency_key": "different-intent"},
                )
                assert result is None
            else:
                if change == "body_conflict":
                    args["payload"] = {"different": True}
                    expected = OperationReceiptConflict
                else:
                    row = await postgres_raw_db.get(
                        "object", first["operation_receipt"]["id"]
                    )
                    row["context"]["status"] = "claimed"
                    await postgres_raw_db.save("object", row)
                    expected = OperationReceiptIncomplete
                with pytest.raises(expected):
                    await recorded_results.recover_recorded_operation_response(
                        scope=scope, subject=Subject(kind="human", id=actor.id), **args
                    )
            assert calls == ["effect"]
        finally:
            unregister_app_operations(scope.workspace_id, app.id)


@pytest.mark.parametrize(
    "change", ["offboard", "app_excluded", "receipt_hash", "receipt_status"]
)
async def test_final_admission_rechecks_changes_after_output_read(
    postgres_raw_db, monkeypatch, change
):
    from app.api.errors import InsufficientPermissionsError
    from app.models.edges import IS_MEMBER_OF
    from app.services.app_operations.execution_receipts import (
        OperationReceiptConflict,
        OperationReceiptIncomplete,
    )

    async with scoped_default_context_async(GraphContext(database=postgres_raw_db)):
        actor, app, scope, args, first, calls = await prepared(monkeypatch)
        saved = recorded_results._saved_result

        async def change_after_read(*arguments):
            result = await saved(*arguments)
            if change in {"offboard", "app_excluded"}:
                profile = await get_user_node(actor.id)
                context = await profile.get_context()
                target = scope.workspace_id if change == "offboard" else app.id
                edge_class = IS_MEMBER_OF if change == "offboard" else COLLABORATES_ON
                for edge in await context.find_edges_between(
                    profile.id, target, edge_class=edge_class
                ):
                    await edge.delete()
                if change == "app_excluded":
                    await profile.connect(app, edge=EXCLUDED_FROM)
                    assert await resolve_role(actor.id, "app", app.id) is None
            else:
                row = await postgres_raw_db.get(
                    "object", first["operation_receipt"]["id"]
                )
                row["context"][
                    "request_hash" if change == "receipt_hash" else "status"
                ] = "changed"
                await postgres_raw_db.save("object", row)
            return result

        monkeypatch.setattr(recorded_results, "_saved_result", change_after_read)
        try:
            if change == "app_excluded":
                result = await recorded_results.recover_recorded_operation_response(
                    scope=scope, subject=Subject(kind="human", id=actor.id), **args
                )
                assert result.output_disposition == "withheld"
            else:
                expected = {
                    "offboard": InsufficientPermissionsError,
                    "receipt_hash": OperationReceiptConflict,
                    "receipt_status": OperationReceiptIncomplete,
                }[change]
                with pytest.raises(expected):
                    await recorded_results.recover_recorded_operation_response(
                        scope=scope, subject=Subject(kind="human", id=actor.id), **args
                    )
            assert calls == ["effect"]
        finally:
            unregister_app_operations(scope.workspace_id, app.id)
