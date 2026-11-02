"""Thin admission envelopes cannot replace full payloads or current authority."""

import pytest
from jvspatial.core.context import get_default_context

from app.exceptions import AuthorizationUnavailableError
from app.models.edges import CONTAINS
from app.models.nodes import Track
from app.services import entry_access_projection as projection
from app.services.permissions import get_user_accessible_entry_page
from tests.test_resource_location import graph

pytestmark = [pytest.mark.smoke, pytest.mark.asyncio]


def install_projection_reader(monkeypatch, database):
    """Exercise the optional public API even against the published old adapter."""
    original = database.find
    requests = []

    async def reader(collection, query, *, fields, limit=None, sort=None):
        requests.append(fields)
        result = []
        for record in await original(collection, query, limit=limit, sort=sort):
            flat = {}
            for path in fields:
                value = record
                for segment in path.split("."):
                    if not isinstance(value, dict) or segment not in value:
                        break
                    value = value[segment]
                else:
                    flat[path] = value
            result.append(flat)
        return result

    monkeypatch.setattr(database, "find_projected", reader, raising=False)
    return requests


async def test_selected_projection_loads_complete_entry(monkeypatch):
    owner, workspace, _, track, entry = await graph()
    entry.body = "The full content must survive the metadata scan."
    await entry.save()
    requests = install_projection_reader(monkeypatch, get_default_context().database)
    result = await get_user_accessible_entry_page(
        owner.id, track.id, limit=20, workspace_id=workspace.id
    )
    assert result["total"] == 1
    assert [item.id for item in result["entries"]] == [entry.id]
    assert result["entries"][0].body == entry.body
    assert requests and all("context.body" not in fields for fields in requests)


async def test_projection_is_never_hydrated_with_model_defaults():
    with pytest.raises(AuthorizationUnavailableError):
        await projection.hydrate_records(
            [
                {
                    "id": "partial",
                    "entity": "Entry",
                    "context": {},
                    "__entry_projection": True,
                }
            ]
        )


async def test_selected_projection_rechecks_changed_containment(monkeypatch):
    owner, workspace, _, track, entry = await graph()
    install_projection_reader(monkeypatch, get_default_context().database)
    # A second current parent invalidates containment after metadata admission.
    alternate = await Track.create(title="Alternative", workspace_id=workspace.id)
    await workspace.connect(alternate, edge=CONTAINS)
    original = projection.admit_records
    changed = False

    async def admit_then_change(*args, **kwargs):
        nonlocal changed
        result = await original(*args, **kwargs)
        if result and result[0].get("__entry_projection") and not changed:
            changed = True
            await alternate.connect(entry, edge=CONTAINS)
        return result

    monkeypatch.setattr(projection, "admit_records", admit_then_change)
    with pytest.raises(AuthorizationUnavailableError):
        await get_user_accessible_entry_page(
            owner.id, track.id, limit=20, workspace_id=workspace.id
        )
    assert changed


@pytest.mark.parametrize(
    "damage", ["conflicting_track", "conflicting_visibility", "invalid_container"]
)
async def test_projection_preserves_legacy_authority_conflicts(monkeypatch, damage):
    owner, _, _, track, entry = await graph()
    database = get_default_context().database
    record = (await database.find("node", {"id": entry.id}))[0]
    if damage == "conflicting_track":
        record["track_id"] = "n.Track.wrong"
    elif damage == "conflicting_visibility":
        record["visibility"] = "private"
    else:
        record["track_id"] = track.id
        record["visibility"] = "inherit"
        record["context"] = "malformed"
    await database.save("node", record)
    install_projection_reader(monkeypatch, database)
    with pytest.raises(AuthorizationUnavailableError):
        # An ID query deliberately includes a malformed legacy record which
        # a context.track_id candidate predicate would otherwise omit.
        async for _ in projection.iter_records(
            owner.id, {"id": entry.id}, projection=[]
        ):
            pass
