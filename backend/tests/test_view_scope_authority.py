"""Saved View scope uses current named ownership, never shared-model guesses."""

from unittest.mock import patch

import pytest

from app.contracts.runtime import ExecutionScope
from app.exceptions import AuthorizationUnavailableError
from app.middleware.permissions_cache import isolated_permissions_cache
from app.models.edges import (
    CATALOGS,
    COLLABORATES_ON,
    CONTAINS,
    HAS_OPERATIONAL_MODEL,
    HAS_VIEW,
    IS_MEMBER_OF,
)
from app.models.nodes import Track, View
from app.modules.policy import PolicyModule
from app.schemas.policy import Resource, Subject
from app.services.app_graph import (
    catalog_track,
    catalog_view_under_track,
    create_track_view,
)
from app.services.permissions import can_edit_view, can_view_view
from app.services.policy_engine import evaluate
from app.services.resource_location import locate_resource
from app.services.view_scope import list_scoped_track_views, read_scoped_view
from tests.test_resource_location import graph

pytestmark = [pytest.mark.smoke, pytest.mark.asyncio]


async def saved_graph():
    owner, workspace, app, track, _ = await graph()
    view = await create_track_view(
        track, name="Saved Scope QA", config={"secret": "fresh"}
    )
    return owner, workspace, app, track, view


@pytest.mark.parametrize("forged", [False, True])
async def test_profile_view_delete_uses_named_scope_before_effect(forged):
    from types import SimpleNamespace

    from app.api.errors import ResourceNotFoundError
    from app.api.operational_models import remove_view_from_track_profile

    owner, _, _, track, view = await saved_graph()
    if forged:
        _, _, _, _, view = await saved_graph()
        context = await view.get_context()
        await context.database.find_one_and_update(
            "node", {"id": view.id}, {"$set": {"context.track_id": track.id}}
        )
    request = SimpleNamespace(state=SimpleNamespace(user=owner), headers={})
    if forged:
        with pytest.raises(ResourceNotFoundError):
            await remove_view_from_track_profile(request, track.id, view.id)
        assert await View.get(view.id) is not None
    else:
        result = await remove_view_from_track_profile(request, track.id, view.id)
        assert result["deleted_view_id"] == view.id
        assert await View.get(view.id) is None


async def test_view_update_receipt_uses_current_raw_post_sync_result(monkeypatch):
    from types import SimpleNamespace
    from unittest.mock import AsyncMock

    from app.api import views as api

    owner, _, _, _, view = await saved_graph()
    context = await view.get_context()
    stale = await View.get(view.id)
    assert stale.config == {"secret": "fresh"}

    async def sync(_model):
        await context.database.find_one_and_update(
            "node",
            {"id": view.id},
            {"$set": {"context.config": {"post_sync": "current"}}},
        )

    monkeypatch.setattr(api, "_maybe_sync_attached_manifest", sync)
    monkeypatch.setattr(View, "get", AsyncMock(return_value=stale))
    request = SimpleNamespace(state=SimpleNamespace(user=owner), headers={})
    result = await api.update_view(request, view.id, name="Current receipt")
    assert result["view"]["config"] == {"post_sync": "current"}


async def test_profile_default_creation_cannot_clear_unmanaged_sibling_default():
    from types import SimpleNamespace

    from app.api.operational_models import add_view_to_track_profile
    from app.models.nodes import OperationalModel

    _, workspace, app, track, _ = await saved_graph()
    caller, _, _, _, _ = await graph()
    await caller.connect(workspace, edge=IS_MEMBER_OF, role="guest")
    await caller.connect(track, edge=COLLABORATES_ON, role="admin")
    model = await OperationalModel.get(track.attached_operational_model_id)
    sibling = await Track.create(
        title="Unmanaged sibling",
        workspace_id=workspace.id,
        attached_operational_model_id=model.id,
    )
    await sibling.connect(model, edge=HAS_OPERATIONAL_MODEL)
    await app.connect(sibling, edge=CONTAINS)
    await catalog_track(sibling)
    other = await create_track_view(sibling, name="Sibling default", is_default=True)
    assert not await can_edit_view(caller.id, other.id)
    request = SimpleNamespace(state=SimpleNamespace(user=caller), headers={})
    result = await add_view_to_track_profile(
        request, track.id, "Own new default", is_default=True
    )
    assert result["view"]["track_id"] == track.id
    assert (await read_scoped_view(other.id))[0].is_default is True


@pytest.mark.parametrize("corrupt", [False, True])
async def test_definition_view_evidence_requires_canonical_scope(corrupt):
    from app.models.edges import HAS_APPLICATION_DEFINITION
    from app.models.nodes import ApplicationDefinition
    from app.services.application_definitions import verify_definition_materialization

    _, workspace, app, track, view = await saved_graph()
    definition = await ApplicationDefinition.create(
        app_id=app.id,
        workspace_id=workspace.id,
        canonical_manifest={"app": {"tracks": [{"key": "qa", "name": track.title}]}},
        requirement_ledger=[
            {"id": "view:qa:saved", "kind": "view", "label": view.name}
        ],
    )
    await app.connect(definition, edge=HAS_APPLICATION_DEFINITION)
    if corrupt:
        context = await view.get_context()
        edges = await context.find_edges_between(track.id, view.id, edge_class=HAS_VIEW)
        assert len(edges) == 1
        await edges[0].delete()
    await verify_definition_materialization(app_node=app, definition=definition)
    evidence = definition.materialization_evidence[0]
    if corrupt:
        assert evidence["status"] == "not_evaluated" and evidence["references"] == []
    else:
        assert evidence["status"] == "verified" and evidence["references"] == [view.id]


@pytest.mark.parametrize(
    "target_state",
    ["in_scope", "foreign", "missing_scope", "corrupt_cache", "missing", "omitted"],
)
async def test_staging_view_target_requires_current_canonical_scope(
    target_state, monkeypatch
):
    from app.agentive import staging_executors as se
    from app.services.agent_scope import current_scope_workspace_id

    caller, home, _, _, _ = await graph()
    _, workspace, _, track, view = await saved_graph()
    await caller.connect(workspace, edge=IS_MEMBER_OF, role="guest")
    await caller.connect(track, edge=COLLABORATES_ON, role="admin")
    assert await can_edit_view(caller.id, view.id)
    if target_state == "missing_scope":
        context = await view.get_context()
        edges = await context.find_edges_between(track.id, view.id, edge_class=HAS_VIEW)
        assert len(edges) == 1
        for edge in edges:
            await edge.delete()
    elif target_state == "corrupt_cache":
        context = await view.get_context()
        await context.database.find_one_and_update(
            "node", {"id": view.id}, {"$set": {"context.track_id": "n.Track.invalid"}}
        )
    payload = {"view_id": view.id}
    if target_state == "missing":
        payload = {"view_id": "n.View.missing"}
    elif target_state == "omitted":
        payload = {}
    calls = []

    async def executor(user_id, data):
        calls.append((user_id, data))
        return {"ok": True}

    monkeypatch.setitem(se._EXECUTORS, "delete_view", executor)
    token = current_scope_workspace_id.set(
        home.id if target_state == "foreign" else workspace.id
    )
    try:
        result = await se.dispatch(
            user_id=caller.id, kind="delete_view", payload=payload
        )
    finally:
        current_scope_workspace_id.reset(token)
    if target_state == "in_scope":
        assert result == {"ok": True}
        assert calls == [(caller.id, payload)]
    else:
        assert result["error_code"] == "scope_violation"
        assert calls == []
        assert await View.get(view.id) is not None


@pytest.mark.parametrize("principal_form", ["graph", "auth"])
async def test_view_policy_binds_canonical_workspace_despite_legitimate_foreign_share(
    principal_form,
):
    from jvspatial.api.auth.models import User as AuthUser

    caller, home, _, _, _ = await graph()
    _, workspace, _, track, view = await saved_graph()
    if principal_form == "auth":
        identity = await AuthUser.create(
            email="view-scope@example.com", password_hash="fixture"
        )
        caller.user_id = identity.id
        await caller.save()
        principal = identity.id
    else:
        principal = caller.id
    await caller.connect(workspace, edge=IS_MEMBER_OF, role="guest")
    grant = await caller.connect(track, edge=COLLABORATES_ON, role="viewer")
    resource = Resource(kind="view", id=view.id, scope=f"view:{view.id}")
    module = PolicyModule()
    with isolated_permissions_cache():
        bound = ExecutionScope.create(principal_id=principal, workspace_id=workspace.id)
        assert (
            await module.evaluate(scope=bound, action="view.read", resource=resource)
        ).allowed
        wrong = ExecutionScope.create(principal_id=principal, workspace_id=home.id)
        assert not (
            await module.evaluate(scope=wrong, action="view.read", resource=resource)
        ).allowed
        await grant.delete()
        assert not (
            await module.evaluate(scope=bound, action="view.read", resource=resource)
        ).allowed


async def test_shared_model_does_not_merge_view_scope_or_defaults_between_tracks():
    owner, workspace, app, track, view = await saved_graph()
    sibling = await Track.create(
        title="Shared model sibling",
        workspace_id=workspace.id,
        attached_operational_model_id=track.attached_operational_model_id,
    )
    from app.models.nodes import OperationalModel

    model = await OperationalModel.get(track.attached_operational_model_id)
    await sibling.connect(model, edge=HAS_OPERATIONAL_MODEL)
    await app.connect(sibling, edge=CONTAINS)
    await catalog_track(sibling)
    other = await create_track_view(sibling, name="Sibling View", is_default=True)
    assert (await locate_resource("view", view.id)).track_id == track.id
    assert (await locate_resource("view", other.id)).track_id == sibling.id
    assert other.id not in {v.id for v in await list_scoped_track_views(track.id)}
    assert view.id not in {v.id for v in await list_scoped_track_views(sibling.id)}
    from app.services.operational_model_runtime import (
        synchronize_track_view_default_flags,
    )

    await synchronize_track_view_default_flags(track)
    assert (await read_scoped_view(other.id))[0].is_default is True
    resource = Resource(kind="view", id=view.id, scope=f"track:{sibling.id}")
    scope = ExecutionScope.create(principal_id=owner.id, workspace_id=workspace.id)
    assert not (
        await PolicyModule().evaluate(
            scope=scope, action="view.read", resource=resource
        )
    ).allowed


@pytest.mark.parametrize(
    "corruption",
    [
        "track_cache",
        "model_cache",
        "removed_scope",
        "duplicate_scope",
        "template_flag",
        "removed_catalog",
    ],
)
async def test_warm_view_and_direct_parent_rights_cannot_rescue_corrupt_scope(
    corruption,
):
    owner, _, _, track, view = await saved_graph()
    _, _, _, other_track, _ = await graph()
    context = await view.get_context()
    assert await can_view_view(owner.id, view.id)
    assert await View.get(view.id)
    if corruption in {"track_cache", "model_cache", "template_flag"}:
        field, value = {
            "track_cache": ("track_id", other_track.id),
            "model_cache": (
                "operational_model_id",
                other_track.attached_operational_model_id,
            ),
            "template_flag": ("is_template", True),
        }[corruption]
        await context.database.find_one_and_update(
            "node", {"id": view.id}, {"$set": {f"context.{field}": value}}
        )
    elif corruption == "duplicate_scope":
        await other_track.connect(view, edge=HAS_VIEW)
    else:
        edge_type = HAS_VIEW if corruption == "removed_scope" else CATALOGS
        edges = await context.database.find(
            "edge", {"target": view.id, "entity": edge_type._entity_name()}, limit=2
        )
        for edge in edges:
            await context.database.delete("edge", edge["id"])
    assert await read_scoped_view(view.id) is None
    assert not await can_view_view(owner.id, view.id)
    assert not await can_edit_view(owner.id, view.id)
    assert view.id not in {v.id for v in await list_scoped_track_views(track.id)}


async def test_view_payload_uses_current_raw_record_after_warming_node_cache():
    _, _, _, _, view = await saved_graph()
    assert (await View.get(view.id)).config["secret"] == "fresh"
    context = await view.get_context()
    await context.database.find_one_and_update(
        "node", {"id": view.id}, {"$set": {"context.config": {"secret": "current"}}}
    )
    assert (await read_scoped_view(view.id))[0].config == {"secret": "current"}


async def test_source_failure_is_unavailable_instead_of_false_permission():
    owner, _, _, _, view = await saved_graph()
    assert await can_view_view(owner.id, view.id)
    with patch(
        "app.services.resource_location.primary_node_record",
        side_effect=RuntimeError("unavailable"),
    ):
        with pytest.raises(AuthorizationUnavailableError):
            await can_view_view(owner.id, view.id)
        with pytest.raises(AuthorizationUnavailableError):
            await can_edit_view(owner.id, view.id)


@pytest.mark.parametrize(
    "role,allowed", [("viewer", False), ("editor", False), ("admin", True)]
)
async def test_saved_view_creation_requires_track_configuration_authority(
    role, allowed
):
    caller, _, _, _, _ = await graph()
    _, workspace, _, track, _ = await graph()
    await caller.connect(workspace, edge=IS_MEMBER_OF, role="guest")
    await caller.connect(track, edge=COLLABORATES_ON, role=role)
    resource = Resource(kind="view", id="", scope=f"track:{track.id}")
    scope = ExecutionScope.create(principal_id=caller.id, workspace_id=workspace.id)
    assert (
        await PolicyModule().evaluate(
            scope=scope, action="view.create", resource=resource
        )
    ).allowed is allowed


async def test_live_legacy_policy_does_not_reuse_allow_after_view_grant_revocation():
    caller, _, _, _, _ = await graph()
    _, workspace, _, track, view = await saved_graph()
    await caller.connect(workspace, edge=IS_MEMBER_OF, role="guest")
    grant = await caller.connect(track, edge=COLLABORATES_ON, role="viewer")
    resource = Resource(kind="view", id=view.id, scope=f"view:{view.id}")
    subject = Subject(kind="human", id=caller.id)
    with isolated_permissions_cache():
        assert (
            await evaluate(subject=subject, action="view.read", resource=resource)
        ).allowed
        await grant.delete()
        assert not (
            await evaluate(subject=subject, action="view.read", resource=resource)
        ).allowed


async def test_catalog_writer_does_not_reassign_an_existing_saved_view():
    _, _, _, _, view = await saved_graph()
    _, _, _, other, _ = await graph()
    with pytest.raises(AuthorizationUnavailableError):
        await catalog_view_under_track(other, view)


async def test_model_authoring_cannot_remove_a_view_from_another_track():
    owner, _, _, track, own_view = await saved_graph()
    _, _, _, _, foreign_view = await saved_graph()
    from app.services.operational_model_authoring import modify_operational_model

    result = await modify_operational_model(
        user_id=owner.id,
        track_id=track.id,
        action="remove_view",
        view_id=foreign_view.id,
    )
    assert result.get("error") == "not_found"
    assert (await read_scoped_view(foreign_view.id))[0].config == {"secret": "fresh"}
    positive = await modify_operational_model(
        user_id=owner.id, track_id=track.id, action="remove_view", view_id=own_view.id
    )
    assert positive.get("action") == "remove_view"
    assert await read_scoped_view(own_view.id) is None


async def test_app_model_view_authoring_checks_exact_catalog_membership():
    owner, _, app, _, _ = await graph()
    _, _, foreign_app, _, _ = await graph()
    from app.services.app_graph import get_app_attached_operational_model
    from app.services.operational_model_authoring import modify_operational_model
    from app.services.view_scope import get_model_scoped_view

    added = await modify_operational_model(
        user_id=owner.id, app_id=app.id, action="add_view", name="Owned model view"
    )
    view_id = added["view_id"]
    model = await get_app_attached_operational_model(app)
    other_model = await get_app_attached_operational_model(foreign_app)
    assert await get_model_scoped_view(view_id, model.id) is not None
    assert await get_model_scoped_view(view_id, other_model.id) is None
    assert not await can_view_view(owner.id, view_id)
    removed = await modify_operational_model(
        user_id=owner.id, app_id=app.id, action="remove_view", view_id=view_id
    )
    assert removed.get("action") == "remove_view"
