"""Actual-store policy scope and retained-context authorization regressions."""

from unittest.mock import patch

import pytest

from app.contracts.runtime import ExecutionScope
from app.exceptions import AuthorizationUnavailableError
from app.middleware.permissions_cache import isolated_permissions_cache
from app.models.edges import COLLABORATES_ON, CONTAINS, IS_MEMBER_OF
from app.modules.policy import PolicyModule
from app.schemas.policy import Resource
from tests.test_resource_location import graph

pytestmark = [pytest.mark.smoke, pytest.mark.asyncio]


async def shared_target(kind, principal_form="graph", role="viewer"):
    from jvspatial.api.auth.models import User as AuthUser

    caller, home, _, _, _ = await graph()
    _, workspace, app, track, entry = await graph()
    await caller.connect(workspace, edge=IS_MEMBER_OF, role="guest")
    target = {"app": app, "track": track, "entry": entry}[kind]
    grant = await caller.connect(target, edge=COLLABORATES_ON, role=role)
    identity = None
    if principal_form == "auth":
        identity = await AuthUser.create(
            email="policy-scope@example.com", password_hash="test-fixture"
        )
        caller.user_id = identity.id
        await caller.save()
    principal = identity.id if identity else caller.id
    scope = ExecutionScope.create(principal_id=principal, workspace_id=workspace.id)
    resource = Resource(kind=kind, id=target.id, scope=f"{kind}:{target.id}")
    return caller, home, workspace, app, track, entry, grant, identity, scope, resource


@pytest.mark.parametrize("kind", ["app", "track", "entry"])
@pytest.mark.parametrize("principal_form", ["graph", "auth"])
async def test_foreign_execution_workspace_denied_despite_legitimate_direct_grant(
    kind, principal_form
):
    _, home, _, _, _, _, _, _, scope, resource = await shared_target(
        kind, principal_form
    )
    module = PolicyModule()
    with isolated_permissions_cache():
        assert (
            await module.evaluate(scope=scope, action=f"{kind}.read", resource=resource)
        ).allowed
        foreign = ExecutionScope.create(
            principal_id=scope.principal_id, workspace_id=home.id
        )
        decision = await module.evaluate(
            scope=foreign, action=f"{kind}.read", resource=resource
        )
        assert not decision.allowed
        assert decision.reason == "canonical_execution_scope_mismatch"
        assert (
            await module.evaluate(scope=scope, action=f"{kind}.read", resource=resource)
        ).allowed


@pytest.mark.parametrize("kind", ["app", "track", "entry"])
async def test_revoked_grant_does_not_reuse_retained_module_allow(kind):
    _, _, _, _, _, _, grant, _, scope, resource = await shared_target(kind)
    module = PolicyModule()
    with isolated_permissions_cache():
        assert (
            await module.evaluate(scope=scope, action=f"{kind}.read", resource=resource)
        ).allowed
        await grant.delete()
        assert not (
            await module.evaluate(scope=scope, action=f"{kind}.read", resource=resource)
        ).allowed


@pytest.mark.parametrize("principal_form", ["graph", "auth"])
async def test_disabled_identity_rechecked_inside_retained_context(principal_form):
    caller, _, _, _, _, _, _, identity, scope, resource = await shared_target(
        "app", "auth"
    )
    if principal_form == "graph":
        scope = ExecutionScope.create(
            principal_id=caller.id, workspace_id=scope.workspace_id
        )
    module = PolicyModule()
    with isolated_permissions_cache():
        assert (
            await module.evaluate(scope=scope, action="app.read", resource=resource)
        ).allowed
        identity.is_active = False
        await identity.save()
        assert not (
            await module.evaluate(scope=scope, action="app.read", resource=resource)
        ).allowed


@pytest.mark.parametrize(
    "kind,parent_kind", [("track", "app"), ("entry", "track"), ("entry", "app")]
)
async def test_same_workspace_wrong_parent_cannot_borrow_authority(kind, parent_kind):
    from app.models.nodes import App, Track
    from app.services.app_graph import catalog_app, catalog_track

    owner, workspace, app, track, entry = await graph()
    other_app = await App.create(name="Other", workspace_id=workspace.id)
    await catalog_app(other_app)
    other_track = await Track.create(title="Other", workspace_id=workspace.id)
    await catalog_track(other_track)
    await other_app.connect(other_track, edge=CONTAINS)
    target = track if kind == "track" else entry
    correct_parent = app if parent_kind == "app" else track
    wrong_parent = other_app if parent_kind == "app" else other_track
    scope = ExecutionScope.create(principal_id=owner.id, workspace_id=workspace.id)
    module = PolicyModule()
    good = Resource(kind=kind, id=target.id, scope=f"{parent_kind}:{correct_parent.id}")
    bad = Resource(kind=kind, id=target.id, scope=f"{parent_kind}:{wrong_parent.id}")
    assert (
        await module.evaluate(scope=scope, action=f"{kind}.read", resource=good)
    ).allowed
    decision = await module.evaluate(scope=scope, action=f"{kind}.read", resource=bad)
    assert (
        not decision.allowed and decision.reason == "canonical_execution_scope_mismatch"
    )


async def test_empty_entry_creation_binds_existing_track_and_current_edit_authority():
    _, home, _, _, track, _, grant, _, scope, _ = await shared_target(
        "track", role="editor"
    )
    module = PolicyModule()
    resource = Resource(kind="entry", id="", scope=f"track:{track.id}")
    with isolated_permissions_cache():
        assert (
            await module.evaluate(scope=scope, action="entry.create", resource=resource)
        ).allowed
        foreign = ExecutionScope.create(
            principal_id=scope.principal_id, workspace_id=home.id
        )
        assert not (
            await module.evaluate(
                scope=foreign, action="entry.create", resource=resource
            )
        ).allowed
        await grant.delete()
        assert not (
            await module.evaluate(scope=scope, action="entry.create", resource=resource)
        ).allowed


@pytest.mark.parametrize(
    "claim",
    ["*", "app:", "user:any", "track: missing", "track:missing", "workspace:missing"],
)
async def test_missing_or_malformed_parent_fails_closed(claim):
    owner, workspace, _, _, entry = await graph()
    scope = ExecutionScope.create(principal_id=owner.id, workspace_id=workspace.id)
    decision = await PolicyModule().evaluate(
        scope=scope,
        action="entry.read",
        resource=Resource(kind="entry", id=entry.id, scope=claim),
    )
    assert (
        not decision.allowed and decision.reason == "canonical_execution_scope_mismatch"
    )


async def test_source_outage_does_not_reuse_retained_allow():
    _, _, _, _, _, _, _, _, scope, resource = await shared_target("entry")
    module = PolicyModule()
    with isolated_permissions_cache():
        assert (
            await module.evaluate(scope=scope, action="entry.read", resource=resource)
        ).allowed
        with patch(
            "app.services.resource_location.primary_node_record",
            side_effect=RuntimeError("source unavailable"),
        ):
            with pytest.raises(AuthorizationUnavailableError):
                await module.evaluate(
                    scope=scope, action="entry.read", resource=resource
                )


async def test_removed_named_parent_rechecked_despite_warm_entity_and_decision():
    _, _, _, _, track, entry, _, _, scope, resource = await shared_target("entry")
    module = PolicyModule()
    with isolated_permissions_cache():
        assert (
            await module.evaluate(scope=scope, action="entry.read", resource=resource)
        ).allowed
        context = await track.get_context()
        for edge in await context.find_edges_between(
            track.id, entry.id, edge_class=CONTAINS
        ):
            await edge.delete()
        assert not (
            await module.evaluate(scope=scope, action="entry.read", resource=resource)
        ).allowed


@pytest.mark.parametrize(
    "state,read_allowed,recovery_allowed",
    [
        ("active", True, True),
        ("paused", True, True),
        ("installing", False, True),
        ("awaiting_settings", False, True),
        ("failed", False, True),
        ("uninstalled", False, False),
    ],
)
async def test_location_management_states_do_not_expand_data_lifecycle(
    state, read_allowed, recovery_allowed
):
    owner, workspace, app, _, _ = await graph()
    app.lifecycle_state = state
    await app.save()
    scope = ExecutionScope.create(principal_id=owner.id, workspace_id=workspace.id)
    resource = Resource(kind="app", id=app.id, scope=f"app:{app.id}")
    module = PolicyModule()
    assert (
        await module.evaluate(scope=scope, action="app.read", resource=resource)
    ).allowed is read_allowed
    assert (
        await module.evaluate(scope=scope, action="app.update", resource=resource)
    ).allowed is recovery_allowed
