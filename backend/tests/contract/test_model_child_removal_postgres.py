"""Model editing authority never permits deleting an unrelated model child."""

import pytest
from jvspatial.core.context import GraphContext, scoped_default_context_async

from app.exceptions import AuthorizationUnavailableError
from app.models.edges import CONTAINS
from app.models.nodes import App, EntryType, Tag
from app.services.agent_scope import current_scope_workspace_id
from app.services.app_graph import (
    ensure_app_attached_operational_model,
    ensure_track_attached_operational_model,
)
from app.services.operational_model_authoring import modify_operational_model
from app.services.resource_location import locate_resource
from tests.contract.test_staging_command_apply_postgres import proposal

pytestmark = [pytest.mark.contract, pytest.mark.postgres, pytest.mark.asyncio]


async def target_scope(track, scope):
    if scope == "track":
        return await ensure_track_attached_operational_model(track), {
            "track_id": track.id
        }
    location = await locate_resource("track", track.id)
    app = await App.get(location.app_id)
    return await ensure_app_attached_operational_model(app), {"app_id": app.id}


@pytest.mark.parametrize("kind,model", [("entry_type", EntryType), ("tag", Tag)])
@pytest.mark.parametrize("scope", ["track", "app"])
@pytest.mark.parametrize(
    "shape",
    [
        "foreign",
        "forged_cache",
        "ambiguous",
        "orphan",
        "conflicting_cache",
        "stale_parent",
    ],
)
async def test_removal_rejects_child_outside_unique_authorized_model(
    postgres_raw_db, kind, model, scope, shape
):
    async with scoped_default_context_async(GraphContext(database=postgres_raw_db)):
        auth, workspace, track, _ = await proposal()
        _, _, foreign_track, _ = await proposal()
        own_model, own_target = await target_scope(track, scope)
        foreign_model, foreign_target = await target_scope(foreign_track, scope)
        hints = (
            own_target
            if shape in {"forged_cache", "ambiguous", "stale_parent"}
            else foreign_target
        )
        child = await model.create(
            name="Private model child",
            **(
                {"track_id": foreign_track.id}
                if shape == "conflicting_cache"
                and scope == "app"
                and kind == "entry_type"
                else ({} if scope == "app" and kind == "entry_type" else hints)
            ),
        )
        if shape not in {"orphan", "conflicting_cache", "stale_parent"}:
            await foreign_model.connect(child, edge=CONTAINS)
        if shape in {"ambiguous", "conflicting_cache", "stale_parent"}:
            await own_model.connect(child, edge=CONTAINS)
        if shape == "stale_parent":
            await model.get(child.id)
            await own_model.disconnect(child, edge_type=CONTAINS)
            await foreign_model.connect(child, edge=CONTAINS)
        before_child = await postgres_raw_db.get("node", child.id)
        before_model = await postgres_raw_db.get("node", own_model.id)
        before_edges = await postgres_raw_db.find("edge", {}, sort=[("id", 1)])
        before_events = await postgres_raw_db.find(
            "object", {"entity": "OperationEventOutbox"}, sort=[("id", 1)]
        )
        reset = current_scope_workspace_id.set(workspace.id)
        try:
            result = await modify_operational_model(
                user_id=auth.id,
                **own_target,
                action=f"remove_{kind}",
                **{f"{kind}_id": child.id},
            )
        finally:
            current_scope_workspace_id.reset(reset)
        assert result.get("error") == "not_found", result
        assert child.id not in str(result) and child.name not in str(result)
        assert await postgres_raw_db.get("node", child.id) == before_child
        assert await postgres_raw_db.get("node", own_model.id) == before_model
        assert await postgres_raw_db.find("edge", {}, sort=[("id", 1)]) == before_edges
        assert (
            await postgres_raw_db.find(
                "object", {"entity": "OperationEventOutbox"}, sort=[("id", 1)]
            )
            == before_events
        )


@pytest.mark.parametrize("kind,model", [("entry_type", EntryType), ("tag", Tag)])
@pytest.mark.parametrize("scope", ["track", "app"])
async def test_removal_accepts_unique_child_of_authorized_model(
    postgres_raw_db, kind, model, scope
):
    async with scoped_default_context_async(GraphContext(database=postgres_raw_db)):
        auth, workspace, track, _ = await proposal()
        own_model, target = await target_scope(track, scope)
        child = await model.create(
            name="Owned model child",
            **({} if scope == "app" and kind == "entry_type" else target),
        )
        await own_model.connect(child, edge=CONTAINS)
        reset = current_scope_workspace_id.set(workspace.id)
        try:
            result = await modify_operational_model(
                user_id=auth.id,
                **target,
                action=f"remove_{kind}",
                **{f"{kind}_id": child.id},
            )
        finally:
            current_scope_workspace_id.reset(reset)
        assert not result.get("error"), result
        assert await postgres_raw_db.get("node", child.id) is None


@pytest.mark.parametrize("kind,model", [("entry_type", EntryType), ("tag", Tag)])
async def test_parent_read_outage_is_not_a_completed_denial(
    postgres_raw_db, monkeypatch, kind, model
):
    async with scoped_default_context_async(GraphContext(database=postgres_raw_db)):
        auth, workspace, track, _ = await proposal()
        own_model, target = await target_scope(track, "track")
        child = await model.create(name="Owned model child", track_id=track.id)
        await own_model.connect(child, edge=CONTAINS)
        find = postgres_raw_db.find

        async def unavailable(collection, query, **kwargs):
            if collection == "edge" and query.get("target") == child.id:
                assert kwargs["limit"] == 2
                raise RuntimeError("Injected parent read outage")
            return await find(collection, query, **kwargs)

        monkeypatch.setattr(postgres_raw_db, "find", unavailable)
        reset = current_scope_workspace_id.set(workspace.id)
        try:
            with pytest.raises(AuthorizationUnavailableError):
                await modify_operational_model(
                    user_id=auth.id,
                    **target,
                    action=f"remove_{kind}",
                    **{f"{kind}_id": child.id},
                )
        finally:
            current_scope_workspace_id.reset(reset)
        assert await postgres_raw_db.get("node", child.id) is not None
