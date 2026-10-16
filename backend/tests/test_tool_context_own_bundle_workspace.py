"""A bundle tool updates its own App's records where the App is installed.

``_bundle_app_owning`` resolves the calling bundle's App in the *dispatcher's*
workspace (an organization workspace, say) and only for an entry that App owns.
It never lets a tool reach another App or another tenant.
"""

import pytest

from app.models.nodes import Workspace
from app.services.app_graph import catalog_workspace
from app.services.hooks.registry import ToolContext
from tests.test_resource_location import graph

pytestmark = [pytest.mark.smoke, pytest.mark.asyncio]

SLUG = "demo-bundle"


async def _installed(slug=SLUG, state="active"):
    user, workspace, app, track, entry = await graph()
    app.source_operational_model_slug = slug
    app.lifecycle_state = state
    await app.save()
    return user, workspace, app, entry


def _ctx(user, workspace, slug=SLUG):
    return ToolContext(
        user_id=user.id,
        workspace_id=workspace.id,
        scope=f"ws:{workspace.id}",
        bundle_slug=slug,
    )


async def test_finds_the_apps_own_record_in_the_workspace_it_is_installed_in():
    user, workspace, app, entry = await _installed()
    found = await _ctx(user, workspace)._bundle_app_owning(entry.id)
    assert found is not None and found.id == app.id


async def test_another_bundle_does_not_get_this_app():
    user, workspace, _, entry = await _installed()
    assert (
        await _ctx(user, workspace, slug="another-bundle")._bundle_app_owning(entry.id)
        is None
    )


async def test_the_record_must_belong_to_the_dispatchers_workspace():
    user, workspace, _, entry = await _installed()
    other = await Workspace.create(name="Other workspace")
    await catalog_workspace(other)
    assert await _ctx(user, other)._bundle_app_owning(entry.id) is None


async def test_a_paused_app_is_not_resolved():
    user, workspace, _, entry = await _installed(state="paused")
    assert await _ctx(user, workspace)._bundle_app_owning(entry.id) is None


async def test_missing_inputs_resolve_to_nothing_and_never_raise():
    user, workspace, _, entry = await _installed()
    assert await _ctx(user, workspace, slug="")._bundle_app_owning(entry.id) is None
    assert (
        await _ctx(user, workspace)._bundle_app_owning("o.Entry.does-not-exist") is None
    )
    assert await _ctx(user, workspace)._bundle_app_owning("") is None
