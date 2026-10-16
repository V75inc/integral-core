"""Operation result dependencies use current nested-resource authority."""

import uuid

import pytest
from jvspatial.api.auth.models import User as AuthUser
from jvspatial.core.context import GraphContext, scoped_default_context_async

from app.api.errors import InsufficientPermissionsError
from app.models.edges import COLLABORATES_ON, EXCLUDED_FROM, IS_MEMBER_OF
from app.models.nodes import App, User
from app.services.app_graph import catalog_user
from app.services.app_operations.result_access import authorize_replayed_result
from app.services.entry_create import create_entry_in_track
from app.services.permissions import resolve_role
from app.services.resource_location import locate_resource
from tests.contract.test_staging_command_apply_postgres import proposal

pytestmark = [pytest.mark.contract, pytest.mark.postgres, pytest.mark.asyncio]


async def operation_context(*, role="viewer"):
    owner, workspace, track, _ = await proposal()
    location = await locate_resource("track", track.id)
    app = await App.get(location.app_id)
    actor = await AuthUser.create(
        email=f"result-reader-{uuid.uuid4().hex}@example.com", password_hash="fixture"
    )
    reader = await User.create(user_id=actor.id, display_name="Result reader")
    await catalog_user(reader)
    await reader.connect(workspace, edge=IS_MEMBER_OF, role="member")
    await reader.connect(app, edge=COLLABORATES_ON, role=role)
    visible = await create_entry_in_track(
        track=track, user_id=owner.id, workspace_id=workspace.id, title="Visible source"
    )
    private = await create_entry_in_track(
        track=track, user_id=owner.id, workspace_id=workspace.id, title="Private source"
    )
    await reader.connect(visible, edge=COLLABORATES_ON, role=role)
    await reader.connect(private, edge=EXCLUDED_FROM)
    assert await resolve_role(actor.id, "entry", visible.id) is not None
    assert await resolve_role(actor.id, "entry", private.id) is None
    return actor, workspace, app, visible, private


@pytest.mark.parametrize("include_visible_top_reference", [False, True])
async def test_operation_result_replay_checks_nested_private_reference(
    postgres_raw_db, include_visible_top_reference
):
    async with scoped_default_context_async(GraphContext(database=postgres_raw_db)):
        actor, workspace, app, visible, private = await operation_context()
        result = {
            "output": {
                "summary": {
                    "source": {"kind": "entry", "id": private.id},
                    "label": "Private source",
                }
            },
            "object_refs": (
                [{"kind": "entry", "id": visible.id}]
                if include_visible_top_reference
                else []
            ),
        }
        with pytest.raises(InsufficientPermissionsError):
            await authorize_replayed_result(
                result, user_id=actor.id, workspace_id=workspace.id, app_id=app.id
            )


async def test_initial_read_operation_refuses_nested_private_output(
    postgres_raw_db, monkeypatch
):
    from app.services.app_operations import dispatch
    from app.services.app_operations.registry import (
        register_app_operations,
        unregister_app_operations,
    )

    async with scoped_default_context_async(GraphContext(database=postgres_raw_db)):
        actor, workspace, app, _, private = await operation_context()

        async def handler(payload, context):
            return {
                "summary": {
                    "source": {"kind": "entry", "id": private.id},
                    "label": "Private source",
                }
            }

        register_app_operations(
            workspace.id,
            app.id,
            "reference",
            [
                {
                    "key": "nested_read",
                    "kind": "read",
                    "handler_ref": "reference:nested_read",
                    "input_schema": {"type": "object"},
                    "output_schema": {"type": "object"},
                }
            ],
        )
        monkeypatch.setattr(dispatch, "resolve_handler", lambda ref: handler)
        try:
            with pytest.raises(InsufficientPermissionsError):
                await dispatch.invoke_app_operation(
                    user_id=actor.id,
                    workspace_id=workspace.id,
                    app_id=app.id,
                    operation_key="nested_read",
                    payload={},
                )
        finally:
            unregister_app_operations(workspace.id, app.id)


async def test_initial_reference_free_output_rechecks_app_read_after_handler(
    postgres_raw_db, monkeypatch
):
    from app.services.app_operations import dispatch
    from app.services.app_operations.registry import (
        register_app_operations,
        unregister_app_operations,
    )
    from app.services.permissions import get_user_node

    async with scoped_default_context_async(GraphContext(database=postgres_raw_db)):
        actor, workspace, app, _, _ = await operation_context()
        profile = await get_user_node(actor.id)

        async def handler(payload, context):
            # Fault injection models the grant being removed during an awaited
            # read. It does not authorize a read handler to mutate permissions.
            grants = await postgres_raw_db.find(
                "edge",
                {
                    "source": profile.id,
                    "target": app.id,
                    "entity": COLLABORATES_ON._entity_name(),
                },
            )
            assert len(grants) == 1
            await postgres_raw_db.delete("edge", grants[0]["id"])
            assert await resolve_role(actor.id, "app", app.id) is None
            return {"summary": "Unreferenced private App summary"}

        register_app_operations(
            workspace.id,
            app.id,
            "reference",
            [
                {
                    "key": "revoked_read",
                    "kind": "read",
                    "handler_ref": "reference:revoked_read",
                }
            ],
        )
        monkeypatch.setattr(dispatch, "resolve_handler", lambda ref: handler)
        try:
            with pytest.raises(InsufficientPermissionsError):
                await dispatch.invoke_app_operation(
                    user_id=actor.id,
                    workspace_id=workspace.id,
                    app_id=app.id,
                    operation_key="revoked_read",
                    payload={},
                )
        finally:
            unregister_app_operations(workspace.id, app.id)


async def test_nested_readable_reference_is_released(postgres_raw_db):
    async with scoped_default_context_async(GraphContext(database=postgres_raw_db)):
        actor, workspace, app, visible, _ = await operation_context()
        await authorize_replayed_result(
            {
                "output": {
                    "source": {"kind": "entry", "id": visible.id},
                    "label": "Visible source",
                }
            },
            user_id=actor.id,
            workspace_id=workspace.id,
            app_id=app.id,
        )


async def test_nested_reference_read_outage_is_not_a_verified_denial(
    postgres_raw_db, monkeypatch
):
    from app.exceptions import AuthorizationUnavailableError
    from app.services.app_operations import result_access

    async with scoped_default_context_async(GraphContext(database=postgres_raw_db)):
        actor, workspace, app, visible, _ = await operation_context()
        locate = result_access.locate_resource

        async def unavailable(kind, resource_id, **kwargs):
            if kind == "entry":
                raise AuthorizationUnavailableError()
            return await locate(kind, resource_id, **kwargs)

        monkeypatch.setattr(result_access, "locate_resource", unavailable)
        with pytest.raises(AuthorizationUnavailableError):
            await authorize_replayed_result(
                {"output": {"source": {"kind": "entry", "id": visible.id}}},
                user_id=actor.id,
                workspace_id=workspace.id,
                app_id=app.id,
            )


async def test_nested_reference_rechecks_current_entry_exclusion(postgres_raw_db):
    from app.services.permissions import get_user_node

    async with scoped_default_context_async(GraphContext(database=postgres_raw_db)):
        actor, workspace, app, visible, _ = await operation_context()
        result = {
            "output": {
                "source": {"kind": "entry", "id": visible.id},
                "label": "Visible source",
            }
        }
        await authorize_replayed_result(
            result, user_id=actor.id, workspace_id=workspace.id, app_id=app.id
        )
        reader = await get_user_node(actor.id)
        # An exclusion removes inherited access; a direct grant remains valid
        # until it too is revoked. Remove that actual grant before asserting deny.
        await reader.connect(visible, edge=EXCLUDED_FROM)
        grants = await postgres_raw_db.find(
            "edge",
            {
                "source": reader.id,
                "target": visible.id,
                "entity": COLLABORATES_ON._entity_name(),
            },
        )
        assert len(grants) == 1
        await postgres_raw_db.delete("edge", grants[0]["id"])
        assert await resolve_role(actor.id, "entry", visible.id) is None
        with pytest.raises(InsufficientPermissionsError):
            await authorize_replayed_result(
                result, user_id=actor.id, workspace_id=workspace.id, app_id=app.id
            )


async def test_committed_operation_replay_withholds_revoked_output_without_repeating_effect(
    postgres_raw_db, monkeypatch
):
    from app.agentive.staging_executors import _call_endpoint
    from app.api.entries import update_entry
    from app.contracts.operations import OperationIdentity, canonical_request_hash
    from app.services import workspace_membership
    from app.services.agent_scope import current_scope_workspace_id
    from app.services.app_operations.execution_receipts import (
        execute_operation_once,
        receipt_object_id,
    )
    from app.services.permissions import get_user_node

    async with scoped_default_context_async(GraphContext(database=postgres_raw_db)):
        actor, workspace, app, visible, _ = await operation_context(role="editor")
        identity = OperationIdentity.create(
            workspace_id=workspace.id,
            app_id=app.id,
            operation_key="record.rename",
            principal_id=actor.id,
            idempotency_key="nested-result-replay",
        )
        request_hash = canonical_request_hash(
            {"entry_id": visible.id, "title": "Updated once"}
        )
        calls = 0

        async def defer_delivery(outbox):
            # This test qualifies receipt/result admission. Delivery recovery
            # is separately qualified through the canonical outbox tests.
            return None

        monkeypatch.setattr(workspace_membership, "_deliver", defer_delivery)

        async def mutate():
            nonlocal calls
            calls += 1
            reset = current_scope_workspace_id.set(workspace.id)
            try:
                applied = await _call_endpoint(
                    update_entry, actor.id, entry_id=visible.id, title="Updated once"
                )
            finally:
                current_scope_workspace_id.reset(reset)
            assert not applied.get("error"), applied
            return {
                "output": {
                    "source": {"kind": "entry", "id": visible.id},
                    "label": "Updated once",
                }
            }

        first = await execute_operation_once(
            identity=identity,
            request_hash=request_hash,
            execute=mutate,
            database=postgres_raw_db,
        )
        assert not first.replayed
        await authorize_replayed_result(
            first.result, user_id=actor.id, workspace_id=workspace.id, app_id=app.id
        )
        entry_after = await postgres_raw_db.get("node", visible.id)
        reader = await get_user_node(actor.id)
        await reader.connect(visible, edge=EXCLUDED_FROM)
        grants = await postgres_raw_db.find(
            "edge",
            {
                "source": reader.id,
                "target": visible.id,
                "entity": COLLABORATES_ON._entity_name(),
            },
        )
        assert len(grants) == 1
        await postgres_raw_db.delete("edge", grants[0]["id"])
        assert await resolve_role(actor.id, "entry", visible.id) is None
        second = await execute_operation_once(
            identity=identity,
            request_hash=request_hash,
            execute=mutate,
            database=postgres_raw_db,
        )
        assert second.replayed
        with pytest.raises(InsufficientPermissionsError):
            await authorize_replayed_result(
                second.result,
                user_id=actor.id,
                workspace_id=workspace.id,
                app_id=app.id,
            )
        receipt = await postgres_raw_db.get("object", receipt_object_id(identity))
        assert receipt["context"]["status"] == "succeeded"
        assert await postgres_raw_db.get("node", visible.id) == entry_after
        assert calls == 1
        # Reacquiring legitimate read access can recover the same saved result;
        # it never causes the already committed mutation to run a second time.
        await reader.connect(visible, edge=COLLABORATES_ON, role="viewer")
        third = await execute_operation_once(
            identity=identity,
            request_hash=request_hash,
            execute=mutate,
            database=postgres_raw_db,
        )
        await authorize_replayed_result(
            third.result, user_id=actor.id, workspace_id=workspace.id, app_id=app.id
        )
        assert third.replayed and calls == 1
        assert await postgres_raw_db.get("node", visible.id) == entry_after


async def test_initial_result_ref_metadata_uses_bounded_canonical_location(
    postgres_raw_db, monkeypatch
):
    from app.services.app_operations import dispatch
    from app.services.app_operations.registry import (
        register_app_operations,
        unregister_app_operations,
    )

    async with scoped_default_context_async(GraphContext(database=postgres_raw_db)):
        actor, workspace, app, visible, _ = await operation_context()
        nodes = App.nodes

        async def refuse_track_inventory(self, *args, **kwargs):
            assert kwargs.get("node") != [
                "Track"
            ], "Result admission must not load the complete App Track inventory"
            return await nodes(self, *args, **kwargs)

        async def handler(payload, context):
            return {"object_refs": [{"kind": "entry", "id": visible.id}]}

        register_app_operations(
            workspace.id,
            app.id,
            "reference",
            [
                {
                    "key": "bounded_read",
                    "kind": "read",
                    "handler_ref": "reference:bounded_read",
                }
            ],
        )
        monkeypatch.setattr(dispatch, "resolve_handler", lambda ref: handler)
        monkeypatch.setattr(App, "nodes", refuse_track_inventory)
        try:
            result = await dispatch.invoke_app_operation(
                user_id=actor.id,
                workspace_id=workspace.id,
                app_id=app.id,
                operation_key="bounded_read",
                payload={},
            )
            assert result["object_refs"][0]["id"] == visible.id
            assert result["object_refs"][0]["title"] == "Visible source"
            assert result["object_refs"][0]["workspace_id"] == workspace.id
        finally:
            unregister_app_operations(workspace.id, app.id)


async def test_nested_reference_cannot_cross_selected_workspace_even_if_readable(
    postgres_raw_db,
):
    from app.services.permissions import get_user_node

    async with scoped_default_context_async(GraphContext(database=postgres_raw_db)):
        actor, workspace, app, _, _ = await operation_context()
        other_owner, other_workspace, other_track, _ = await proposal()
        other_location = await locate_resource("track", other_track.id)
        other_app = await App.get(other_location.app_id)
        entry = await create_entry_in_track(
            track=other_track,
            user_id=other_owner.id,
            workspace_id=other_workspace.id,
            title="Other workspace source",
        )
        reader = await get_user_node(actor.id)
        await reader.connect(other_workspace, edge=IS_MEMBER_OF, role="member")
        await reader.connect(other_app, edge=COLLABORATES_ON, role="viewer")
        await reader.connect(entry, edge=COLLABORATES_ON, role="viewer")
        assert await resolve_role(actor.id, "entry", entry.id) is not None
        with pytest.raises(InsufficientPermissionsError):
            await authorize_replayed_result(
                {"output": {"source": {"kind": "entry", "id": entry.id}}},
                user_id=actor.id,
                workspace_id=workspace.id,
                app_id=app.id,
            )


@pytest.mark.parametrize("mutation_reference", [False, True])
async def test_same_workspace_cross_app_source_needs_read_but_is_not_a_mutation_ref(
    postgres_raw_db, mutation_reference
):
    from app.models.edges import CONTAINS, OWNS
    from app.models.nodes import Track
    from app.services.app_graph import catalog_app, catalog_track
    from app.services.permissions import get_user_node

    async with scoped_default_context_async(GraphContext(database=postgres_raw_db)):
        actor, workspace, app, _, private = await operation_context()
        owner = await get_user_node(private.author_id)
        other_app = await App.create(
            name="Other reference domain", workspace_id=workspace.id
        )
        await catalog_app(other_app)
        await owner.connect(other_app, edge=OWNS)
        track = await Track.create(
            title="Other reference records", workspace_id=workspace.id
        )
        await catalog_track(track)
        await other_app.connect(track, edge=CONTAINS)
        await owner.connect(track, edge=OWNS)
        entry = await create_entry_in_track(
            track=track,
            user_id=owner.user_id,
            workspace_id=workspace.id,
            title="Readable source",
        )
        reader = await get_user_node(actor.id)
        await reader.connect(other_app, edge=COLLABORATES_ON, role="viewer")
        await reader.connect(entry, edge=COLLABORATES_ON, role="viewer")
        assert await resolve_role(actor.id, "entry", entry.id) is not None
        result = {"output": {"source": {"kind": "entry", "id": entry.id}}}
        if mutation_reference:
            result["object_refs"] = [{"kind": "entry", "id": entry.id}]
            with pytest.raises(InsufficientPermissionsError):
                await authorize_replayed_result(
                    result, user_id=actor.id, workspace_id=workspace.id, app_id=app.id
                )
        else:
            await authorize_replayed_result(
                result, user_id=actor.id, workspace_id=workspace.id, app_id=app.id
            )


async def test_initial_result_refuses_handler_contract_replacement(
    postgres_raw_db, monkeypatch
):
    from app.services.app_operations import dispatch
    from app.services.app_operations.registry import (
        register_app_operations,
        unregister_app_operations,
    )

    async with scoped_default_context_async(GraphContext(database=postgres_raw_db)):
        actor, workspace, app, _, _ = await operation_context()
        original = {
            "key": "contract_read",
            "kind": "read",
            "handler_ref": "reference:old",
        }

        async def handler(payload, context):
            register_app_operations(
                workspace.id,
                app.id,
                "reference",
                [{**original, "handler_ref": "reference:replacement"}],
            )
            return {"summary": "Old contract output"}

        register_app_operations(workspace.id, app.id, "reference", [original])
        monkeypatch.setattr(dispatch, "resolve_handler", lambda ref: handler)
        try:
            with pytest.raises(InsufficientPermissionsError):
                await dispatch.invoke_app_operation(
                    user_id=actor.id,
                    workspace_id=workspace.id,
                    app_id=app.id,
                    operation_key="contract_read",
                    payload={},
                )
        finally:
            unregister_app_operations(workspace.id, app.id)


@pytest.mark.parametrize("release_kind", ["initial", "read_cache", "command_receipt"])
async def test_result_release_rechecks_function_binding_after_prior_actor_check(
    postgres_raw_db, monkeypatch, release_kind
):
    from app.services import access_delegation
    from app.services.app_operations import dispatch, result_access
    from app.services.app_operations.registry import (
        register_app_operations,
        unregister_app_operations,
    )
    from app.services.application_definitions import definition_fingerprint
    from tests.contract.test_access_delegation_postgres import fixture

    async with scoped_default_context_async(GraphContext(database=postgres_raw_db)):
        owner, reader, workspace, app, _, entry, definition = await fixture()
        auth = await AuthUser.create(
            email=f"function-reader-{uuid.uuid4().hex}@example.com",
            password_hash="fixture",
        )
        reader.user_id = auth.id
        await reader.save()
        spec = definition.canonical_manifest["app"]["operations"][0]
        spec.update(
            kind="execute" if release_kind == "command_receipt" else "read",
            handler_ref="reference:declared_read",
        )
        definition.manifest_fingerprint = definition_fingerprint(
            definition.canonical_manifest
        )
        await definition.save()
        binding = await access_delegation.create_binding(
            actor_id=owner.id,
            app_id=app.id,
            subject_kind="human",
            subject_id=reader.id,
            role="reviewer",
            conditions=[],
        )
        arguments = dict(
            principal_id=auth.id,
            app_id=app.id,
            operation_key="approve",
            payload={"entry_id": entry.id},
        )
        assert (await access_delegation.action_decision(**arguments)).allowed

        handler_calls = 0

        async def handler(payload, context):
            nonlocal handler_calls
            handler_calls += 1
            return {"summary": "Only a current reviewer may receive this"}

        locate = result_access.locate_resource
        revoked = False

        async def revoke_between_checks(kind, resource_id, **kwargs):
            nonlocal revoked
            if not revoked:
                revoked = True
                # Inject the committed state change at final admission, after
                # the dispatcher's existing post-handler actor check.
                await postgres_raw_db.find_one_and_update(
                    "node", {"id": binding.id}, {"$set": {"context.active": False}}
                )
                assert not (
                    await access_delegation.action_decision(**arguments)
                ).allowed
                assert await resolve_role(auth.id, "app", app.id) == "viewer"
            return await locate(kind, resource_id, **kwargs)

        register_app_operations(
            workspace.id,
            app.id,
            "reference",
            [{**spec, "_definition_fingerprint": definition.manifest_fingerprint}],
        )
        monkeypatch.setattr(dispatch, "resolve_handler", lambda ref: handler)
        invoke_arguments = dict(
            user_id=auth.id,
            workspace_id=workspace.id,
            app_id=app.id,
            operation_key="approve",
            payload={"entry_id": entry.id},
            idempotency_key="function-result-release",
        )
        try:
            if release_kind != "initial":
                first = await dispatch.invoke_app_operation(**invoke_arguments)
                assert first["output"]["summary"]
            monkeypatch.setattr(result_access, "locate_resource", revoke_between_checks)
            with pytest.raises(InsufficientPermissionsError):
                await dispatch.invoke_app_operation(**invoke_arguments)
            assert revoked
            assert handler_calls == 1
            if release_kind == "command_receipt":
                from app.contracts.operations import OperationIdentity
                from app.services.app_operations.execution_receipts import (
                    receipt_object_id,
                )

                identity = OperationIdentity.create(
                    workspace_id=workspace.id,
                    app_id=app.id,
                    operation_key="approve",
                    principal_id=auth.id,
                    idempotency_key="function-result-release",
                )
                receipt = await postgres_raw_db.get(
                    "object", receipt_object_id(identity)
                )
                assert receipt["context"]["status"] == "succeeded"
        finally:
            unregister_app_operations(workspace.id, app.id)


@pytest.mark.parametrize("operation_kind", ["read", "execute"])
async def test_saved_result_does_not_relabel_old_output_as_replacement_contract(
    postgres_raw_db, monkeypatch, operation_kind
):
    from app.services.app_operations import dispatch
    from app.services.app_operations.registry import (
        register_app_operations,
        unregister_app_operations,
    )

    async with scoped_default_context_async(GraphContext(database=postgres_raw_db)):
        from app.models.edges import HAS_APPLICATION_DEFINITION
        from app.models.nodes import ApplicationDefinition
        from app.services.application_definitions import definition_fingerprint

        actor, workspace, app, _, _ = await operation_context(role="editor")
        spec = {
            "key": "versioned_read",
            "kind": operation_kind,
            "handler_ref": "reference:first",
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
        app.active_definition_id = definition.id
        app.active_definition_revision = 1
        await app.save()
        calls = []

        async def handler(payload, context):
            calls.append("first")
            return {"summary": "Old declared output"}

        register_app_operations(workspace.id, app.id, "reference", [spec])
        monkeypatch.setattr(dispatch, "resolve_handler", lambda ref: handler)
        arguments = dict(
            user_id=actor.id,
            workspace_id=workspace.id,
            app_id=app.id,
            operation_key="versioned_read",
            payload={},
            idempotency_key="same-read",
        )
        try:
            first = await dispatch.invoke_app_operation(**arguments)
            assert first["output"]["summary"] == "Old declared output"
            assert "_operation_contract" not in first
            register_app_operations(
                workspace.id,
                app.id,
                "reference",
                [{**spec, "handler_ref": "reference:replacement"}],
            )
            from app.api.errors import OperationIdempotencyConflictError

            expected_error = (
                OperationIdempotencyConflictError
                if operation_kind == "execute"
                else InsufficientPermissionsError
            )
            with pytest.raises(expected_error):
                await dispatch.invoke_app_operation(**arguments)
            assert calls == ["first"]
            if operation_kind == "execute":
                import json

                receipt = await postgres_raw_db.get(
                    "object", first["operation_receipt"]["id"]
                )
                assert receipt["context"]["status"] == "succeeded"
                stored = json.loads(receipt["context"]["result_json"])
                assert len(stored["_operation_contract"]) == 64
        finally:
            unregister_app_operations(workspace.id, app.id)
