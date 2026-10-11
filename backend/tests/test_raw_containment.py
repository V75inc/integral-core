"""Persisted containment candidates cannot disappear behind hydration defaults."""

import pytest

from app.exceptions import AuthorizationUnavailableError
from app.models.edges import CATALOGS, CONTAINS
from app.models.nodes import App, Workspace
from app.services.app_graph import _workspace_branch_id, catalog_app, catalog_workspace
from app.services.resource_location import locate_resource, locate_resources
from tests.test_resource_location import graph

pytestmark = [pytest.mark.smoke, pytest.mark.asyncio]


@pytest.fixture(params=["point", "batch"])
def resolve_entry(request):
    async def resolve(entry_id):
        if request.param == "batch":
            return (await locate_resources("entry", [entry_id]))[entry_id]
        return await locate_resource("entry", entry_id)

    return resolve


@pytest.mark.parametrize(
    "kind,field,value",
    [
        ("app", "lifecycle_state", None),
        ("app", "lifecycle_state", " ACTIVE "),
        ("app", "visibility", None),
        ("app", "visibility", "PUBLIC"),
        ("track", "visibility", None),
        ("track", "workspace_id", False),
        ("entry", "visibility", None),
        ("entry", "track_id", False),
        ("workspace", "kind", None),
    ],
)
async def test_missing_or_coerced_scope_facts_never_activate_model_defaults(
    kind, field, value, resolve_entry
):
    _, workspace, app, track, entry = await graph()
    target = {"workspace": workspace, "app": app, "track": track, "entry": entry}[kind]
    assert await resolve_entry(entry.id) is not None
    context = await target.get_context()
    patch = (
        {"$unset": {f"context.{field}": ""}}
        if value is None
        else {"$set": {f"context.{field}": value}}
    )
    await context.database.find_one_and_update("node", {"id": target.id}, patch)
    with pytest.raises(AuthorizationUnavailableError):
        await resolve_entry(entry.id)


async def test_unhydratable_extra_app_parent_cannot_make_a_track_unambiguous(
    resolve_entry,
):
    _, workspace, _, track, entry = await graph()
    other = await App.create(name="Malformed second parent", workspace_id=workspace.id)
    await catalog_app(other)
    await other.connect(track, edge=CONTAINS)
    context = await other.get_context()
    await context.database.find_one_and_update(
        "node", {"id": other.id}, {"$set": {"context.name": {"invalid": True}}}
    )
    assert await resolve_entry(entry.id) is None


async def test_malformed_foreign_catalog_edge_is_still_a_second_parent(resolve_entry):
    from app.models.nodes import Tracks

    _, _, _, track, entry = await graph()
    other = await Workspace.create(name="Foreign catalog")
    await catalog_workspace(other)
    branch = await Tracks.find_one({"id": _workspace_branch_id(other.id, "tracks")})
    extra = await branch.connect(track, edge=CATALOGS)
    context = await branch.get_context()
    await context.database.find_one_and_update(
        "edge", {"id": extra.id}, {"$set": {"context.cataloged_at": {"invalid": True}}}
    )
    assert await resolve_entry(entry.id) is None


async def test_optional_catalog_audit_metadata_does_not_erase_the_valid_path(
    resolve_entry,
):
    from app.models.nodes import Tracks

    _, workspace, _, track, entry = await graph()
    context = await track.get_context()
    branch = await Tracks.find_one({"id": _workspace_branch_id(workspace.id, "tracks")})
    edge = (await context.find_edges_between(branch.id, track.id, edge_class=CATALOGS))[
        0
    ]
    await context.database.find_one_and_update(
        "edge", {"id": edge.id}, {"$set": {"context.cataloged_at": {"invalid": True}}}
    )
    location = await resolve_entry(entry.id)
    assert location is not None and location.workspace_id == workspace.id
