"""One authenticated profile must not fork an operation via its principal aliases."""

import pytest
from jvspatial.core.context import (
    GraphContext,
    get_default_context,
    scoped_default_context_async,
)

from app.models.edges import HAS_APPLICATION_DEFINITION
from app.models.nodes import ApplicationDefinition, Entry
from app.services.app_operations import dispatch
from app.services.app_operations.registry import (
    register_app_operations,
    unregister_app_operations,
)
from app.services.application_definitions import definition_fingerprint
from app.services.authority_facts import primary_node_record
from app.services.permissions import get_user_node
from tests.contract.test_operation_nested_result_postgres import operation_context
from tests.contract.test_operation_parent_result_postgres import child_reference

pytestmark = [pytest.mark.contract, pytest.mark.postgres, pytest.mark.asyncio]


@pytest.mark.parametrize("graph_first", [False, True])
@pytest.mark.parametrize("operation_kind", ["read", "execute"])
async def test_human_auth_and_graph_aliases_share_one_effect_receipt(
    postgres_raw_db, monkeypatch, graph_first, operation_kind
):
    async with scoped_default_context_async(GraphContext(database=postgres_raw_db)):
        actor, workspace, app, visible, _ = await operation_context(role="editor")
        profile = await get_user_node(actor.id)
        assert profile.id != actor.id and profile.user_id == actor.id
        spec = {
            "key": "alias_create",
            "kind": operation_kind,
            "handler_ref": "reference:alias_create",
        }
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
        calls = 0

        async def handler(payload, context):
            nonlocal calls
            calls += 1
            if operation_kind == "read":
                return {"read": True}
            graph = get_default_context()
            row = await primary_node_record(graph.database, Entry, visible.id)
            parent = await graph._deserialize_entity(Entry, row)
            child = await child_reference("comment", parent, actor)
            return {"created": {"kind": "comment", "id": child.id}}

        register_app_operations(workspace.id, app.id, "reference", [spec])
        monkeypatch.setattr(dispatch, "resolve_handler", lambda ref: handler)
        arguments = dict(
            workspace_id=workspace.id,
            app_id=app.id,
            operation_key="alias_create",
            payload={},
            idempotency_key="same-human-command",
        )
        try:
            first_id, second_id = (
                (profile.id, actor.id) if graph_first else (actor.id, profile.id)
            )
            first = await dispatch.invoke_app_operation(user_id=first_id, **arguments)
            second = await dispatch.invoke_app_operation(user_id=second_id, **arguments)
            assert (
                calls == 1
            ), "Changing the current human's principal alias repeated the committed effect"
            assert first["output"] == second["output"]
            if operation_kind == "execute":
                assert (
                    first["operation_receipt"]["id"]
                    == second["operation_receipt"]["id"]
                )
                assert second["operation_receipt"]["replayed"] is True
        finally:
            unregister_app_operations(workspace.id, app.id)


def alias_identity(actor, workspace, app, principal_id):
    from app.contracts.operations import OperationIdentity

    return OperationIdentity.create(
        workspace_id=workspace.id,
        app_id=app.id,
        operation_key="legacy_alias",
        principal_id=principal_id,
        idempotency_key="legacy-key",
    )


@pytest.mark.parametrize("legacy_graph", [False, True])
async def test_legacy_alias_receipt_keeps_original_identity_and_document(
    postgres_raw_db, legacy_graph
):
    from app.schemas.policy import Subject
    from app.services.app_operations.execution_receipts import (
        OperationReceiptIncomplete,
        execute_operation_once,
        receipt_object_id,
    )
    from app.services.app_operations.principal import resolve_operation_principal

    async with scoped_default_context_async(GraphContext(database=postgres_raw_db)):
        actor, workspace, app, _, _ = await operation_context(role="editor")
        profile = await get_user_node(actor.id)
        old_identity = alias_identity(
            actor, workspace, app, profile.id if legacy_graph else actor.id
        )
        preferred = alias_identity(actor, workspace, app, actor.id)
        calls = 0

        async def handler():
            nonlocal calls
            calls += 1
            return {"output": {"legacy": "original result"}}

        await execute_operation_once(
            identity=old_identity, request_hash="original-hash", execute=handler
        )
        original = await postgres_raw_db.get("object", receipt_object_id(old_identity))
        principal = await resolve_operation_principal(
            Subject(kind="human", id=profile.id)
        )
        replay = await execute_operation_once(
            identity=preferred,
            principal_aliases=principal.aliases,
            admit_identity=principal.admit,
            request_hash="original-hash",
            execute=handler,
        )
        assert calls == 1 and replay.replayed
        assert replay.identity == old_identity
        assert replay.result == {"output": {"legacy": "original result"}}
        assert (
            await postgres_raw_db.get("object", receipt_object_id(old_identity))
            == original
        )
        other_identity = alias_identity(
            actor, workspace, app, actor.id if legacy_graph else profile.id
        )
        marker = await postgres_raw_db.get("object", receipt_object_id(other_identity))
        assert marker["context"]["status"] == "alias"
        assert marker["context"]["canonical_receipt_id"] == receipt_object_id(
            old_identity
        )
        # An older worker cannot treat the new alias marker as an unused key.
        with pytest.raises(OperationReceiptIncomplete):
            await execute_operation_once(
                identity=other_identity, request_hash="original-hash", execute=handler
            )
        assert calls == 1


@pytest.mark.parametrize(
    "legacy_state", ["two_completed", "incomplete", "foreign_pointer", "input_conflict"]
)
async def test_ambiguous_legacy_aliases_never_authorize_a_new_effect(
    postgres_raw_db, legacy_state
):
    from app.services.app_operations.execution_receipts import (
        OperationReceiptConflict,
        OperationReceiptIncomplete,
        _receipt_document,
        execute_operation_once,
        receipt_object_id,
    )
    from app.services.app_operations.transaction_scope import postgres_graph_transaction

    async with scoped_default_context_async(GraphContext(database=postgres_raw_db)):
        actor, workspace, app, _, _ = await operation_context(role="editor")
        profile = await get_user_node(actor.id)
        old_identity = alias_identity(actor, workspace, app, profile.id)
        preferred = alias_identity(actor, workspace, app, actor.id)
        calls = 0

        async def handler():
            nonlocal calls
            calls += 1
            return {"output": {"run": calls}}

        if legacy_state in {"two_completed", "input_conflict"}:
            await execute_operation_once(
                identity=old_identity, request_hash="original-hash", execute=handler
            )
            if legacy_state == "two_completed":
                await execute_operation_once(
                    identity=preferred, request_hash="original-hash", execute=handler
                )
        else:
            row = _receipt_document(
                identity=old_identity,
                request_hash="original-hash",
                status="claimed" if legacy_state == "incomplete" else "alias",
            )
            if legacy_state == "foreign_pointer":
                row["context"][
                    "canonical_receipt_id"
                ] = "o.OperationExecutionReceipt.foreign"
            async with postgres_graph_transaction() as transaction:
                await transaction.insert_if_absent("object", row)
        before_calls = calls
        originals = {
            identity.principal_id: await postgres_raw_db.get(
                "object", receipt_object_id(identity)
            )
            for identity in (old_identity, preferred)
        }
        expected = (
            OperationReceiptConflict
            if legacy_state == "input_conflict"
            else OperationReceiptIncomplete
        )
        with pytest.raises(expected):
            await execute_operation_once(
                identity=preferred,
                principal_aliases=(profile.id,),
                request_hash=(
                    "changed-hash"
                    if legacy_state == "input_conflict"
                    else "original-hash"
                ),
                execute=handler,
            )
        assert calls == before_calls
        for identity in (old_identity, preferred):
            assert (
                await postgres_raw_db.get("object", receipt_object_id(identity))
                == originals[identity.principal_id]
            )


async def test_legacy_read_cache_alias_survives_worker_memory_loss(postgres_raw_db):
    from app.services.app_operations.idempotency import (
        clear_idempotency_memory_for_tests,
        lookup_idempotent_result,
        store_idempotent_result,
    )

    async with scoped_default_context_async(GraphContext(database=postgres_raw_db)):
        actor, workspace, app, _, _ = await operation_context()
        profile = await get_user_node(actor.id)
        scope = dict(
            workspace_id=workspace.id,
            app_id=app.id,
            operation_key="legacy_read",
            idempotency_key="legacy-cache",
            request_hash="same-input",
        )
        await store_idempotent_result(
            principal_id=profile.id, result={"output": {"value": "original"}}, **scope
        )
        clear_idempotency_memory_for_tests()
        result = await lookup_idempotent_result(
            principal_id=actor.id, principal_aliases=(profile.id,), **scope
        )
        assert result == {"output": {"value": "original"}}
        result["output"]["value"] = "caller mutation"
        again = await lookup_idempotent_result(
            principal_id=actor.id, principal_aliases=(profile.id,), **scope
        )
        assert again == {"output": {"value": "original"}}


async def test_conflicting_read_cache_aliases_are_not_selected_by_query_order(
    postgres_raw_db,
):
    from app.api.errors import BadRequestError
    from app.services.app_operations.idempotency import (
        clear_idempotency_memory_for_tests,
        lookup_idempotent_result,
        store_idempotent_result,
    )

    async with scoped_default_context_async(GraphContext(database=postgres_raw_db)):
        actor, workspace, app, _, _ = await operation_context()
        profile = await get_user_node(actor.id)
        scope = dict(
            workspace_id=workspace.id,
            app_id=app.id,
            operation_key="legacy_read",
            idempotency_key="legacy-cache",
            request_hash="same-input",
        )
        await store_idempotent_result(
            principal_id=profile.id, result={"output": {"value": "old"}}, **scope
        )
        await store_idempotent_result(
            principal_id=actor.id, result={"output": {"value": "different"}}, **scope
        )
        clear_idempotency_memory_for_tests()
        with pytest.raises(BadRequestError, match="require reconciliation"):
            await lookup_idempotent_result(
                principal_id=actor.id, principal_aliases=(profile.id,), **scope
            )


async def test_cache_lookup_outage_cannot_masquerade_as_a_missing_key(
    postgres_raw_db, monkeypatch
):
    from app.exceptions import AuthorizationUnavailableError
    from app.models.operation_idempotency import OperationIdempotencyRecord
    from app.services.app_operations.idempotency import lookup_idempotent_result

    async with scoped_default_context_async(GraphContext(database=postgres_raw_db)):
        actor, workspace, app, _, _ = await operation_context()
        profile = await get_user_node(actor.id)

        find = postgres_raw_db.find

        async def unavailable(collection, query=None, *args, **kwargs):
            if (
                collection == "object"
                and query
                and query.get("entity") == OperationIdempotencyRecord._entity_name()
            ):
                raise OSError("Injected cache store outage")
            return await find(collection, query, *args, **kwargs)

        monkeypatch.setattr(postgres_raw_db, "find", unavailable)
        with pytest.raises(AuthorizationUnavailableError):
            await lookup_idempotent_result(
                workspace_id=workspace.id,
                app_id=app.id,
                operation_key="read",
                principal_id=actor.id,
                principal_aliases=(profile.id,),
                idempotency_key="lookup-outage",
                request_hash="input",
            )


async def test_corrupt_cache_body_cannot_masquerade_as_a_missing_key(postgres_raw_db):
    from app.exceptions import AuthorizationUnavailableError
    from app.models.operation_idempotency import OperationIdempotencyRecord
    from app.services.app_operations.idempotency import lookup_idempotent_result

    async with scoped_default_context_async(GraphContext(database=postgres_raw_db)):
        actor, workspace, app, _, _ = await operation_context()
        profile = await get_user_node(actor.id)
        row = OperationIdempotencyRecord(
            workspace_id=workspace.id,
            app_id=app.id,
            operation_key="read",
            principal_id=profile.id,
            idempotency_key="corrupt-cache",
            request_hash="input",
            result_json="[]",
        )
        await row.save()
        with pytest.raises(AuthorizationUnavailableError):
            await lookup_idempotent_result(
                workspace_id=workspace.id,
                app_id=app.id,
                operation_key="read",
                principal_id=actor.id,
                principal_aliases=(profile.id,),
                idempotency_key="corrupt-cache",
                request_hash="input",
            )


async def test_process_cache_cannot_hide_duplicate_durable_rows(postgres_raw_db):
    from app.exceptions import AuthorizationUnavailableError
    from app.models.operation_idempotency import OperationIdempotencyRecord
    from app.services.app_operations.idempotency import (
        lookup_idempotent_result,
        store_idempotent_result,
    )

    async with scoped_default_context_async(GraphContext(database=postgres_raw_db)):
        actor, workspace, app, _, _ = await operation_context()
        profile = await get_user_node(actor.id)
        scope = dict(
            workspace_id=workspace.id,
            app_id=app.id,
            operation_key="duplicate_read",
            principal_id=actor.id,
            idempotency_key="duplicate-cache",
            request_hash="input",
        )
        await store_idempotent_result(result={"output": {"value": "memory"}}, **scope)
        duplicate = OperationIdempotencyRecord(
            **{key: value for key, value in scope.items()},
            result_json='{"output":{"value":"other"}}',
        )
        await duplicate.save()
        with pytest.raises(AuthorizationUnavailableError):
            await lookup_idempotent_result(principal_aliases=(profile.id,), **scope)


@pytest.mark.parametrize("legacy_receipt", [False, True])
@pytest.mark.parametrize("identity_change", ["disabled", "relinked"])
async def test_stale_identity_proof_cannot_dispatch_or_recover(
    postgres_raw_db, legacy_receipt, identity_change
):
    from app.api.errors import InsufficientPermissionsError
    from app.exceptions import AuthorizationUnavailableError
    from app.schemas.policy import Subject
    from app.services.app_operations.execution_receipts import (
        execute_operation_once,
        receipt_object_id,
    )
    from app.services.app_operations.principal import resolve_operation_principal

    async with scoped_default_context_async(GraphContext(database=postgres_raw_db)):
        actor, workspace, app, _, _ = await operation_context(role="editor")
        profile = await get_user_node(actor.id)
        preferred = alias_identity(actor, workspace, app, actor.id)
        legacy = alias_identity(actor, workspace, app, profile.id)
        principal = await resolve_operation_principal(
            Subject(kind="human", id=actor.id)
        )
        calls = 0

        async def handler():
            nonlocal calls
            calls += 1
            return {"output": {"proof": "completed"}}

        if legacy_receipt:
            await execute_operation_once(
                identity=legacy, request_hash="input", execute=handler
            )
        original = await postgres_raw_db.get("object", receipt_object_id(legacy))
        if identity_change == "disabled":
            await postgres_raw_db.find_one_and_update(
                "object",
                {"id": actor.id},
                {"$set": {"context.is_active": False}},
            )
        else:
            await postgres_raw_db.find_one_and_update(
                "node",
                {"id": profile.id},
                {"$set": {"context.user_id": "different-auth-identity"}},
            )
        before = calls
        with pytest.raises(
            (InsufficientPermissionsError, AuthorizationUnavailableError)
        ):
            await execute_operation_once(
                identity=preferred,
                principal_aliases=principal.aliases,
                admit_identity=principal.admit,
                request_hash="input",
                execute=handler,
            )
        assert calls == before
        assert (
            await postgres_raw_db.get("object", receipt_object_id(legacy)) == original
        )
        assert await postgres_raw_db.get("object", receipt_object_id(preferred)) is None
