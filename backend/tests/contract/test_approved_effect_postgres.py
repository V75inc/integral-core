"""Storage-level approved replay keeps current authority, exact effects and rollback."""

import asyncio

import pytest
from jvspatial.core.context import (
    GraphContext,
    get_default_context,
    scoped_default_context_async,
)

from app.agentive.edges import HAS_ORG_AGENT
from app.agentive.nodes import AgentConfig
from app.models.edges import HAS_APPROVAL_DECISION, OWNS
from app.schemas.policy import Resource, Subject
from app.services import approval_executor
from app.services.app_graph import ensure_integral_app_graph
from app.services.approved_effect import (
    approved_effect_principal,
    approved_effect_scope,
    fact_fingerprint,
)
from app.services.policy_engine import evaluate
from app.services.policy_registry import create_policy
from tests.test_resource_location import graph

pytestmark = [pytest.mark.asyncio, pytest.mark.contract, pytest.mark.postgres]


async def intent(action="entry.create"):
    """Mint a real rooted pending intent through current delegated policy."""
    await ensure_integral_app_graph(include_library=False)
    owner, workspace, app, track, entry = await graph()
    from app.services.entry_create import create_entry_in_track

    entry = await create_entry_in_track(
        track=track,
        user_id=owner.id,
        workspace_id=workspace.id,
        title="Existing approval entry",
    )
    agent = await AgentConfig.create(
        scope="org_facing",
        facet="org_facing",
        workspace_id=workspace.id,
        is_active=True,
    )
    await workspace.connect(agent, edge=HAS_ORG_AGENT)
    if action == "entry.create":
        payload = {"track_id": track.id, "title": "Approved exact effect"}
        resource = Resource(kind="entry", id="", scope=f"track:{track.id}")
    elif action in {"entry.update", "entry.delete", "comment.create"}:
        payload = {
            "entry_id": entry.id,
            **({"body": "Approved body"} if action != "entry.delete" else {}),
        }
        resource = Resource(kind="entry", id=entry.id, scope=f"track:{track.id}")
    elif action == "track.update":
        payload = {"title": "Unsupported deferred update"}
        resource = Resource(kind="track", id=track.id, scope=f"track:{track.id}")
    else:
        payload = {"workspace_id": workspace.id, "title": "Approved new resource"}
        resource = Resource(
            kind="app" if action == "app.create" else "track",
            id="",
            scope=f"workspace:{workspace.id}",
        )
    policy = await create_policy(
        subject_kind="agent",
        subject_id=agent.id,
        scope=resource.scope,
        actions=[action],
        requires_human_approval=True,
        created_by=owner.id,
    )
    decision = await evaluate(
        subject=Subject(kind="agent", id=agent.id),
        action=action,
        resource=resource,
        payload=payload,
    )
    assert not decision.allowed and decision.approval_id
    return (
        owner,
        workspace,
        app,
        track,
        entry,
        agent,
        policy,
        decision.approval_id,
        payload,
    )


@pytest.mark.parametrize("action", list(approval_executor.WRITE_ACTION_DISPATCH))
async def test_current_exact_approved_effect_six_supported_actions(
    postgres_raw_db, action
):
    """Each supported adapter stores its rooted effect once with a durable claim."""
    async with scoped_default_context_async(GraphContext(database=postgres_raw_db)):
        owner, workspace, _, _, entry, _, _, approval_id, _ = await intent(action)
        result = await approval_executor.execute_approval(
            approval_id=approval_id, approver_user_id=owner.id
        )
        assert isinstance(result, dict)
        row = await postgres_raw_db.get("node", approval_id)
        assert (
            row["context"]["status"] == "approved"
            and row["context"]["decision_revision"] == 1
        )
        assert (
            len(
                await postgres_raw_db.find(
                    "edge",
                    {
                        "entity": HAS_APPROVAL_DECISION._entity_name(),
                        "target": approval_id,
                    },
                )
            )
            == 1
        )
        with pytest.raises(approval_executor.ApprovalAlreadyDecided):
            await approval_executor.execute_approval(
                approval_id=approval_id, approver_user_id=owner.id
            )
        if action == "entry.delete":
            assert await postgres_raw_db.get("node", entry.id) is None
        elif action == "entry.update":
            assert (await postgres_raw_db.get("node", entry.id))["context"][
                "body"
            ] == "Approved body"
        elif action in {"app.create", "track.create"}:
            from app.services.resource_location import locate_resource

            identity = result["id"]
            location = await locate_resource(
                "app" if action == "app.create" else "track", identity
            )
            assert location is not None and location.workspace_id == workspace.id
            assert (
                len(
                    await postgres_raw_db.find(
                        "edge",
                        {
                            "entity": OWNS._entity_name(),
                            "source": owner.id,
                            "target": identity,
                        },
                    )
                )
                == 1
            )


async def test_original_delegator_revoke_denies_and_preserves_pending(postgres_raw_db):
    """A current approver cannot revive the issuer's removed target grant."""
    async with scoped_default_context_async(GraphContext(database=postgres_raw_db)):
        owner, _, app, track, entry, _, _, approval_id, _ = await intent("entry.update")
        for target in (app, track):
            for edge in await get_default_context().find_edges_between(
                owner.id, target.id, edge_class=OWNS
            ):
                await edge.delete()
        before = await postgres_raw_db.get("node", entry.id)
        from app.api.errors import InsufficientPermissionsError

        with pytest.raises(InsufficientPermissionsError):
            await approval_executor.execute_approval(
                approval_id=approval_id, approver_user_id=owner.id
            )
        assert await postgres_raw_db.get("node", entry.id) == before
        assert (await postgres_raw_db.get("node", approval_id))["context"][
            "status"
        ] == "pending"
        assert not await postgres_raw_db.find(
            "edge",
            {"entity": HAS_APPROVAL_DECISION._entity_name(), "target": approval_id},
        )


async def test_dispatch_failure_rolls_back_claim_graph_effect_and_decider(
    postgres_raw_db, monkeypatch
):
    """A fault after actual creation rolls back effect and all decision metadata."""
    async with scoped_default_context_async(GraphContext(database=postgres_raw_db)):
        owner, _, _, track, _, _, _, approval_id, _ = await intent()
        before = await postgres_raw_db.find(
            "node", {"entity": "Entry", "context.track_id": track.id}, sort=[("id", 1)]
        )
        real = approval_executor.WRITE_ACTION_DISPATCH["entry.create"]

        async def failing(**kwargs):
            await real(**kwargs)
            raise RuntimeError("after approved graph effect")

        monkeypatch.setitem(
            approval_executor.WRITE_ACTION_DISPATCH, "entry.create", failing
        )
        with pytest.raises(RuntimeError, match="after approved graph effect"):
            await approval_executor.execute_approval(
                approval_id=approval_id, approver_user_id=owner.id
            )
        assert (
            await postgres_raw_db.find(
                "node",
                {"entity": "Entry", "context.track_id": track.id},
                sort=[("id", 1)],
            )
            == before
        )
        assert (await postgres_raw_db.get("node", approval_id))["context"][
            "status"
        ] == "pending"
        assert not await postgres_raw_db.find(
            "edge",
            {"entity": HAS_APPROVAL_DECISION._entity_name(), "target": approval_id},
        )


async def test_exact_proof_rejects_changed_payload_and_child_task(postgres_raw_db):
    """A task-local exact approval cannot authorize a different mutation or task."""
    async with scoped_default_context_async(GraphContext(database=postgres_raw_db)):
        owner, _, _, track, _, agent, _, approval_id, payload = await intent()
        row = await postgres_raw_db.find_one_and_update(
            "node", {"id": approval_id}, {"$set": {"context.status": "approved"}}
        )
        from app.exceptions import AuthorizationUnavailableError

        async with approved_effect_scope(
            approval_id=approval_id,
            approval_fingerprint=fact_fingerprint(row),
            original_subject=Subject(kind="agent", id=agent.id),
            action="entry.create",
            resource=Resource(kind="entry", id="", scope=f"track:{track.id}"),
            payload=payload,
            delegator_id=owner.id,
        ):
            args = dict(
                actor_kind="agent",
                actor_id=agent.id,
                action="entry.create",
                resource_type="track",
                resource_id=track.id,
                payload=payload,
            )
            assert await approved_effect_principal(**args) == owner.id
            with pytest.raises(AuthorizationUnavailableError):
                await approved_effect_principal(
                    **{**args, "payload": {**payload, "body": "Injected body"}}
                )
            with pytest.raises(AuthorizationUnavailableError):
                await asyncio.create_task(approved_effect_principal(**args))


async def test_two_pools_approve_one_effect_once(postgres_raw_db, monkeypatch):
    """A second approver waits for the claim and observes the terminal outcome."""
    from contextvars import Context

    from jvspatial.db.factory import create_database

    second = create_database(db_type="postgres", min_size=1, max_size=1)
    held, release, following = asyncio.Event(), asyncio.Event(), asyncio.Event()
    tasks = []
    try:
        async with scoped_default_context_async(GraphContext(database=postgres_raw_db)):
            owner, _, _, track, _, _, _, approval_id, _ = await intent()
            for collection in ("node", "edge", "object"):
                await second.find(collection, {"id": ""}, limit=1)
            before = await postgres_raw_db.find(
                "node", {"entity": "Entry", "context.track_id": track.id}
            )
            real = approval_executor.WRITE_ACTION_DISPATCH["entry.create"]

            async def held_effect(**kwargs):
                result = await real(**kwargs)
                held.set()
                await asyncio.wait_for(release.wait(), timeout=15)
                return result

            monkeypatch.setitem(
                approval_executor.WRITE_ACTION_DISPATCH, "entry.create", held_effect
            )

            async def approve(db, signal=None):
                async with scoped_default_context_async(GraphContext(database=db)):
                    if signal:
                        signal.set()
                    return await approval_executor.execute_approval(
                        approval_id=approval_id, approver_user_id=owner.id
                    )

            tasks.append(
                asyncio.create_task(approve(postgres_raw_db), context=Context())
            )
            await asyncio.wait_for(held.wait(), timeout=15)
            tasks.append(
                asyncio.create_task(approve(second, following), context=Context())
            )
            await asyncio.wait_for(following.wait(), timeout=15)
            with pytest.raises(asyncio.TimeoutError):
                await asyncio.wait_for(asyncio.shield(tasks[1]), timeout=0.15)
            release.set()
            await asyncio.wait_for(tasks[0], timeout=15)
            with pytest.raises(approval_executor.ApprovalAlreadyDecided):
                await asyncio.wait_for(tasks[1], timeout=15)
            after = await postgres_raw_db.find(
                "node", {"entity": "Entry", "context.track_id": track.id}
            )
            assert len(after) == len(before) + 1
    finally:
        release.set()
        for task in tasks:
            if not task.done():
                task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        await second.close()


async def test_unsupported_approval_has_no_claim_or_effect(postgres_raw_db):
    """Unsupported deferred action stays pending rather than claiming success."""
    async with scoped_default_context_async(GraphContext(database=postgres_raw_db)):
        owner, _, _, track, _, _, _, approval_id, _ = await intent("track.update")
        before = await postgres_raw_db.get("node", track.id)
        with pytest.raises(approval_executor.UnsupportedApprovalAction):
            await approval_executor.execute_approval(
                approval_id=approval_id, approver_user_id=owner.id
            )
        assert await postgres_raw_db.get("node", track.id) == before
        assert (await postgres_raw_db.get("node", approval_id))["context"][
            "status"
        ] == "pending"


async def test_reject_emits_only_denial_and_never_original_effect(
    postgres_raw_db, monkeypatch
):
    """A valid current rejection has durable denial and no deferred mutation."""
    from app.services.app_operations import event_outbox

    async with scoped_default_context_async(GraphContext(database=postgres_raw_db)):
        owner, _, _, track, _, _, _, approval_id, _ = await intent()
        before = await postgres_raw_db.find(
            "node", {"entity": "Entry", "context.track_id": track.id}, sort=[("id", 1)]
        )
        captured = []
        original = event_outbox.insert_revision_event

        async def capture(**kwargs):
            captured.append(kwargs["event"])
            return await original(**kwargs)

        monkeypatch.setattr(event_outbox, "insert_revision_event", capture)
        result = await approval_executor.reject_approval(
            approval_id=approval_id,
            rejecter_user_id=owner.id,
            reason="Reviewed decline",
        )
        assert result["status"] == "rejected"
        assert captured[0]["action"] == "policy.deny"
        assert captured[0]["details"]["approval_id"] == approval_id
        assert (
            await postgres_raw_db.find(
                "node",
                {"entity": "Entry", "context.track_id": track.id},
                sort=[("id", 1)],
            )
            == before
        )
        assert (await postgres_raw_db.get("node", approval_id))["context"][
            "status"
        ] == "rejected"
