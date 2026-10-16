"""Own history status uses current identity/workspace/delegation, not App data rights."""

import asyncio
import uuid
from contextvars import Context
from dataclasses import replace

import pytest
from jvspatial.core.context import (
    GraphContext,
    get_default_context,
    scoped_default_context_async,
)

from app.api.errors import InsufficientPermissionsError
from app.contracts.operations import OperationIdentity, canonical_request_hash
from app.contracts.runtime import ExecutionScope
from app.exceptions import AuthorizationUnavailableError
from app.schemas.policy import Subject
from app.services.app_operations.execution_receipts import (
    execute_operation_once,
    receipt_object_id,
)
from app.services.app_operations.history import observe_own_operation_outcome
from app.services.permissions import get_user_node, resolve_role
from app.services.policy_registry import create_policy
from tests.contract.test_operation_nested_result_postgres import operation_context

pytestmark = [pytest.mark.contract, pytest.mark.postgres, pytest.mark.asyncio]


async def fixture(kind="human", *, grant=True, app_bound=False):
    actor, workspace, app, _, _ = await operation_context()
    profile = await get_user_node(actor.id)
    service = policy = None
    subject = Subject(kind="human", id=actor.id)
    if kind == "agent":
        from app.agentive.edges import HAS_ORG_AGENT
        from app.agentive.nodes import AgentConfig
        from app.models.edges import CONTAINS

        service = await AgentConfig.create(
            scope="org_facing",
            facet="org_facing",
            workspace_id=workspace.id,
            is_active=True,
            app_id=app.id if app_bound else None,
        )
        await (app if app_bound else workspace).connect(
            service, edge=CONTAINS if app_bound else HAS_ORG_AGENT
        )
    elif kind == "connector":
        from app.agentive.edges import OWNS
        from app.agentive.nodes import Connector
        from app.models.edges import HAS_CONNECTOR

        service = await Connector.create(owner=profile.id, workspace_id=workspace.id)
        await profile.connect(service, edge=OWNS)
        await workspace.connect(service, edge=HAS_CONNECTOR)
    if service:
        subject = Subject(kind=kind, id=service.id)
        if grant:
            policy = await create_policy(
                subject_kind=kind,
                subject_id=service.id,
                scope=f"app:{app.id}" if app_bound else f"workspace:{workspace.id}",
                actions=["operation.history.read_own"],
                created_by=profile.id,
                requires_human_approval=False,
            )
    scope = ExecutionScope.create(
        principal_id=subject.id,
        workspace_id=workspace.id,
        origin="native_history_qualification",
    )
    return actor, profile, workspace, app, service, policy, scope, subject


def identity(scope, app, principal=None):
    return OperationIdentity.create(
        workspace_id=scope.workspace_id,
        app_id=app.id,
        operation_key="past_operation",
        principal_id=principal or scope.principal_id,
        idempotency_key="exact-private-key",
    )


async def record(scope, app, principal=None):
    async def handler():
        return {"output": {"private": "never released"}}

    return await execute_operation_once(
        identity=identity(scope, app, principal),
        request_hash=canonical_request_hash({}),
        execute=handler,
    )


async def observe(scope, subject, app):
    return await observe_own_operation_outcome(
        scope=scope,
        subject=subject,
        app_id=app.id,
        operation_key="past_operation",
        payload={},
        idempotency_key="exact-private-key",
    )


@pytest.mark.parametrize("kind", ["human", "agent", "connector"])
async def test_own_status_survives_app_data_revocation_without_output(
    postgres_raw_db, kind
):
    from app.models.edges import COLLABORATES_ON, EXCLUDED_FROM

    async with scoped_default_context_async(GraphContext(database=postgres_raw_db)):
        _, profile, _, app, _, _, scope, subject = await fixture(kind)
        await record(scope, app)
        before = await postgres_raw_db.get(
            "object", receipt_object_id(identity(scope, app))
        )
        await profile.connect(app, edge=EXCLUDED_FROM)
        context = await profile.get_context()
        for edge in await context.find_edges_between(
            profile.id, app.id, edge_class=COLLABORATES_ON
        ):
            await edge.delete()
        assert await resolve_role(profile.id, "app", app.id) is None
        result = await observe(scope, subject, app)
        assert result.reference()["id"] == before["id"]
        assert set(result.reference()) == {"id", "status", "replayed"}
        assert not hasattr(result, "result")
        assert await postgres_raw_db.get("object", before["id"]) == before


@pytest.mark.parametrize("kind", ["agent", "connector"])
async def test_service_never_observes_its_issuers_receipt(postgres_raw_db, kind):
    async with scoped_default_context_async(GraphContext(database=postgres_raw_db)):
        actor, _, _, app, _, _, scope, subject = await fixture(kind)
        await record(scope, app, actor.id)
        assert await observe(scope, subject, app) is None
        await record(scope, app)
        own = await observe(scope, subject, app)
        assert own.identity.principal_id == subject.id
        assert own.reference()["id"] != receipt_object_id(
            identity(scope, app, actor.id)
        )


@pytest.mark.parametrize("kind", ["agent", "connector"])
@pytest.mark.parametrize("failure", ["missing", "revoked", "review_required"])
async def test_service_history_requires_current_direct_history_delegation(
    postgres_raw_db, kind, failure
):
    async with scoped_default_context_async(GraphContext(database=postgres_raw_db)):
        _, _, _, app, _, policy, scope, subject = await fixture(
            kind, grant=failure != "missing"
        )
        await record(scope, app)
        if policy:
            if failure == "revoked":
                policy.is_active = False
            else:
                policy.requires_human_approval = True
            await policy.save()
        with pytest.raises(InsufficientPermissionsError):
            await observe(scope, subject, app)


@pytest.mark.parametrize("kind", ["human", "agent", "connector"])
async def test_offboarded_actor_or_issuer_cannot_read_history(postgres_raw_db, kind):
    from app.models.edges import IS_MEMBER_OF

    async with scoped_default_context_async(GraphContext(database=postgres_raw_db)):
        _, profile, workspace, app, _, _, scope, subject = await fixture(kind)
        await record(scope, app)
        context = await profile.get_context()
        for edge in await context.find_edges_between(
            profile.id, workspace.id, edge_class=IS_MEMBER_OF
        ):
            await edge.delete()
        from app.services.workspace_permissions import can_access_workspace

        assert await can_access_workspace(profile.id, workspace.id) == "none"
        with pytest.raises(InsufficientPermissionsError):
            await observe(scope, subject, app)


async def test_mismatched_bound_actor_never_selects_an_issuers_namespace(
    postgres_raw_db,
):
    async with scoped_default_context_async(GraphContext(database=postgres_raw_db)):
        actor, _, _, app, _, _, scope, subject = await fixture("agent")
        await record(scope, app, actor.id)
        with pytest.raises(InsufficientPermissionsError):
            await observe(replace(scope, principal_id=actor.id), subject, app)


async def test_app_bound_agent_uses_its_exact_root_and_grant(postgres_raw_db):
    async with scoped_default_context_async(GraphContext(database=postgres_raw_db)):
        _, _, _, app, _, _, scope, subject = await fixture("agent", app_bound=True)
        await record(scope, app)
        assert (await observe(scope, subject, app)).identity.principal_id == subject.id


@pytest.mark.parametrize("kind", ["agent", "connector"])
@pytest.mark.parametrize(
    "actions,policy_scope",
    [
        (["*"], "workspace"),
        (["workspace.read"], "workspace"),
        (["operation.history.read_own"], "*"),
    ],
)
async def test_existing_or_unbounded_grants_do_not_confer_history_access(
    postgres_raw_db, kind, actions, policy_scope
):
    async with scoped_default_context_async(GraphContext(database=postgres_raw_db)):
        _, profile, workspace, app, _, _, scope, subject = await fixture(
            kind, grant=False
        )
        await record(scope, app)
        await create_policy(
            subject_kind=kind,
            subject_id=subject.id,
            scope="*" if policy_scope == "*" else f"workspace:{workspace.id}",
            actions=actions,
            created_by=profile.id,
        )
        with pytest.raises(InsufficientPermissionsError):
            await observe(scope, subject, app)


@pytest.mark.parametrize("kind", ["agent", "connector"])
async def test_invalid_earlier_policy_does_not_hide_later_exact_history_grant(
    postgres_raw_db, kind
):
    async with scoped_default_context_async(GraphContext(database=postgres_raw_db)):
        _, profile, workspace, app, _, _, scope, subject = await fixture(
            kind, grant=False
        )
        await record(scope, app)
        await create_policy(
            subject_kind=kind,
            subject_id=subject.id,
            scope="*",
            actions=["operation.history.read_own"],
            created_by=profile.id,
        )
        await create_policy(
            subject_kind=kind,
            subject_id=subject.id,
            scope=f"workspace:{workspace.id}",
            actions=["operation.history.read_own"],
            created_by=profile.id,
            requires_human_approval=True,
        )
        await create_policy(
            subject_kind=kind,
            subject_id=subject.id,
            scope=f"workspace:{workspace.id}",
            actions=["operation.history.read_own"],
            created_by=profile.id,
            requires_human_approval=False,
        )
        assert (await observe(scope, subject, app)).identity.principal_id == subject.id


async def test_relinked_authentication_binding_cannot_read_previous_receipt(
    postgres_raw_db,
):
    from jvspatial.api.auth.models import User as AuthUser

    async with scoped_default_context_async(GraphContext(database=postgres_raw_db)):
        _, profile, _, app, _, _, scope, subject = await fixture()
        await record(scope, app)
        before = await postgres_raw_db.get(
            "object", receipt_object_id(identity(scope, app))
        )
        replacement = await AuthUser.create(
            email=f"history-relink-{uuid.uuid4().hex}@example.com",
            password_hash="fixture",
        )
        profile.user_id = replacement.id
        await profile.save()
        with pytest.raises(
            (InsufficientPermissionsError, AuthorizationUnavailableError)
        ):
            await observe(scope, subject, app)
        assert await postgres_raw_db.get("object", before["id"]) == before


@pytest.mark.parametrize("kind", ["human", "agent", "connector"])
async def test_disabled_authentication_actor_or_issuer_cannot_release_status(
    postgres_raw_db, kind
):
    from jvspatial.api.auth.models import User as AuthUser

    async with scoped_default_context_async(GraphContext(database=postgres_raw_db)):
        actor, _, _, app, _, _, scope, subject = await fixture(kind)
        await record(scope, app)
        auth = await AuthUser.get(actor.id)
        auth.is_active = False
        await auth.save()
        with pytest.raises(
            (InsufficientPermissionsError, AuthorizationUnavailableError)
        ):
            await observe(scope, subject, app)


@pytest.mark.parametrize("revocation_first", [False, True])
async def test_independent_membership_revocation_orders_own_history_release(
    postgres_raw_db, monkeypatch, revocation_first
):
    from jvspatial.db.postgres import PostgresTransaction

    from app.models.edges import IS_MEMBER_OF
    from app.services.app_operations import history
    from app.services.membership_write_scope import membership_write_scope
    from tests.contract.test_access_delegation_postgres import second_database

    other = await second_database()
    tasks = []
    entered, release, revoked = asyncio.Event(), asyncio.Event(), asyncio.Event()
    attempted = asyncio.Event()
    second_actor = {}
    try:
        async with scoped_default_context_async(GraphContext(database=postgres_raw_db)):
            actor, profile, workspace, app, _, _, scope, subject = await fixture()
            await record(scope, app)
            original_lookup = history.lookup_operation_outcome
            original_update = PostgresTransaction.find_one_and_update

            async def observed_update(transaction, collection, query, update, **kwargs):
                if (
                    asyncio.current_task() is second_actor.get("task")
                    and collection == "object"
                    and query.get("id") == actor.id
                ):
                    attempted.set()
                return await original_update(
                    transaction, collection, query, update, **kwargs
                )

            monkeypatch.setattr(
                PostgresTransaction, "find_one_and_update", observed_update
            )

            async def paused_lookup(**kwargs):
                entered.set()
                await release.wait()
                return await original_lookup(**kwargs)

            monkeypatch.setattr(history, "lookup_operation_outcome", paused_lookup)

            async def read():
                async with scoped_default_context_async(
                    GraphContext(database=postgres_raw_db)
                ):
                    return await observe(scope, subject, app)

            async def revoke():
                async with scoped_default_context_async(GraphContext(database=other)):
                    async with membership_write_scope(
                        workspace.id, principal_ids=(actor.id,), active_principal_ids=()
                    ):
                        graph = get_default_context()
                        for edge in await graph.find_edges_between(
                            profile.id, workspace.id, edge_class=IS_MEMBER_OF
                        ):
                            await edge.delete()
                        revoked.set()
                        if revocation_first:
                            await release.wait()

            first = asyncio.create_task(
                revoke() if revocation_first else read(), context=Context()
            )
            tasks.append(first)
            await asyncio.wait_for(
                (revoked if revocation_first else entered).wait(), timeout=15
            )
            second = asyncio.create_task(
                read() if revocation_first else revoke(), context=Context()
            )
            tasks.append(second)
            second_actor["task"] = second
            # Prove the independent actor reached the conflicting native Auth
            # row CAS; startup or an unstarted coroutine is not lock evidence.
            await asyncio.wait_for(attempted.wait(), timeout=15)
            with pytest.raises(asyncio.TimeoutError):
                await asyncio.wait_for(asyncio.shield(second), timeout=0.15)
            release.set()
            if revocation_first:
                await asyncio.wait_for(first, timeout=15)
                with pytest.raises(InsufficientPermissionsError):
                    await asyncio.wait_for(second, timeout=15)
            else:
                result = await asyncio.wait_for(first, timeout=15)
                assert result.reference()["status"] == "succeeded"
                await asyncio.wait_for(second, timeout=15)
                with pytest.raises(InsufficientPermissionsError):
                    await read()
    finally:
        release.set()
        for task in tasks:
            if not task.done():
                task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        await other.close()
