"""An App can read another App it declared ``requires_apps[].read: true`` (human only, read-only)."""

import pytest

from app.api.errors import InsufficientPermissionsError
from app.models.edges import IS_MEMBER_OF
from app.models.nodes import User, Workspace
from app.services import access_delegation
from app.services.app_graph import catalog_user
from app.services.app_operations.context import OperationContext
from app.services.declared_app_reads import invalidate_declared_reads
from tests.test_resource_location import graph, sibling

pytestmark = pytest.mark.smoke


def declare(monkeypatch, requires):
    """The source App's active definition lists ``requires`` as ``app.requires_apps``."""

    async def current_definition(app_id, *, execution=True):
        return {}, {
            "context": {
                "revision": 1,
                "canonical_manifest": {"app": {"requires_apps": requires}},
            }
        }

    monkeypatch.setattr(access_delegation, "current_definition", current_definition)
    invalidate_declared_reads()


def ctx(user, workspace, app, **kw):
    return OperationContext(
        user_id=user.id,
        workspace_id=workspace.id,
        scope=f"app:{app.id}",
        app_id=app.id,
        **kw,
    )


@pytest.mark.asyncio
async def test_declared_read_app_is_readable_and_stays_read_only(monkeypatch):
    user, workspace, app, track, entry = await graph()
    other_app, other_track, other_entry = await sibling(user, workspace)
    declare(monkeypatch, [{"key": other_app.name, "read": True}])
    base = ctx(user, workspace, app)
    # Without the bound context the other App is still invisible.
    assert await base.get_entry_system(other_entry.id) is None
    assert await base._tracks_by_title("Sibling") == []
    hr = await base.for_app(other_app.name)
    assert hr.app_id == other_app.id and hr.read_only is True
    assert (await hr.get_entry(other_entry.id)).id == other_entry.id
    assert [t.id for t in await hr._tracks_by_title("Sibling")] == [other_track.id]
    assert [row.id for row in await hr.find_entries({})] == [other_entry.id]
    # It is bound to the other App only: own records are not read through it.
    assert await hr.get_entry(entry.id) is None
    # The original context is unchanged.
    assert base.app_id == app.id and base.read_only is False
    # No writes through it.
    ok, reason = await hr.conditional_update_entry_fields(
        other_entry.id, state_field="state", expected_state=None, updates={"state": "x"}
    )
    assert (ok, reason) == (False, "read_only")
    assert (
        await hr.create_entry(
            track_id=other_track.id, entry_type_key="item", title="no"
        )
        is None
    )


@pytest.mark.asyncio
async def test_undeclared_or_read_false_is_refused(monkeypatch):
    user, workspace, app, _, _ = await graph()
    other_app, _, _ = await sibling(user, workspace)
    base = ctx(user, workspace, app)
    for requires in (
        [],
        [{"key": other_app.name}],
        [{"key": other_app.name, "read": False}],
        [{"key": other_app.name, "read": "true"}],
        [{"key": "Something Else", "read": True}],
    ):
        declare(monkeypatch, requires)
        with pytest.raises(InsufficientPermissionsError):
            await base.for_app(other_app.name)


@pytest.mark.asyncio
async def test_other_workspace_inactive_missing_and_self_are_refused(monkeypatch):
    user, workspace, app, _, _ = await graph()
    other_app, _, _ = await sibling(user, workspace)
    declare(
        monkeypatch,
        [{"key": other_app.name, "read": True}, {"key": app.name, "read": True}],
    )
    base = ctx(user, workspace, app)
    with pytest.raises(InsufficientPermissionsError):
        await base.for_app(app.name)  # not itself
    with pytest.raises(InsufficientPermissionsError):
        await base.for_app("Not Installed")
    other_app.lifecycle_state = "suspended"
    await other_app.save()
    invalidate_declared_reads()
    with pytest.raises(InsufficientPermissionsError):
        await base.for_app(other_app.name)
    other_app.lifecycle_state = "active"
    other_app.workspace_id = (await Workspace.create(name="Elsewhere")).id
    await other_app.save()
    invalidate_declared_reads()
    with pytest.raises(InsufficientPermissionsError):
        await base.for_app(other_app.name)


@pytest.mark.asyncio
async def test_viewer_without_target_access_reads_nothing(monkeypatch):
    user, workspace, app, _, _ = await graph()
    other_app, _, other_entry = await sibling(user, workspace)
    declare(monkeypatch, [{"key": other_app.name, "read": True}])
    stranger = await User.create(display_name="No HR access")
    await catalog_user(stranger)
    await stranger.connect(workspace, edge=IS_MEMBER_OF, role="guest")
    hr = await ctx(stranger, workspace, app).for_app(other_app.name)
    assert await hr.get_entry(other_entry.id) is None
    assert await hr.find_entries({}) == []


@pytest.mark.asyncio
async def test_agent_and_connector_actors_and_unbound_contexts_are_refused(monkeypatch):
    user, workspace, app, _, _ = await graph()
    other_app, _, _ = await sibling(user, workspace)
    declare(monkeypatch, [{"key": other_app.name, "read": True}])
    for kind in ("agent", "connector"):
        with pytest.raises(InsufficientPermissionsError):
            await ctx(user, workspace, app, actor_kind=kind).for_app(other_app.name)
    unbound = OperationContext(
        user_id=user.id, workspace_id=workspace.id, scope="x", app_id=""
    )
    with pytest.raises(InsufficientPermissionsError):
        await unbound.for_app(other_app.name)


def test_manifest_parser_keeps_read_only_when_literally_true():
    from app.services.operational_model_compile import _parse_manifest_requires_apps

    out = _parse_manifest_requires_apps(
        [
            {"key": "a", "read": True},
            {"key": "b"},
            {"key": "c", "read": "yes"},
            {"key": "d", "read": False, "optional": True},
        ]
    )
    assert [(d["key"], d["read"]) for d in out] == [
        ("a", True),
        ("b", False),
        ("c", False),
        ("d", False),
    ]
