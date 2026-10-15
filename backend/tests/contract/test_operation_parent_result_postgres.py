"""Current canonical Entry parents govern operation comment/file references."""

import pytest
from jvspatial.core.context import GraphContext, scoped_default_context_async

from app.api.errors import InsufficientPermissionsError
from app.models.edges import HAS_ATTACHMENT, HAS_COMMENT
from app.models.nodes import Attachment, Comment
from app.services.app_operations.result_access import authorize_replayed_result
from tests.contract.test_operation_nested_result_postgres import operation_context

pytestmark = [pytest.mark.contract, pytest.mark.postgres, pytest.mark.asyncio]


async def child_reference(kind, entry, actor):
    if kind == "comment":
        child = await Comment.create(author_id=actor.id, text="Authorized note")
        await entry.connect(child, edge=HAS_COMMENT)
    else:
        child = await Attachment.create(
            filename="Authorized document.txt",
            scan_status="clean",
            owner_kind="entry",
            uploaded_by=actor.id,
        )
        await entry.connect(child, edge=HAS_ATTACHMENT)
    return child


@pytest.mark.parametrize("kind", ["comment", "attachment"])
async def test_readable_parent_owned_reference_is_released(postgres_raw_db, kind):
    async with scoped_default_context_async(GraphContext(database=postgres_raw_db)):
        actor, workspace, app, visible, _ = await operation_context()
        child = await child_reference(kind, visible, actor)
        await authorize_replayed_result(
            {
                "output": {
                    "source": {"kind": kind, "id": child.id},
                    "label": "Authorized source",
                }
            },
            user_id=actor.id,
            workspace_id=workspace.id,
            app_id=app.id,
        )


@pytest.mark.parametrize("kind", ["comment", "attachment"])
async def test_private_parent_owned_reference_withholds_whole_result(
    postgres_raw_db, kind
):
    async with scoped_default_context_async(GraphContext(database=postgres_raw_db)):
        actor, workspace, app, _, private = await operation_context()
        child = await child_reference(kind, private, actor)
        with pytest.raises(InsufficientPermissionsError):
            await authorize_replayed_result(
                {
                    "output": {
                        "source": {"kind": kind, "id": child.id},
                        "label": "Private source",
                    }
                },
                user_id=actor.id,
                workspace_id=workspace.id,
                app_id=app.id,
            )


@pytest.mark.parametrize("kind", ["comment", "attachment"])
@pytest.mark.parametrize("parent_state", ["orphan", "competing", "missing"])
async def test_unproven_parent_set_cannot_select_a_readable_first_parent(
    postgres_raw_db, kind, parent_state
):
    async with scoped_default_context_async(GraphContext(database=postgres_raw_db)):
        actor, workspace, app, visible, private = await operation_context()
        child = await child_reference(kind, visible, actor)
        edge_type = HAS_COMMENT if kind == "comment" else HAS_ATTACHMENT
        if parent_state == "competing":
            await private.connect(child, edge=edge_type)
        elif parent_state == "missing":
            await postgres_raw_db.delete("node", visible.id)
        else:
            edges = await postgres_raw_db.find(
                "edge",
                {
                    "source": visible.id,
                    "target": child.id,
                    "entity": edge_type._entity_name(),
                },
                limit=2,
            )
            assert len(edges) == 1
            await postgres_raw_db.delete("edge", edges[0]["id"])
        with pytest.raises(InsufficientPermissionsError):
            await authorize_replayed_result(
                {
                    "output": {
                        "source": {"kind": kind, "id": child.id},
                        "label": "Unproven source",
                    }
                },
                user_id=actor.id,
                workspace_id=workspace.id,
                app_id=app.id,
            )


@pytest.mark.parametrize("kind", ["comment", "attachment"])
async def test_parent_owned_reference_stays_in_selected_workspace(
    postgres_raw_db, kind
):
    from app.models.edges import COLLABORATES_ON, IS_MEMBER_OF
    from app.services.permissions import get_user_node, resolve_role

    async with scoped_default_context_async(GraphContext(database=postgres_raw_db)):
        actor, workspace, app, _, _ = await operation_context()
        _, other_workspace, other_app, other_entry, _ = await operation_context()
        profile = await get_user_node(actor.id)
        await profile.connect(other_workspace, edge=IS_MEMBER_OF, role="member")
        await profile.connect(other_app, edge=COLLABORATES_ON, role="viewer")
        assert await resolve_role(actor.id, "entry", other_entry.id) is not None
        child = await child_reference(kind, other_entry, actor)
        with pytest.raises(InsufficientPermissionsError):
            await authorize_replayed_result(
                {
                    "output": {
                        "source": {"kind": kind, "id": child.id},
                        "label": "Other workspace source",
                    }
                },
                user_id=actor.id,
                workspace_id=workspace.id,
                app_id=app.id,
            )


async def test_filed_attachment_may_retain_its_original_chat_link(postgres_raw_db):
    from app.models.edges import OWNS
    from app.models.nodes import ChatThread
    from app.services.permissions import get_user_node

    async with scoped_default_context_async(GraphContext(database=postgres_raw_db)):
        actor, workspace, app, visible, _ = await operation_context()
        child = await child_reference("attachment", visible, actor)
        thread = await ChatThread.create(user_id=actor.id, workspace_id=workspace.id)
        profile = await get_user_node(actor.id)
        await profile.connect(thread, edge=OWNS)
        await thread.connect(child, edge=HAS_ATTACHMENT)
        await authorize_replayed_result(
            {
                "output": {
                    "source": {"kind": "attachment", "id": child.id},
                    "label": "Filed source",
                }
            },
            user_id=actor.id,
            workspace_id=workspace.id,
            app_id=app.id,
        )


@pytest.mark.parametrize("state", ["chat", "blocked"])
async def test_non_entry_or_blocked_file_is_not_admitted_by_an_entry_edge(
    postgres_raw_db, state
):
    async with scoped_default_context_async(GraphContext(database=postgres_raw_db)):
        actor, workspace, app, visible, _ = await operation_context()
        child = await child_reference("attachment", visible, actor)
        if state == "chat":
            child.owner_kind = "chat"
        else:
            child.scan_status = "blocked"
        await child.save()
        with pytest.raises(InsufficientPermissionsError):
            await authorize_replayed_result(
                {"output": {"source": {"kind": "attachment", "id": child.id}}},
                user_id=actor.id,
                workspace_id=workspace.id,
                app_id=app.id,
            )


@pytest.mark.parametrize("kind", ["comment", "attachment"])
async def test_parent_read_outage_is_not_misreported_as_denied(
    postgres_raw_db, monkeypatch, kind
):
    from app.exceptions import AuthorizationUnavailableError
    from app.services.parent_owned_resource_scope import EntryOwnedResourceWalker

    async with scoped_default_context_async(GraphContext(database=postgres_raw_db)):
        actor, workspace, app, visible, _ = await operation_context()
        child = await child_reference(kind, visible, actor)
        original = EntryOwnedResourceWalker.parent_rows

        async def unavailable(self, node_id, edge, maximum, context):
            if node_id == child.id:
                raise OSError("Injected parent-store outage")
            return await original(self, node_id, edge, maximum, context)

        monkeypatch.setattr(EntryOwnedResourceWalker, "parent_rows", unavailable)
        with pytest.raises(AuthorizationUnavailableError):
            await authorize_replayed_result(
                {"output": {"source": {"kind": kind, "id": child.id}}},
                user_id=actor.id,
                workspace_id=workspace.id,
                app_id=app.id,
            )


@pytest.mark.parametrize("kind", ["comment", "attachment"])
@pytest.mark.parametrize("change", ["entry_revocation", "reparent"])
async def test_held_parent_change_orders_independent_result_release(
    postgres_raw_db, kind, change
):
    import asyncio
    from contextvars import Context

    from jvspatial.core.context import get_default_context

    from app.models.edges import COLLABORATES_ON, EXCLUDED_FROM
    from app.services.permissions import get_user_node
    from app.services.sharing_write_scope import sharing_write_scope
    from tests.contract.test_access_delegation_postgres import finish, second_database

    second, task = await second_database(), None
    try:
        async with scoped_default_context_async(GraphContext(database=postgres_raw_db)):
            actor, workspace, app, visible, private = await operation_context()
            child = await child_reference(kind, visible, actor)
            result = {
                "output": {
                    "source": {"kind": kind, "id": child.id},
                    "label": "Original source",
                }
            }
            await authorize_replayed_result(
                result,
                user_id=actor.id,
                workspace_id=workspace.id,
                app_id=app.id,
            )
            profile = await get_user_node(actor.id)
            started = asyncio.Event()

            async def release():
                async with scoped_default_context_async(GraphContext(database=second)):
                    started.set()
                    await authorize_replayed_result(
                        result,
                        user_id=actor.id,
                        workspace_id=workspace.id,
                        app_id=app.id,
                    )

            async with sharing_write_scope(workspace.id, principal_ids=(actor.id,)):
                database = get_default_context().database
                if change == "entry_revocation":
                    await profile.connect(visible, edge=EXCLUDED_FROM)
                    grants = await database.find(
                        "edge",
                        {
                            "source": profile.id,
                            "target": visible.id,
                            "entity": COLLABORATES_ON._entity_name(),
                        },
                        limit=2,
                    )
                    assert len(grants) == 1
                    await database.delete("edge", grants[0]["id"])
                else:
                    edge_type = HAS_COMMENT if kind == "comment" else HAS_ATTACHMENT
                    edges = await database.find(
                        "edge",
                        {
                            "source": visible.id,
                            "target": child.id,
                            "entity": edge_type._entity_name(),
                        },
                        limit=2,
                    )
                    assert len(edges) == 1
                    await database.delete("edge", edges[0]["id"])
                    await private.connect(child, edge=edge_type)
                task = asyncio.create_task(release(), context=Context())
                await asyncio.wait_for(started.wait(), timeout=15)
                with pytest.raises(asyncio.TimeoutError):
                    await asyncio.wait_for(asyncio.shield(task), timeout=0.15)
            with pytest.raises(InsufficientPermissionsError):
                await asyncio.wait_for(task, timeout=15)
    finally:
        await finish(second, task)


@pytest.mark.parametrize("kind", ["comment", "attachment"])
async def test_initial_and_cached_operation_outputs_share_parent_admission(
    postgres_raw_db, monkeypatch, kind
):
    from app.services.app_operations import dispatch
    from app.services.app_operations.registry import (
        register_app_operations,
        unregister_app_operations,
    )

    async with scoped_default_context_async(GraphContext(database=postgres_raw_db)):
        actor, workspace, app, visible, _ = await operation_context()
        child = await child_reference(kind, visible, actor)
        calls = 0

        async def handler(payload, context):
            nonlocal calls
            calls += 1
            return {"source": {"kind": kind, "id": child.id}, "label": "Current child"}

        register_app_operations(
            workspace.id,
            app.id,
            "reference",
            [{"key": "child_read", "kind": "read", "handler_ref": "reference:child"}],
        )
        monkeypatch.setattr(dispatch, "resolve_handler", lambda ref: handler)
        arguments = dict(
            user_id=actor.id,
            workspace_id=workspace.id,
            app_id=app.id,
            operation_key="child_read",
            payload={},
            idempotency_key="child-read",
        )
        try:
            first = await dispatch.invoke_app_operation(**arguments)
            assert first["output"]["source"]["id"] == child.id
            assert "_operation_contract" not in first
            await postgres_raw_db.delete("node", child.id)
            with pytest.raises(InsufficientPermissionsError):
                await dispatch.invoke_app_operation(**arguments)
            assert calls == 1
        finally:
            unregister_app_operations(workspace.id, app.id)


@pytest.mark.parametrize("kind", ["comment", "attachment"])
async def test_child_parent_candidate_queries_are_bounded(
    postgres_raw_db, monkeypatch, kind
):
    from app.services.parent_owned_resource_scope import EntryOwnedResourceWalker

    async with scoped_default_context_async(GraphContext(database=postgres_raw_db)):
        actor, workspace, app, visible, _ = await operation_context()
        child = await child_reference(kind, visible, actor)
        parent_reads = []
        parent_rows = EntryOwnedResourceWalker.parent_rows

        async def observe(self, node_id, edge, maximum, context):
            if node_id != child.id:
                return await parent_rows(self, node_id, edge, maximum, context)
            # Release uses the task-owned native transaction handle, not the
            # original pool adapter. Observe its actual query without replacing
            # the containment decision or the SQL read.
            database_type = type(context.database)
            find = database_type.find

            async def bounded(database, collection, query=None, *args, **kwargs):
                if collection == "edge" and query and query.get("target") == child.id:
                    parent_reads.append(kwargs.get("limit"))
                    assert kwargs.get("limit") == (2 if kind == "comment" else 3)
                return await find(database, collection, query, *args, **kwargs)

            with monkeypatch.context() as query_patch:
                query_patch.setattr(database_type, "find", bounded)
                return await parent_rows(self, node_id, edge, maximum, context)

        monkeypatch.setattr(EntryOwnedResourceWalker, "parent_rows", observe)
        await authorize_replayed_result(
            {"output": {"source": {"kind": kind, "id": child.id}}},
            user_id=actor.id,
            workspace_id=workspace.id,
            app_id=app.id,
        )
        assert parent_reads


@pytest.mark.parametrize("kind", ["comment", "attachment"])
@pytest.mark.parametrize("initial_parent_readable", [True, False])
async def test_committed_child_effect_is_not_repeated_when_parent_read_is_revoked(
    postgres_raw_db, monkeypatch, kind, initial_parent_readable
):
    from jvspatial.core.context import get_default_context

    from app.models.edges import (
        COLLABORATES_ON,
        EXCLUDED_FROM,
        HAS_APPLICATION_DEFINITION,
    )
    from app.models.nodes import ApplicationDefinition, Entry
    from app.services.app_operations import dispatch
    from app.services.app_operations.registry import (
        register_app_operations,
        unregister_app_operations,
    )
    from app.services.application_definitions import definition_fingerprint
    from app.services.authority_facts import primary_node_record
    from app.services.permissions import get_user_node, resolve_role

    async with scoped_default_context_async(GraphContext(database=postgres_raw_db)):
        actor, workspace, app, visible, private = await operation_context(role="editor")
        spec = {
            "key": "create_child",
            "kind": "execute",
            "handler_ref": "reference:create_child",
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
        created_id = ""

        async def handler(payload, context):
            nonlocal calls, created_id
            calls += 1
            graph = get_default_context()
            row = await primary_node_record(
                graph.database,
                Entry,
                visible.id if initial_parent_readable else private.id,
            )
            parent = await graph._deserialize_entity(Entry, row)
            child = await child_reference(kind, parent, actor)
            created_id = child.id
            return {"source": {"kind": kind, "id": child.id}, "label": "Created once"}

        register_app_operations(workspace.id, app.id, "reference", [spec])
        monkeypatch.setattr(dispatch, "resolve_handler", lambda ref: handler)
        arguments = dict(
            user_id=actor.id,
            workspace_id=workspace.id,
            app_id=app.id,
            operation_key="create_child",
            payload={},
            idempotency_key="one-child",
        )
        try:
            if not initial_parent_readable:
                from app.contracts.operations import OperationIdentity
                from app.services.app_operations.execution_receipts import (
                    receipt_object_id,
                )

                # A faulty trusted handler attempted a local write whose result
                # cannot be disclosed. No successful receipt or child may commit.
                with pytest.raises(InsufficientPermissionsError):
                    await dispatch.invoke_app_operation(**arguments)
                assert created_id and calls == 1
                assert await postgres_raw_db.get("node", created_id) is None
                identity = OperationIdentity.create(
                    workspace_id=workspace.id,
                    app_id=app.id,
                    operation_key="create_child",
                    principal_id=actor.id,
                    idempotency_key="one-child",
                )
                assert (
                    await postgres_raw_db.get("object", receipt_object_id(identity))
                    is None
                )
                return
            first = await dispatch.invoke_app_operation(**arguments)
            assert first["output"]["source"]["id"] == created_id
            original_child = await postgres_raw_db.get("node", created_id)
            assert original_child is not None
            profile = await get_user_node(actor.id)
            await profile.connect(visible, edge=EXCLUDED_FROM)
            grants = await postgres_raw_db.find(
                "edge",
                {
                    "source": profile.id,
                    "target": visible.id,
                    "entity": COLLABORATES_ON._entity_name(),
                },
                limit=2,
            )
            assert len(grants) == 1
            await postgres_raw_db.delete("edge", grants[0]["id"])
            assert await resolve_role(actor.id, "entry", visible.id) is None
            with pytest.raises(InsufficientPermissionsError):
                await dispatch.invoke_app_operation(**arguments)
            assert calls == 1
            assert await postgres_raw_db.get("node", created_id) == original_child
            receipt = await postgres_raw_db.get(
                "object", first["operation_receipt"]["id"]
            )
            assert receipt["context"]["status"] == "succeeded"
        finally:
            unregister_app_operations(workspace.id, app.id)
