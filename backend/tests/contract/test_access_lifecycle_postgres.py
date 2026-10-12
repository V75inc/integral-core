"""Real PostgreSQL consent and source terminal units roll back after grant effects."""

import pytest
from jvspatial.core.context import GraphContext, scoped_default_context_async

from app.models.access_lifecycle import SHARE_GRANT, OwnershipProposal
from app.models.edges import IS_MEMBER_OF, OWNS
from app.services import ownership_consent, share_link_lifecycle
from app.services.permissions import resolve_role
from app.services.share_links import mint_share_link
from tests.test_access_lifecycle import verified_subject
from tests.test_resource_location import graph as _graph

pytestmark = [pytest.mark.asyncio, pytest.mark.postgres, pytest.mark.contract]


async def graph():
    from app.services.app_graph import ensure_integral_app_graph

    await ensure_integral_app_graph(include_library=False)
    return await _graph()


@pytest.mark.parametrize("kind", ["workspace", "app", "track"])
async def test_consent_transfer_acceptance_replay_and_unique_owner(
    postgres_raw_db, kind
):
    async with scoped_default_context_async(GraphContext(database=postgres_raw_db)):
        owner, workspace, app, track, _ = await graph()
        member = await verified_subject(workspace)
        resource = {"workspace": workspace, "app": app, "track": track}[kind]
        proposal = await ownership_consent.request_ownership_transfer(
            actor_id=owner.id,
            resource_type=kind,
            resource_id=resource.id,
            recipient_id=member.id,
        )
        accepted = await ownership_consent.respond_ownership_transfer(
            actor_id=member.id,
            proposal_id=proposal["proposal_id"],
            revision=0,
            action="accept",
        )
        assert (
            accepted["status"] == "accepted"
            and accepted["current_owner_id"] == member.id
        )
        retry = await ownership_consent.respond_ownership_transfer(
            actor_id=member.id,
            proposal_id=proposal["proposal_id"],
            revision=0,
            action="accept",
        )
        assert retry["changed"] is False
        family = IS_MEMBER_OF if kind == "workspace" else OWNS
        query = {"target": resource.id, "entity": family.__name__}
        if kind == "workspace":
            query["context.role"] = "owner"
        owners = await postgres_raw_db.find("edge", query)
        assert len(owners) == 1 and owners[0]["source"] == member.id
        assert (await postgres_raw_db.get("node", proposal["proposal_id"]))["context"][
            "status"
        ] == "accepted"
        if kind != "workspace":
            assert await resolve_role(member.id, kind, resource.id) == "owner"
            assert (
                await resolve_role(owner.id, kind, resource.id) == "editor"
            )  # governance remains a separate protected authority path


@pytest.mark.parametrize("kind", ["workspace", "app", "track"])
async def test_consent_audit_failure_rolls_back_owner_history_and_terminal(
    postgres_raw_db, monkeypatch, kind
):
    async with scoped_default_context_async(GraphContext(database=postgres_raw_db)):
        owner, workspace, app, track, _ = await graph()
        member = await verified_subject(workspace)
        resource = {"workspace": workspace, "app": app, "track": track}[kind]
        proposal = await ownership_consent.request_ownership_transfer(
            actor_id=owner.id,
            resource_type=kind,
            resource_id=resource.id,
            recipient_id=member.id,
        )
        before_edges = await postgres_raw_db.find(
            "edge", {"target": resource.id}, sort=[("id", 1)]
        )
        before_resource = await postgres_raw_db.get("node", resource.id)
        before_proposal = await postgres_raw_db.get("node", proposal["proposal_id"])

        async def fault(*args, **kwargs):
            raise RuntimeError("consent audit fault")

        monkeypatch.setattr(ownership_consent, "_record_change", fault)
        with pytest.raises(RuntimeError, match="consent audit fault"):
            await ownership_consent.respond_ownership_transfer(
                actor_id=member.id,
                proposal_id=proposal["proposal_id"],
                revision=0,
                action="accept",
            )
        assert await postgres_raw_db.get("node", resource.id) == before_resource
        assert (
            await postgres_raw_db.get("node", proposal["proposal_id"])
            == before_proposal
        )
        assert (
            await postgres_raw_db.find(
                "edge", {"target": resource.id}, sort=[("id", 1)]
            )
            == before_edges
        )


async def test_source_audit_fault_rolls_back_capacity_guest_and_source(
    postgres_raw_db, monkeypatch
):
    async with scoped_default_context_async(GraphContext(database=postgres_raw_db)):
        owner, workspace, app, _, _ = await graph()
        member = await verified_subject(workspace, None)
        minted = await mint_share_link(owner.id, "app", app.id, role="viewer")
        link_id = minted["share_link"]["id"]

        async def fault(*args, **kwargs):
            raise RuntimeError("source audit fault")

        monkeypatch.setattr(share_link_lifecycle, "_event", fault)
        with pytest.raises(RuntimeError, match="source audit fault"):
            await share_link_lifecycle.redeem_source_link(member.id, minted["token"])
        assert (await postgres_raw_db.get("node", link_id))["context"][
            "redemptions"
        ] == 0
        assert not await postgres_raw_db.find(
            "edge", {"target": member.id, "entity": SHARE_GRANT.__name__}
        )
        assert not await postgres_raw_db.find(
            "edge",
            {
                "source": member.id,
                "target": workspace.id,
                "entity": IS_MEMBER_OF.__name__,
            },
        )


async def test_link_retry_orders_behind_other_pool_source_revocation(
    postgres_raw_db, monkeypatch
):
    import asyncio

    from jvspatial.db.factory import create_database

    from app.api.errors import ResourceConflictError

    second = create_database(db_type="postgres", min_size=1, max_size=1)
    held, release, retrying = asyncio.Event(), asyncio.Event(), asyncio.Event()
    tasks = []
    try:
        async with scoped_default_context_async(GraphContext(database=postgres_raw_db)):
            owner, workspace, app, _, _ = await graph()
            member = await verified_subject(workspace, "guest")
            minted = await mint_share_link(owner.id, "app", app.id, role="viewer")
            first = await share_link_lifecycle.redeem_source_link(
                member.id, minted["token"]
            )
            original = share_link_lifecycle._event

            async def hold_event(*args, **kwargs):
                held.set()
                await asyncio.wait_for(release.wait(), timeout=15)
                return await original(*args, **kwargs)

            monkeypatch.setattr(share_link_lifecycle, "_event", hold_event)

            async def revoke():
                async with scoped_default_context_async(
                    GraphContext(database=postgres_raw_db)
                ):
                    return await share_link_lifecycle.revoke_source_grant(
                        actor_id=owner.id,
                        link_id=minted["share_link"]["id"],
                        recipient_id=member.id,
                    )

            async def retry():
                async with scoped_default_context_async(GraphContext(database=second)):
                    retrying.set()
                    return await share_link_lifecycle.redeem_source_link(
                        member.id, minted["token"]
                    )

            tasks.append(asyncio.create_task(revoke()))
            await asyncio.wait_for(held.wait(), timeout=15)
            tasks.append(asyncio.create_task(retry()))
            await asyncio.wait_for(retrying.wait(), timeout=15)
            with pytest.raises(asyncio.TimeoutError):
                await asyncio.wait_for(asyncio.shield(tasks[1]), timeout=0.15)
            release.set()
            await asyncio.wait_for(tasks[0], timeout=15)
            with pytest.raises(ResourceConflictError):
                await asyncio.wait_for(tasks[1], timeout=15)
            source = await postgres_raw_db.get("edge", first["grant_id"])
            assert source["context"]["state"] == "revoked"
            assert (await postgres_raw_db.get("node", minted["share_link"]["id"]))[
                "context"
            ]["redemptions"] == 1
    finally:
        release.set()
        for task in tasks:
            if not task.done():
                task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        await second.close()


async def test_consent_orders_behind_other_pool_suspension(
    postgres_raw_db, monkeypatch
):
    import asyncio

    from jvspatial.db.factory import create_database

    from app.api.errors import InsufficientPermissionsError
    from app.services import workspace_suspension

    second = create_database(db_type="postgres", min_size=1, max_size=1)
    held, release, accepting = asyncio.Event(), asyncio.Event(), asyncio.Event()
    tasks = []
    try:
        async with scoped_default_context_async(GraphContext(database=postgres_raw_db)):
            owner, workspace, app, _, _ = await graph()
            member = await verified_subject(workspace)
            proposal = await ownership_consent.request_ownership_transfer(
                actor_id=owner.id,
                resource_type="app",
                resource_id=app.id,
                recipient_id=member.id,
            )
            original = workspace_suspension._record_change

            async def hold_fact(*args, **kwargs):
                held.set()
                await asyncio.wait_for(release.wait(), timeout=15)
                return await original(*args, **kwargs)

            monkeypatch.setattr(workspace_suspension, "_record_change", hold_fact)

            async def suspend():
                async with scoped_default_context_async(
                    GraphContext(database=postgres_raw_db)
                ):
                    return await workspace_suspension.set_workspace_suspension(
                        actor_id=owner.id,
                        workspace_id=workspace.id,
                        member_id=member.id,
                        suspended=True,
                    )

            async def accept():
                async with scoped_default_context_async(GraphContext(database=second)):
                    accepting.set()
                    return await ownership_consent.respond_ownership_transfer(
                        actor_id=member.id,
                        proposal_id=proposal["proposal_id"],
                        revision=0,
                        action="accept",
                    )

            tasks.append(asyncio.create_task(suspend()))
            await asyncio.wait_for(held.wait(), timeout=15)
            tasks.append(asyncio.create_task(accept()))
            await asyncio.wait_for(accepting.wait(), timeout=15)
            with pytest.raises(asyncio.TimeoutError):
                await asyncio.wait_for(asyncio.shield(tasks[1]), timeout=0.15)
            release.set()
            await asyncio.wait_for(tasks[0], timeout=15)
            with pytest.raises(InsufficientPermissionsError):
                await asyncio.wait_for(tasks[1], timeout=15)
            assert (await postgres_raw_db.get("node", proposal["proposal_id"]))[
                "context"
            ]["status"] == "pending"
            rows = await postgres_raw_db.find(
                "edge", {"entity": OWNS.__name__, "target": app.id}
            )
            assert len(rows) == 1 and rows[0]["source"] == owner.id
    finally:
        release.set()
        for task in tasks:
            if not task.done():
                task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        await second.close()


async def _destination(owner):
    from app.models.nodes import Workspace
    from app.services.app_graph import catalog_workspace

    destination = await Workspace.create(name="Explicit rehome destination")
    await catalog_workspace(destination)
    await owner.connect(destination, edge=IS_MEMBER_OF, role="owner")
    return destination


async def test_explicit_rehome_preserves_canonical_roots_and_drops_foreign_access(
    postgres_raw_db,
):
    from app.models.edges import COLLABORATES_ON
    from app.services.resource_location import DATA_APP_STATES, locate_resource
    from app.services.workspace_rehome import rehome_workspace_resources

    async with scoped_default_context_async(GraphContext(database=postgres_raw_db)):
        owner, source, app, track, entry = await graph()
        recipient = await verified_subject(source)
        await recipient.connect(entry, edge=COLLABORATES_ON, role="editor")
        destination = await _destination(owner)
        result = await rehome_workspace_resources(
            actor_id=owner.id, source_id=source.id, destination_id=destination.id
        )
        assert result["outcome"] == "rehomed" and result["removed_foreign_grants"] == 1
        for kind, node in (("app", app), ("track", track), ("entry", entry)):
            assert (
                await locate_resource(kind, node.id, allowed_app_states=DATA_APP_STATES)
            ).workspace_id == destination.id
            assert await locate_resource(kind, node.id) is None
        assert await resolve_role(recipient.id, "entry", entry.id) is None
        assert (await postgres_raw_db.get("node", app.id))["context"][
            "lifecycle_state"
        ] == "paused"
        assert not await postgres_raw_db.find(
            "edge",
            {
                "source": recipient.id,
                "target": entry.id,
                "entity": COLLABORATES_ON.__name__,
            },
        )


async def test_rehome_audit_failure_rolls_back_scope_and_grant_removal(
    postgres_raw_db, monkeypatch
):
    from app.models.edges import COLLABORATES_ON
    from app.services.app_operations import event_outbox
    from app.services.workspace_rehome import rehome_workspace_resources

    async with scoped_default_context_async(GraphContext(database=postgres_raw_db)):
        owner, source, app, track, entry = await graph()
        recipient = await verified_subject(source)
        await recipient.connect(entry, edge=COLLABORATES_ON, role="viewer")
        destination = await _destination(owner)
        before_nodes = [
            await postgres_raw_db.get("node", node.id) for node in (app, track, entry)
        ]
        before_edges = await postgres_raw_db.find(
            "edge", {"target": {"$in": [app.id, track.id, entry.id]}}, sort=[("id", 1)]
        )

        async def fault(**kwargs):
            raise RuntimeError("rehome audit fault")

        monkeypatch.setattr(event_outbox, "insert_revision_event", fault)
        with pytest.raises(RuntimeError, match="rehome audit fault"):
            await rehome_workspace_resources(
                actor_id=owner.id, source_id=source.id, destination_id=destination.id
            )
        assert [
            await postgres_raw_db.get("node", node.id) for node in (app, track, entry)
        ] == before_nodes
        assert (
            await postgres_raw_db.find(
                "edge",
                {"target": {"$in": [app.id, track.id, entry.id]}},
                sort=[("id", 1)],
            )
            == before_edges
        )


async def test_implicit_nonempty_workspace_unlink_has_no_effects(postgres_raw_db):
    from app.api.errors import ResourceConflictError
    from app.services.workspace_lifecycle import delete_workspace_cascade

    async with scoped_default_context_async(GraphContext(database=postgres_raw_db)):
        owner, workspace, app, track, entry = await graph()
        before = [
            await postgres_raw_db.get("node", node.id)
            for node in (workspace, app, track, entry)
        ]
        with pytest.raises(ResourceConflictError):
            await delete_workspace_cascade(workspace, actor_id=owner.id)
        assert [
            await postgres_raw_db.get("node", node.id)
            for node in (workspace, app, track, entry)
        ] == before


async def test_workspace_cascade_fault_preserves_data_and_authority(
    postgres_raw_db, monkeypatch
):
    from app.services import app_deletion
    from app.services.workspace_lifecycle import delete_workspace_cascade

    async with scoped_default_context_async(GraphContext(database=postgres_raw_db)):
        owner, workspace, app, track, entry = await graph()
        original = app_deletion._safe_delete_node

        async def fault(node, label):
            if node.id == app.id:
                raise RuntimeError("cascade final-node fault")
            await original(node, label)

        monkeypatch.setattr(app_deletion, "_safe_delete_node", fault)
        before = [
            await postgres_raw_db.get("node", node.id)
            for node in (workspace, app, track, entry)
        ]
        before_edges = await postgres_raw_db.find(
            "edge",
            {"target": {"$in": [workspace.id, app.id, track.id, entry.id]}},
            sort=[("id", 1)],
        )
        with pytest.raises(RuntimeError, match="cascade final-node fault"):
            await delete_workspace_cascade(workspace, actor_id=owner.id, cascade=True)
        assert [
            await postgres_raw_db.get("node", node.id)
            for node in (workspace, app, track, entry)
        ] == before
        assert (
            await postgres_raw_db.find(
                "edge",
                {"target": {"$in": [workspace.id, app.id, track.id, entry.id]}},
                sort=[("id", 1)],
            )
            == before_edges
        )
