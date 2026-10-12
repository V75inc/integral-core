"""Independent PostgreSQL delegation effect ordering and atomic rollback."""

import asyncio
from contextvars import Context
from types import SimpleNamespace
from typing import Any

import pytest
from jvspatial.api.auth.models import User as AuthUser
from jvspatial.core.context import (
    GraphContext,
    get_default_context,
    scoped_default_context_async,
)
from jvspatial.db.factory import create_database
from starlette.requests import Request

from app.api.errors import InsufficientPermissionsError
from app.models.access_delegation import AppActionBinding
from app.models.nodes import Policy
from app.schemas.policy import Resource, Subject
from app.services import access_delegation
from app.services.app_operation_authority import operation_permit, permits_mutation
from app.services.app_operations.transaction_scope import postgres_graph_transaction
from app.services.service_delegation import policy_delegation_allowed
from app.services.sharing_write_scope import sharing_write_scope
from tests.test_app_action_delegation import fixture as action_fixture

pytestmark = [pytest.mark.postgres, pytest.mark.contract, pytest.mark.asyncio]


async def fixture():
    from app.services.app_graph import ensure_integral_app_graph

    await ensure_integral_app_graph(include_library=False)
    return await action_fixture()


async def second_database():
    database = create_database(db_type="postgres", min_size=1, max_size=1)
    for collection in ("node", "edge", "object"):
        await database.find(collection, {"id": ""}, limit=1)
    return database


async def finish(database, task):
    if task is not None and not task.done():
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
    await database.close()


async def test_action_binding_audit_failure_rolls_back_node_and_attachment(
    postgres_raw_db, monkeypatch
):
    async with scoped_default_context_async(GraphContext(database=postgres_raw_db)):
        owner, reader, workspace, app, _, _, _ = await fixture()

        async def fail(*args: Any, **kwargs: Any) -> None:
            raise RuntimeError("delegation audit fault")

        monkeypatch.setattr(access_delegation, "_audit", fail)
        with pytest.raises(RuntimeError, match="delegation audit fault"):
            await access_delegation.create_binding(
                actor_id=owner.id,
                app_id=app.id,
                subject_kind="human",
                subject_id=reader.id,
                role="reviewer",
                conditions=[],
            )
        assert (
            await postgres_raw_db.find(
                "node",
                {"entity": AppActionBinding._entity_name(), "context.app_id": app.id},
            )
            == []
        )
        assert (
            await postgres_raw_db.find(
                "edge",
                {
                    "source": app.id,
                    "entity": access_delegation.HAS_ACTION_BINDING._entity_name(),
                },
            )
            == []
        )


async def test_held_group_revocation_prevents_later_exact_operation_effect(
    postgres_raw_db,
):
    second, task = await second_database(), None
    try:
        async with scoped_default_context_async(GraphContext(database=postgres_raw_db)):
            owner, reader, workspace, app, track, entry, _ = await fixture()
            group = await access_delegation.create_group(
                actor_id=owner.id, workspace_id=workspace.id, name="Reviewers"
            )
            await access_delegation.set_group_member(
                actor_id=owner.id, group_id=group.id, user_id=reader.id, member=True
            )
            await access_delegation.create_binding(
                actor_id=owner.id,
                app_id=app.id,
                subject_kind="group",
                subject_id=group.id,
                role="reviewer",
                conditions=[],
            )
            started = asyncio.Event()

            async def execute():
                async with scoped_default_context_async(GraphContext(database=second)):
                    started.set()
                    async with sharing_write_scope(
                        workspace.id, principal_ids=(reader.id,)
                    ):
                        async with operation_permit(
                            principal_id=reader.id,
                            workspace_id=workspace.id,
                            app_id=app.id,
                            operation_key="approve",
                            payload={"entry_id": entry.id},
                        ):
                            assert await permits_mutation(
                                workspace_id=workspace.id,
                                entry_type_key="request",
                                fields={"approval"},
                                entry_id=entry.id,
                                principal_id=reader.id,
                            )
                            await second.find_one_and_update(
                                "node",
                                {"id": entry.id},
                                {"$set": {"context.custom_fields.approval": "yes"}},
                            )

            async with sharing_write_scope(
                workspace.id, principal_ids=(owner.id, reader.id)
            ):
                await access_delegation.set_group_member(
                    actor_id=owner.id,
                    group_id=group.id,
                    user_id=reader.id,
                    member=False,
                )
                task = asyncio.create_task(execute(), context=Context())
                await asyncio.wait_for(started.wait(), timeout=15)
                with pytest.raises(asyncio.TimeoutError):
                    await asyncio.wait_for(asyncio.shield(task), timeout=0.15)
            with pytest.raises(InsufficientPermissionsError):
                await asyncio.wait_for(task, timeout=15)
            assert (
                not (await postgres_raw_db.get("node", entry.id))["context"]
                .get("custom_fields", {})
                .get("approval")
            )
    finally:
        await finish(second, task)


async def policy_fixture():
    from app.agentive.edges import HAS_ORG_AGENT
    from app.agentive.nodes import AgentConfig

    owner, reader, workspace, app, _, _, _ = await fixture()
    identity = await AuthUser.create(
        email=f"delegation-{owner.id}@example.com", password_hash="fixture"
    )
    owner.user_id = identity.id
    await owner.save()
    agent = await AgentConfig.create(
        scope="org_facing",
        facet="org_facing",
        workspace_id=workspace.id,
        is_active=True,
    )
    await workspace.connect(agent, edge=HAS_ORG_AGENT)
    request = Request(
        {"type": "http", "method": "POST", "path": "/api/policies", "headers": []}
    )
    request.state.user = SimpleNamespace(id=identity.id, roles=["user"])
    return owner, workspace, app, agent, request


async def test_policy_api_audit_failure_rolls_back_node_and_subject_edge(
    postgres_raw_db, monkeypatch
):
    from app.api import policies
    from app.models.edges import HAS_POLICY

    async with scoped_default_context_async(GraphContext(database=postgres_raw_db)):
        owner, _, app, agent, request = await policy_fixture()

        async def fail(**kwargs):
            raise RuntimeError("policy audit fault")

        monkeypatch.setattr(policies, "emit_policy_change", fail)
        with pytest.raises(RuntimeError, match="policy audit fault"):
            await policies.post_create_policy(
                request=request,
                subject_kind="agent",
                subject_id=agent.id,
                scope=f"app:{app.id}",
                actions=["app.read"],
            )
        assert (
            await postgres_raw_db.find(
                "node",
                {"entity": Policy._entity_name(), "context.subject_id": agent.id},
            )
            == []
        )
        assert (
            await postgres_raw_db.find(
                "edge", {"source": agent.id, "entity": HAS_POLICY._entity_name()}
            )
            == []
        )


async def test_held_delegator_disable_prevents_later_policy_creation(postgres_raw_db):
    from app.api import policies

    second, task = await second_database(), None
    try:
        async with scoped_default_context_async(GraphContext(database=postgres_raw_db)):
            owner, workspace, app, agent, request = await policy_fixture()
            started = asyncio.Event()

            async def create():
                async with scoped_default_context_async(GraphContext(database=second)):
                    started.set()
                    return await policies.post_create_policy(
                        request=request,
                        subject_kind="agent",
                        subject_id=agent.id,
                        scope=f"app:{app.id}",
                        actions=["app.read"],
                    )

            async with postgres_graph_transaction(postgres_raw_db) as transaction:
                await transaction.find_one_and_update(
                    "object",
                    {"id": owner.user_id},
                    {"$set": {"context.is_active": False}},
                )
                task = asyncio.create_task(create(), context=Context())
                await asyncio.wait_for(started.wait(), timeout=15)
                with pytest.raises(asyncio.TimeoutError):
                    await asyncio.wait_for(asyncio.shield(task), timeout=0.15)
            with pytest.raises(InsufficientPermissionsError):
                await asyncio.wait_for(task, timeout=15)
            assert (
                await postgres_raw_db.find(
                    "node",
                    {"entity": Policy._entity_name(), "context.subject_id": agent.id},
                )
                == []
            )
    finally:
        await finish(second, task)


async def test_held_policy_revocation_blocks_later_stale_policy_object(postgres_raw_db):
    from app.services.policy_registry import create_policy, update_policy

    second, task = await second_database(), None
    try:
        async with scoped_default_context_async(GraphContext(database=postgres_raw_db)):
            owner, workspace, app, agent, _ = await policy_fixture()
            policy = await create_policy(
                subject_kind="agent",
                subject_id=agent.id,
                scope=f"app:{app.id}",
                actions=["app.read"],
                created_by=owner.id,
            )
            started = asyncio.Event()

            async def execute():
                async with scoped_default_context_async(GraphContext(database=second)):
                    started.set()
                    async with sharing_write_scope(
                        workspace.id, principal_ids=(owner.id,)
                    ):
                        return await policy_delegation_allowed(
                            policy=policy,
                            subject=Subject(kind="agent", id=agent.id),
                            action="app.read",
                            resource=Resource(
                                kind="app", id=app.id, scope=f"app:{app.id}"
                            ),
                        )

            async with sharing_write_scope(workspace.id, principal_ids=(owner.id,)):
                await update_policy(policy.id, is_active=False)
                task = asyncio.create_task(execute(), context=Context())
                await asyncio.wait_for(started.wait(), timeout=15)
                with pytest.raises(asyncio.TimeoutError):
                    await asyncio.wait_for(asyncio.shield(task), timeout=0.15)
            assert await asyncio.wait_for(task, timeout=15) is False
    finally:
        await finish(second, task)


@pytest.mark.parametrize("revocation", ["identity", "workspace-owner"])
async def test_held_owner_revocation_prevents_inventory_release(
    postgres_raw_db, revocation
):
    from app.models.edges import IS_MEMBER_OF

    second, task = await second_database(), None
    try:
        async with scoped_default_context_async(GraphContext(database=postgres_raw_db)):
            owner, workspace, app, agent, _ = await policy_fixture()
            await access_delegation.create_group(
                actor_id=owner.id, workspace_id=workspace.id, name="Private directory"
            )
            started = asyncio.Event()

            async def release():
                async with scoped_default_context_async(GraphContext(database=second)):
                    started.set()
                    return await access_delegation.list_groups(
                        actor_id=owner.id, workspace_id=workspace.id
                    )

            holder = (
                postgres_graph_transaction(postgres_raw_db)
                if revocation == "identity"
                else sharing_write_scope(workspace.id, principal_ids=(owner.id,))
            )
            async with holder as transaction:
                if revocation == "identity":
                    await transaction.find_one_and_update(
                        "object",
                        {"id": owner.user_id},
                        {"$set": {"context.is_active": False}},
                    )
                else:
                    await get_default_context().database.find_one_and_update(
                        "edge",
                        {
                            "source": owner.id,
                            "target": workspace.id,
                            "entity": IS_MEMBER_OF._entity_name(),
                        },
                        {"$set": {"context.role": "member"}},
                    )
                task = asyncio.create_task(release(), context=Context())
                await asyncio.wait_for(started.wait(), timeout=15)
                with pytest.raises(asyncio.TimeoutError):
                    await asyncio.wait_for(asyncio.shield(task), timeout=0.15)
            with pytest.raises(InsufficientPermissionsError):
                await asyncio.wait_for(task, timeout=15)
    finally:
        await finish(second, task)


@pytest.mark.parametrize(
    "service_kind,approval_mode",
    [
        ("agent", "direct"),
        ("agent", "deferred"),
        ("agent", "revoked"),
        ("agent", "definition_changed"),
        ("connector", "direct"),
        ("connector", "deferred"),
    ],
)
async def test_service_exact_function_executes_viewer_approval_with_original_actor(
    postgres_raw_db, monkeypatch, approval_mode, service_kind
):
    """An agent consumes a viewer's exact function grant without generic Entry rights."""
    from pathlib import Path

    from app.agentive.edges import HAS_ORG_AGENT
    from app.agentive.nodes import AgentConfig
    from app.models.edges import AUTHORED_BY, COLLABORATES_ON, CONTAINS, IS_OF_TYPE
    from app.models.nodes import App, Entry
    from app.services.app_graph import ensure_integral_app_graph
    from app.services.app_lifecycle import install_app
    from app.services.app_operations.dispatch import invoke_app_operation
    from app.services.operational_model_loader import (
        load_library_operational_models_with_issues,
    )
    from app.services.policy_registry import create_policy
    from tests.fixtures.library_models import publish_library_spec
    from tests.fixtures.workspaces import make_org_workspace, workspace_owner_id
    from tests.test_access_lifecycle import verified_subject

    async with scoped_default_context_async(GraphContext(database=postgres_raw_db)):
        await ensure_integral_app_graph(include_library=False)
        package_dir = (
            Path(__file__).resolve().parents[3] / "examples" / "reference-review-queue"
        )
        monkeypatch.setenv("INTEGRAL_PACKAGE_PATHS", str(package_dir.parent))
        monkeypatch.setenv("INTEGRAL_CORE_ONLY", "0")
        specs, _ = load_library_operational_models_with_issues(
            package_paths=[str(package_dir.parent)],
            core_only=False,
            verify_signatures=False,
        )
        spec = next(spec for spec in specs if spec.slug == "reference-review-queue")
        library = await publish_library_spec(spec)
        workspace = await make_org_workspace("Service function scope")
        owner_id = await workspace_owner_id(workspace)
        installed = await install_app(
            workspace_id=workspace.id,
            library_cp_id=library.id,
            actor_id=owner_id,
            include_seed_data=False,
        )
        app = await App.get(installed["app_id"])
        issuer = await verified_subject(workspace)
        await issuer.connect(app, edge=COLLABORATES_ON, role="viewer")
        tracks = await app.nodes(edge=[CONTAINS], node=["Track"], direction="out")
        track = tracks[0]
        from app.services.entry_type_resolver import resolve_entry_type_id_by_key

        type_id = await resolve_entry_type_id_by_key(track.id, "submission")
        from app.models.nodes import EntryType, User

        entry_type = await EntryType.get(type_id)
        owner = await User.get(owner_id)
        entry = await Entry.create(
            title="Exact service request",
            track_id=track.id,
            type_id=type_id,
            custom_fields={"review_state": "pending"},
        )
        await track.connect(entry, edge=CONTAINS)
        await entry.connect(entry_type, edge=IS_OF_TYPE)
        await entry.connect(owner, edge=AUTHORED_BY)
        binding = await access_delegation.create_binding(
            actor_id=owner_id,
            app_id=app.id,
            subject_kind="human",
            subject_id=issuer.id,
            role="reviewer",
            conditions=[],
        )
        if service_kind == "agent":
            agent = await AgentConfig.create(
                scope="org_facing",
                facet="org_facing",
                workspace_id=workspace.id,
                is_active=True,
            )
            await workspace.connect(agent, edge=HAS_ORG_AGENT)
        else:
            from app.agentive.edges import OWNS
            from app.agentive.nodes import Connector
            from app.models.edges import HAS_CONNECTOR

            agent = await Connector.create(owner=issuer.id, workspace_id=workspace.id)
            await issuer.connect(agent, edge=OWNS)
            await workspace.connect(agent, edge=HAS_CONNECTOR)
        await create_policy(
            subject_kind=service_kind,
            subject_id=agent.id,
            scope=f"app:{app.id}",
            actions=[f"app:{app.id}:approve"],
            created_by=issuer.id,
            requires_human_approval=approval_mode != "direct",
        )
        result = await invoke_app_operation(
            user_id=owner_id,
            subject=Subject(kind=service_kind, id=agent.id),
            workspace_id=workspace.id,
            app_id=app.id,
            operation_key="approve",
            payload={"entry_id": entry.id},
            idempotency_key="service-exact-approve",
        )
        if approval_mode != "direct":
            from app.exceptions import AuthorizationUnavailableError
            from app.models.nodes import Approval
            from app.services.approval_executor import execute_approval

            assert result["output"]["status"] == "pending"
            approval_id = result["output"]["approval_id"]
            duplicate = await invoke_app_operation(
                user_id=owner_id,
                subject=Subject(kind=service_kind, id=agent.id),
                workspace_id=workspace.id,
                app_id=app.id,
                operation_key="approve",
                payload={"entry_id": entry.id},
                idempotency_key="service-exact-approve",
            )
            assert duplicate["output"]["approval_id"] == approval_id
            unchanged = await postgres_raw_db.get("node", entry.id)
            assert unchanged["context"]["custom_fields"]["review_state"] == "pending"
            if approval_mode == "revoked":
                await access_delegation.revoke_binding(
                    actor_id=owner_id, binding_id=binding.id
                )
                with pytest.raises(InsufficientPermissionsError):
                    await execute_approval(
                        approval_id=approval_id, approver_user_id=issuer.id
                    )
                assert (await postgres_raw_db.get("node", approval_id))["context"][
                    "status"
                ] == "pending"
                return
            if approval_mode == "definition_changed":
                approval = await Approval.get(approval_id)
                approval.app_operation = {
                    **approval.app_operation,
                    "definition_fingerprint": "stale-definition",
                }
                await approval.save()
                with pytest.raises(AuthorizationUnavailableError):
                    await execute_approval(
                        approval_id=approval_id, approver_user_id=issuer.id
                    )
                assert (await postgres_raw_db.get("node", entry.id))["context"][
                    "custom_fields"
                ]["review_state"] == "pending"
                from app.services.approval_executor import reject_approval

                await reject_approval(
                    approval_id=approval_id, rejecter_user_id=issuer.id
                )
                assert (await postgres_raw_db.get("node", approval_id))["context"][
                    "status"
                ] == "rejected"
                return
            result = await execute_approval(
                approval_id=approval_id, approver_user_id=issuer.id
            )
            assert (await postgres_raw_db.get("node", approval_id))["context"][
                "status"
            ] == "approved"
        assert result["output"]["changed"] is True
        receipt = await postgres_raw_db.get("object", result["operation_receipt"]["id"])
        assert receipt["context"]["principal_id"] == agent.id
        events = await postgres_raw_db.find(
            "object",
            {"entity": "OperationEventOutbox", "context.event.resource_id": entry.id},
            limit=10,
        )
        assert events and all(
            event["context"]["event"]["actor_kind"] == service_kind
            and event["context"]["event"]["actor_id"] == agent.id
            for event in events
        )
        current = await postgres_raw_db.get("node", entry.id)
        assert current["context"]["custom_fields"]["review_state"] == "approved"
        await access_delegation.revoke_binding(actor_id=owner_id, binding_id=binding.id)
        with pytest.raises(InsufficientPermissionsError):
            await invoke_app_operation(
                user_id=owner_id,
                subject=Subject(kind=service_kind, id=agent.id),
                workspace_id=workspace.id,
                app_id=app.id,
                operation_key="approve",
                payload={"entry_id": entry.id},
                idempotency_key="service-exact-revoked",
            )
