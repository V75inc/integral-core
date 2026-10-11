"""Canonical execution scope must agree with primary graph containment."""

import pytest

from app.exceptions import AuthorizationUnavailableError
from app.models.edges import CONTAINS, IS_MEMBER_OF, OWNS
from app.models.nodes import App, Entry, Track, User, Workspace
from app.services.app_graph import (
    catalog_app,
    catalog_track,
    catalog_user,
    catalog_workspace,
)
from app.services.app_operations.context import OperationContext
from app.services.hooks.registry import ToolContext
from app.services.resource_location import locate_resource

pytestmark = pytest.mark.smoke


@pytest.mark.asyncio
@pytest.mark.parametrize("field", ["excluded_at", "excluded_by", "reason"])
async def test_malformed_optional_exclusion_metadata_does_not_restore_access(field):
    from app.middleware.permissions_cache import isolated_permissions_cache
    from app.models.edges import COLLABORATES_ON, EXCLUDED_FROM
    from app.services.permissions import resolve_role

    _, workspace, app, track, entry = await graph()
    reader = await User.create(display_name="Malformed deny QA")
    await catalog_user(reader)
    await reader.connect(workspace, edge=IS_MEMBER_OF, role="member")
    await reader.connect(app, edge=COLLABORATES_ON, role="editor")
    deny = await reader.connect(entry, edge=EXCLUDED_FROM)
    context = await reader.get_context()
    await context.database.find_one_and_update(
        "edge", {"id": deny.id}, {"$set": {f"context.{field}": {"malformed": True}}}
    )
    with isolated_permissions_cache():
        assert await resolve_role(reader.id, "entry", entry.id) is None
        from app.services.permissions import (
            count_user_accessible_entries,
            get_user_accessible_entries,
            get_user_accessible_entry_page,
        )

        assert await count_user_accessible_entries(reader.id, track.id) == 0
        assert await get_user_accessible_entries(reader.id, track_id=track.id) == []
        page = await get_user_accessible_entry_page(reader.id, track.id, limit=20)
        assert page["entries"] == [] and page["total"] == 0
    # The contract preserves a complete direct grant despite inherited deny.
    await reader.connect(entry, edge=COLLABORATES_ON, role="viewer")
    with isolated_permissions_cache():
        assert await resolve_role(reader.id, "entry", entry.id) == "viewer"
        assert await count_user_accessible_entries(reader.id, track.id) == 1
        page = await get_user_accessible_entry_page(reader.id, track.id, limit=20)
        assert [row.id for row in page["entries"]] == [entry.id]
        assert page["total"] == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("principal_form", ["graph", "auth"])
@pytest.mark.parametrize("identity_state", ["inactive", "missing"])
async def test_disabled_identity_cannot_use_either_principal_alias(
    principal_form, identity_state
):
    from jvspatial.api.auth.models import User as AuthUser

    from app.middleware.permissions_cache import isolated_permissions_cache
    from app.models.edges import COLLABORATES_ON
    from app.services.permissions import resolve_role

    _, workspace, app, _, _ = await graph()
    identity = await AuthUser.create(
        email="disable-scope@example.com", password_hash="test-fixture"
    )
    profile = await User.create(display_name="Identity QA", user_id=identity.id)
    await catalog_user(profile)
    await profile.connect(workspace, edge=IS_MEMBER_OF, role="member")
    await profile.connect(app, edge=COLLABORATES_ON, role="editor")
    principal = profile.id if principal_form == "graph" else identity.id
    with isolated_permissions_cache():
        assert await resolve_role(principal, "app", app.id) == "editor"
    if identity_state == "inactive":
        identity.is_active = False
        await identity.save()
    else:
        await identity.delete()
    with isolated_permissions_cache():
        assert await resolve_role(principal, "app", app.id) is None


async def graph():
    user = await User.create(display_name="Scope QA")
    await catalog_user(user)
    workspace = await Workspace.create(name="Scope QA")
    await catalog_workspace(workspace)
    await user.connect(workspace, edge=IS_MEMBER_OF, role="owner")
    app = await App.create(name="Scope QA", workspace_id=workspace.id)
    await catalog_app(app)
    await user.connect(app, edge=OWNS)
    track = await Track.create(title="Scope QA", workspace_id=workspace.id)
    await catalog_track(track)
    await user.connect(track, edge=OWNS)
    await app.connect(track, edge=CONTAINS)
    entry = await Entry.create(title="Scope QA", track_id=track.id)
    await track.connect(entry, edge=CONTAINS)
    return user, workspace, app, track, entry


@pytest.mark.asyncio
async def test_bound_facade_rejects_other_workspace_despite_legitimate_user_access():
    user, workspace, app, track, entry = await graph()
    context = ToolContext(
        user_id=user.id, workspace_id=workspace.id, scope=f"track:{track.id}"
    )
    assert (await context.get_entry(entry.id)).id == entry.id
    assert (await context.get({"id": entry.id}))["workspace_id"] == workspace.id
    other = await Workspace.create(name="Other")
    await catalog_workspace(other)
    await user.connect(other, edge=IS_MEMBER_OF, role="owner")
    elsewhere = ToolContext(
        user_id=user.id, workspace_id=other.id, scope=f"ws:{other.id}"
    )
    assert await elsewhere.get_entry(entry.id) is None
    assert await elsewhere.get({"id": entry.id}) is None
    operation = OperationContext(
        user_id=user.id, workspace_id=workspace.id, scope=f"app:{app.id}", app_id=app.id
    )
    assert (await operation.get_entry(entry.id)).id == entry.id
    operation.app_id = "different-app"
    assert await operation.get_entry(entry.id) is None


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "fault",
    [
        "missing_workspace",
        "track_mismatch",
        "second_track",
        "second_app",
        "inactive_app",
        "missing_catalog",
        "uncataloged_workspace",
        "foreign_track_registry",
        "foreign_app_registry",
        "foreign_track_workspace_parent",
        "foreign_app_workspace_parent",
    ],
)
async def test_canonical_location_rejects_corrupt_or_inactive_containment(fault):
    _, workspace, app, track, entry = await graph()
    assert await locate_resource("entry", entry.id) is not None
    context = await track.get_context()
    if fault == "missing_workspace":
        # Update behind warmed graph objects to reproduce a stale worker.
        await context.database.find_one_and_update(
            "node",
            {"id": track.id},
            {"$set": {"context.workspace_id": "missing-workspace"}},
        )
    elif fault == "track_mismatch":
        await context.database.find_one_and_update(
            "node", {"id": entry.id}, {"$set": {"context.track_id": "another-track"}}
        )
    elif fault == "second_track":
        other = await Track.create(title="Other", workspace_id=workspace.id)
        await catalog_track(other)
        await other.connect(entry, edge=CONTAINS)
    elif fault == "second_app":
        other = await App.create(name="Other", workspace_id=workspace.id)
        await catalog_app(other)
        await other.connect(track, edge=CONTAINS)
    elif fault == "inactive_app":
        await context.database.find_one_and_update(
            "node", {"id": app.id}, {"$set": {"context.lifecycle_state": "uninstalled"}}
        )
    elif fault.startswith("foreign_"):
        from app.models.edges import CATALOGS
        from app.models.nodes import Apps, Tracks
        from app.services.app_graph import _workspace_branch_id

        other = await Workspace.create(name="Foreign parent")
        await catalog_workspace(other)
        target = track if "track" in fault else app
        if fault.endswith("registry"):
            kind = "tracks" if "track" in fault else "apps"
            model = Tracks if kind == "tracks" else Apps
            branch = await model.find_one({"id": _workspace_branch_id(other.id, kind)})
            await branch.connect(target, edge=CATALOGS)
        else:
            await other.connect(target, edge=CONTAINS)
    elif fault == "uncataloged_workspace":
        from app.models.edges import CATALOGS
        from app.models.nodes import WORKSPACES_REGISTRY_ID

        edges = await context.find_edges_between(
            WORKSPACES_REGISTRY_ID, workspace.id, edge_class=CATALOGS
        )
        for edge in edges:
            await edge.delete()
    else:
        from app.models.edges import CATALOGS
        from app.services.app_graph import _workspace_branch_id

        edges = await context.find_edges_between(
            _workspace_branch_id(workspace.id, "tracks"), track.id, edge_class=CATALOGS
        )
        for edge in edges:
            await edge.delete()
    assert await locate_resource("entry", entry.id) is None


@pytest.mark.asyncio
async def test_scope_storage_failure_is_unavailable(monkeypatch):
    _, _, _, _, entry = await graph()

    async def fail(*args, **kwargs):
        raise RuntimeError("injected primary read failure")

    monkeypatch.setattr(Entry, "find_one", fail)
    with pytest.raises(AuthorizationUnavailableError):
        await locate_resource("entry", entry.id)


@pytest.mark.asyncio
async def test_parent_visit_fault_cannot_return_a_partial_location(monkeypatch):
    _, _, _, _, entry = await graph()

    async def fail(*args, **kwargs):
        raise RuntimeError("injected App primary read fault")

    monkeypatch.setattr(App, "find_one", fail)
    with pytest.raises(AuthorizationUnavailableError):
        await locate_resource("entry", entry.id)


@pytest.mark.asyncio
async def test_strongest_complete_role_and_direct_allow_survives_inherited_exclusion():
    from app.middleware.permissions_cache import isolated_permissions_cache
    from app.models.edges import COLLABORATES_ON, EXCLUDED_FROM
    from app.services.permissions import resolve_role

    owner, workspace, app, track, entry = await graph()
    reader = await User.create(display_name="Reader")
    await catalog_user(reader)
    await reader.connect(workspace, edge=IS_MEMBER_OF, role="member")
    await reader.connect(app, edge=COLLABORATES_ON, role="editor")
    await reader.connect(track, edge=COLLABORATES_ON, role="viewer")
    await reader.connect(entry, edge=COLLABORATES_ON, role="viewer")
    with isolated_permissions_cache():
        assert await resolve_role(reader.id, "track", track.id) == "editor"
        assert await resolve_role(reader.id, "entry", entry.id) == "editor"
    await reader.connect(entry, edge=EXCLUDED_FROM)
    with isolated_permissions_cache():
        assert await resolve_role(reader.id, "entry", entry.id) == "viewer"


@pytest.mark.asyncio
@pytest.mark.parametrize("role", ["viewer", "commenter"])
@pytest.mark.parametrize("principal_form", ["graph", "auth"])
async def test_authored_entry_edit_requires_commenter_and_normalizes_principal(
    role, principal_form
):
    from jvspatial.api.auth.models import User as AuthUser

    from app.middleware.permissions_cache import isolated_permissions_cache
    from app.models.edges import COLLABORATES_ON
    from app.services.permissions import can_edit_entry

    _, workspace, _, track, entry = await graph()
    identity = await AuthUser.create(
        email="author-scope@example.com", password_hash="test-fixture"
    )
    author = await User.create(display_name="Author", user_id=identity.id)
    await catalog_user(author)
    await author.connect(workspace, edge=IS_MEMBER_OF, role="member")
    await author.connect(entry, edge=COLLABORATES_ON, role=role)
    entry.author_id = author.id
    await entry.save()
    principal = author.id if principal_form == "graph" else identity.id
    with isolated_permissions_cache():
        assert await can_edit_entry(principal, entry.id) is (role == "commenter")


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "lookup", ["membership", "direct_ownership", "direct_collaboration"]
)
async def test_indeterminate_authority_does_not_activate_another_grant(
    monkeypatch, lookup
):
    from app.middleware.permissions_cache import isolated_permissions_cache
    from app.models.edges import COLLABORATES_ON
    from app.services.permissions import resolve_role

    user, _, _, _, entry = await graph()
    context = await entry.get_context()
    real_find = context.database.find
    failing_kind = {
        "membership": IS_MEMBER_OF,
        "direct_ownership": OWNS,
        "direct_collaboration": COLLABORATES_ON,
    }[lookup]

    async def fault(collection, query=None, *args, **kwargs):
        entity = (query or {}).get("entity")
        if collection == "edge" and (
            entity == failing_kind.__name__
            or (
                isinstance(entity, dict)
                and failing_kind.__name__ in entity.get("$in", [])
            )
        ):
            raise RuntimeError("injected authority-store failure")
        return await real_find(collection, query, *args, **kwargs)

    monkeypatch.setattr(context.database, "find", fault)
    with isolated_permissions_cache(), pytest.raises(AuthorizationUnavailableError):
        await resolve_role(user.id, "entry", entry.id)


@pytest.mark.asyncio
async def test_http_boundary_ignores_warm_process_role_and_stale_visibility(
    monkeypatch,
):
    from app.middleware.permissions_cache import PermissionsCacheMiddleware
    from app.services import permissions_process_cache as cache
    from app.services.permissions import resolve_role

    _, workspace, app, _, _ = await graph()
    reader = await User.create(display_name="HTTP reader")
    await catalog_user(reader)
    await reader.connect(workspace, edge=IS_MEMBER_OF, role="member")
    app.visibility = "public"
    await app.save()
    monkeypatch.setattr(cache, "_ENABLED", True)
    try:
        assert await resolve_role(reader.id, "app", app.id) == "viewer"
        assert cache.get_resolve_role_cached(reader.id, "app", app.id) == "viewer"
        context = await app.get_context()
        await context.database.find_one_and_update(
            "node", {"id": app.id}, {"$set": {"context.visibility": "private"}}
        )
        results = []

        async def inner(scope, receive, send):
            results.append(await resolve_role(reader.id, "app", app.id))

        async def receive():
            return {"type": "http.request", "body": b""}

        async def send(message):
            pass

        await PermissionsCacheMiddleware(inner)({"type": "http"}, receive, send)
        assert results == [None]
    finally:
        cache.invalidate_user(reader.id)


async def sibling(user, workspace):
    app = await App.create(name="Sibling", workspace_id=workspace.id)
    await catalog_app(app)
    await user.connect(app, edge=OWNS)
    track = await Track.create(title="Sibling", workspace_id=workspace.id)
    await catalog_track(track)
    await user.connect(track, edge=OWNS)
    await app.connect(track, edge=CONTAINS)
    entry = await Entry.create(title="Sibling", track_id=track.id)
    await track.connect(entry, edge=CONTAINS)
    return app, track, entry


@pytest.mark.asyncio
async def test_facade_discovery_relations_and_settings_stay_in_bound_app():
    from app.models.edges import REFERENCES

    user, workspace, app, track, entry = await graph()
    other_app, other_track, other_entry = await sibling(user, workspace)
    app.settings = {"marker": "local"}
    other_app.settings = {"marker": "foreign"}
    await app.save()
    await other_app.save()
    await entry.connect(other_entry, edge=REFERENCES)
    context = OperationContext(
        user_id=user.id, workspace_id=workspace.id, scope=f"app:{app.id}", app_id=app.id
    )
    assert [row.id for row in await context.find_entries({})] == [entry.id]
    assert await context.get_related_entries(entry.id) == []
    assert await context.get_entry_system(other_entry.id) is None
    assert await context.track_kind(other_track.id) == ""
    assert [row.id for row in await context._tracks_by_title("Scope QA")] == [track.id]
    assert await context._tracks_by_title("Sibling") == []
    assert await context.get_app_settings(other_app.name) == {}
    assert await context.get_app_settings(app.name) == {"marker": "local"}


@pytest.mark.asyncio
async def test_bound_query_filters_before_count_and_pagination():
    user, workspace, app, _, _ = await graph()
    other_app, _, _ = await sibling(user, workspace)
    context = OperationContext(
        user_id=user.id, workspace_id=workspace.id, scope=f"app:{app.id}", app_id=app.id
    )
    result = await context.query({"mode": "core_open", "resource": "app", "limit": 1})
    assert result["total_estimate"] == 1
    assert result["cursor"] is None
    assert [row["id"] for row in result["rows"]] == [app.id]
    assert other_app.id not in str(result)


@pytest.mark.asyncio
async def test_bound_declared_query_rejects_foreign_handler_before_invocation(
    monkeypatch,
):
    from app.api.errors import InsufficientPermissionsError
    from app.services.app_queries import dispatch

    user, workspace, app, _, _ = await graph()
    other_app, _, _ = await sibling(user, workspace)
    calls = []

    async def invoke(**kwargs):
        calls.append(kwargs)
        raise AssertionError("foreign handler was invoked")

    monkeypatch.setattr(dispatch, "invoke_app_query", invoke)
    context = OperationContext(
        user_id=user.id, workspace_id=workspace.id, scope="tool", app_id=app.id
    )
    with pytest.raises(InsufficientPermissionsError):
        await context.query(
            {
                "mode": "declared_capability",
                "capability_key": "test.query",
                "app_id": other_app.id,
            }
        )
    assert calls == []


@pytest.mark.asyncio
async def test_facade_live_read_ignores_warm_process_grant(monkeypatch):
    from app.models.edges import COLLABORATES_ON
    from app.services import permissions_process_cache as cache
    from app.services.permissions import resolve_role

    _, workspace, app, _, entry = await graph()
    reader = await User.create(display_name="Tool reader")
    await catalog_user(reader)
    await reader.connect(workspace, edge=IS_MEMBER_OF, role="member")
    grant = await reader.connect(app, edge=COLLABORATES_ON, role="viewer")
    monkeypatch.setattr(cache, "_ENABLED", True)
    try:
        assert await resolve_role(reader.id, "entry", entry.id) == "viewer"
        assert cache.get_resolve_role_cached(reader.id, "entry", entry.id) == "viewer"
        await grant.delete()
        context = ToolContext(
            user_id=reader.id, workspace_id=workspace.id, scope="tool"
        )
        assert await context.get_entry_system(entry.id) is None
        assert await context.find_entries({}) == []
    finally:
        cache.invalidate_user(reader.id)


@pytest.mark.asyncio
async def test_file_relationship_is_authoritative_and_rechecked_after_storage(
    monkeypatch,
):
    from app.models.edges import HAS_ATTACHMENT
    from app.models.nodes import Attachment
    from app.services import attachment_storage

    user, workspace, _, _, entry = await graph()
    _, _, other_entry = await sibling(user, workspace)
    attachment = await Attachment.create(filename="private.txt", storage_key="fixture")
    relationship = await other_entry.connect(attachment, edge=HAS_ATTACHMENT)
    entry.attachment_ids = [attachment.id]
    await entry.save()
    reads = []

    class Storage:
        async def read_attachment(self, key):
            reads.append(key)
            return b"private"

    monkeypatch.setattr(attachment_storage, "get_attachment_storage_service", Storage)
    context = ToolContext(user_id=user.id, workspace_id=workspace.id, scope="tool")
    assert await context.read_entry_file(entry.id, attachment.id) is None
    assert reads == []
    await relationship.delete()
    relationship = await entry.connect(attachment, edge=HAS_ATTACHMENT)
    assert await context.read_entry_file(entry.id, attachment.id) == (
        b"private",
        "private.txt",
    )

    class RevokingStorage:
        async def read_attachment(self, key):
            await relationship.delete()
            return b"private"

    monkeypatch.setattr(
        attachment_storage, "get_attachment_storage_service", RevokingStorage
    )
    assert await context.read_entry_file(entry.id, attachment.id) is None


@pytest.mark.asyncio
async def test_rollup_refuses_foreign_or_excluded_dependencies():
    from app.api.errors import InsufficientPermissionsError
    from app.models.edges import COLLABORATES_ON, EXCLUDED_FROM, REFERENCES

    user, workspace, app, track, plan = await graph()
    item = await Entry.create(
        title="Blocked", track_id=track.id, custom_fields={"status": "blocked"}
    )
    await track.connect(item, edge=CONTAINS)
    await plan.connect(item, edge=REFERENCES)
    context = OperationContext(
        user_id=user.id, workspace_id=workspace.id, scope="tool", app_id=app.id
    )
    assert await context.rollup_plan(plan.id) == {"open_count": 1, "at_risk": True}
    _, _, foreign = await sibling(user, workspace)
    await plan.connect(foreign, edge=REFERENCES)
    with pytest.raises(InsufficientPermissionsError):
        await context.rollup_plan(plan.id)
    reader = await User.create(display_name="Rollup reader")
    await catalog_user(reader)
    await reader.connect(workspace, edge=IS_MEMBER_OF, role="member")
    await reader.connect(app, edge=COLLABORATES_ON, role="viewer")
    await reader.connect(item, edge=EXCLUDED_FROM)
    context.user_id = reader.id
    with pytest.raises(InsufficientPermissionsError):
        await context.rollup_plan(plan.id)


@pytest.mark.asyncio
async def test_rollup_relationship_fault_is_unavailable(monkeypatch):
    user, workspace, app, _, entry = await graph()
    real_nodes = Entry.nodes

    async def fault(self, **kwargs):
        if kwargs.get("direction") == "out":
            raise RuntimeError("injected rollup relationship fault")
        return await real_nodes(self, **kwargs)

    monkeypatch.setattr(Entry, "nodes", fault)
    context = OperationContext(
        user_id=user.id, workspace_id=workspace.id, scope="tool", app_id=app.id
    )
    with pytest.raises(AuthorizationUnavailableError):
        await context.rollup_plan(entry.id)
