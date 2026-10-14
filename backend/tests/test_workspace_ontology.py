"""Ontology visibility against real rooted graphs and authenticated routes."""

import json

import pytest

from app.api.errors import InsufficientPermissionsError
from app.models.edges import (
    COLLABORATES_ON,
    CONTAINS,
    EXCLUDED_FROM,
    HAS_OPERATIONAL_MODEL,
    IS_MEMBER_OF,
    OWNS,
)
from app.models.nodes import App, EntryType, OperationalModel, Track
from app.schemas.workspace_ontology import OntologyRequest
from app.services.app_graph import catalog_app, catalog_track
from app.services.personal_workspace import ensure_personal_workspace
from app.services.workspace_ontology import capture_ontology, list_ontology_roots

pytestmark = pytest.mark.smoke


@pytest.fixture
async def ontology_graph(test_user, test_user2):
    workspace = await ensure_personal_workspace(test_user)
    await test_user2.connect(workspace, edge=IS_MEMBER_OF, role="guest")
    app = await App.create(name="Knowledge", workspace_id=workspace.id)
    await workspace.connect(app, edge=CONTAINS)
    await catalog_app(app)
    await test_user.connect(app, edge=OWNS)
    await test_user2.connect(app, edge=COLLABORATES_ON, role="viewer")
    track = await Track.create(title="Decisions", workspace_id=workspace.id)
    await app.connect(track, edge=CONTAINS)
    await catalog_track(track)
    model = await OperationalModel.create(
        name="Decision model", workspace_id=workspace.id
    )
    await track.connect(model, edge=HAS_OPERATIONAL_MODEL)
    track.attached_operational_model_id = model.id
    await track.save()
    kind = await EntryType.create(
        name="Decision",
        track_id=track.id,
        form_schema={
            "fields": [
                {
                    "key": "summary",
                    "label": "Summary",
                    "type": "text",
                    "required": True,
                },
                {"key": "internal", "label": "Hidden schema", "hidden": True},
                {
                    "key": "related",
                    "type": "relation",
                    "target_track_id": "secret-target",
                    "default": "secret-default",
                },
            ],
            "secret_schema_config": "secret-config",
        },
    )
    await model.connect(kind, edge=CONTAINS)
    return workspace, app, track, model, kind, test_user, test_user2


@pytest.fixture
async def ontology(ontology_graph, tmp_path):
    """Check complete semantics on a native store, separate from JSON timing."""
    from jvspatial.core import GraphContext
    from jvspatial.core.context import get_default_context, scoped_default_context
    from jvspatial.db import get_database_manager
    from jvspatial.db.sqlite import SQLiteDB

    source = get_default_context().database
    database = SQLiteDB(db_path=tmp_path / "ontology.db")
    context = GraphContext(database)
    manager = get_database_manager()
    original_prime = manager.get_prime_database()
    originals = []
    try:
        for collection in ("node", "edge", "object"):
            await database.bulk_save(collection, await source.find(collection, {}))
        for node in ontology_graph:
            originals.append((node, await node.get_context()))
            await node.set_context(context)
        manager.set_prime_database(database)
        with scoped_default_context(context):
            yield ontology_graph
    finally:
        manager.set_prime_database(original_prime)
        for node, original in originals:
            await node.set_context(original)
        await database.close()


async def capture(graph, root_id=None, user=None, depth=4):
    workspace, _, _, _, _, _, guest = graph
    return await capture_ontology(
        principal_id=(user or guest).id,
        workspace_id=workspace.id,
        spec=OntologyRequest(root_id=root_id, max_depth=depth),
    )


async def test_viewer_can_browse_schema_without_admin(ontology):
    workspace, app, track, model, kind, _, _ = ontology
    payload = await capture(ontology)
    ids = {node["id"] for node in payload["nodes"]}
    assert {workspace.id, app.id, track.id, model.id, kind.id} <= ids
    assert all(
        edge["source"] in ids and edge["target"] in ids for edge in payload["edges"]
    )
    schema = next(node for node in payload["nodes"] if node["id"] == kind.id)
    assert [field["key"] for field in schema["schema_fields"]] == ["summary", "related"]
    assert schema["schema_fields"][0]["required"] is True
    raw = json.dumps(payload)
    for forbidden in (
        "secret-target",
        "secret-default",
        "secret-config",
        "Hidden schema",
        "candidate_records",
    ):
        assert forbidden not in raw


async def test_hidden_parent_is_not_a_bridge_but_direct_child_is_discoverable(ontology):
    workspace, app, track, model, kind, _, guest = ontology
    await guest.connect(app, edge=EXCLUDED_FROM)
    # The direct App grant beats exclusion. Remove it to exercise inherited deny.
    context = await app.get_context()
    for edge in await context.find_edges_between(
        guest.id, app.id, edge_class=COLLABORATES_ON
    ):
        await edge.delete()
    await guest.connect(track, edge=COLLABORATES_ON, role="viewer")
    payload = await capture(ontology, root_id=track.id)
    ids = {node["id"] for node in payload["nodes"]}
    assert {track.id, model.id, kind.id} <= ids
    assert app.id not in ids
    roots = await list_ontology_roots(principal_id=guest.id, workspace_id=workspace.id)
    assert track.id in {root.id for root in roots}
    assert app.id not in {root.id for root in roots}
    with pytest.raises(InsufficientPermissionsError):
        await capture(ontology, root_id=app.id)


async def test_revocation_applies_to_new_capture_and_root_search(ontology):
    workspace, _, track, model, kind, _, guest = ontology
    assert kind.id in {node["id"] for node in (await capture(ontology))["nodes"]}
    await guest.connect(track, edge=EXCLUDED_FROM)
    payload = await capture(ontology)
    ids = {node["id"] for node in payload["nodes"]}
    assert not {track.id, model.id, kind.id} & ids
    roots = await list_ontology_roots(
        principal_id=guest.id, workspace_id=workspace.id, search="Decisions"
    )
    assert roots == []
    for root_id in (track.id, model.id, kind.id, "n.Track.unknown"):
        with pytest.raises(InsufficientPermissionsError):
            await capture(ontology, root_id=root_id)


async def test_foreign_workspace_and_shared_template_consumers_are_excluded(ontology):
    workspace, _, track, model, kind, _, guest = ontology
    foreign_workspace = await ensure_personal_workspace(guest)
    foreign = await Track.create(
        title="Foreign private", workspace_id=foreign_workspace.id
    )
    await foreign_workspace.connect(foreign, edge=CONTAINS)
    await catalog_track(foreign)
    await guest.connect(foreign, edge=OWNS)
    # Same template can be read in either workspace, never both in one capture.
    model.workspace_id = None
    await model.save()
    await foreign.connect(model, edge=HAS_OPERATIONAL_MODEL)
    foreign.attached_operational_model_id = model.id
    await foreign.save()
    kind.track_id = ""
    await kind.save()
    payload = await capture(ontology, root_id=model.id)
    ids = {node["id"] for node in payload["nodes"]}
    assert kind.id in ids
    assert foreign.id not in ids and foreign_workspace.id not in ids
    with pytest.raises(InsufficientPermissionsError):
        await capture(ontology, root_id=foreign.id)


async def test_draft_and_unattached_models_are_not_visible(ontology):
    _, _, track, model, kind, _, _ = ontology
    model.status = "draft"
    await model.save()
    with pytest.raises(InsufficientPermissionsError):
        await capture(ontology, root_id=model.id)
    with pytest.raises(InsufficientPermissionsError):
        await capture(ontology, root_id=kind.id)
    model.status = "published"
    await model.save()
    track.attached_operational_model_id = ""
    await track.save()
    with pytest.raises(InsufficientPermissionsError):
        await capture(ontology, root_id=kind.id)


async def test_legacy_direct_track_type_remains_readable(ontology):
    _, _, track, _, _, _, _ = ontology
    kind = await EntryType.create(name="Direct type", track_id=track.id)
    await track.connect(kind, edge=CONTAINS)
    payload = await capture(ontology, root_id=kind.id)
    assert kind.id in {node["id"] for node in payload["nodes"]}


async def test_http_scope_and_validation(ontology, second_user_client):
    workspace, _, track, _, _, _, _ = ontology
    headers = {"X-Integral-Scope": f"ws:{workspace.id}"}
    response = await second_user_client.post(
        "/api/workspace-ontology", json={"root_id": track.id}, headers=headers
    )
    assert response.status_code == 200, response.text
    assert response.json()["meta"]["workspace_id"] == workspace.id
    bad = await second_user_client.post(
        "/api/workspace-ontology", json={"max_depth": 20}, headers=headers
    )
    assert bad.status_code == 400
    malformed = await second_user_client.post(
        "/api/workspace-ontology", json={}, headers={"X-Integral-Scope": workspace.id}
    )
    assert malformed.status_code == 400
    denied = await second_user_client.post(
        "/api/workspace-ontology", json={}, headers={"X-Integral-Scope": "ws:unknown"}
    )
    assert denied.status_code == 403
    discovery = await second_user_client.get(
        "/api/workspace-ontology/roots?search=Decisions", headers=headers
    )
    assert discovery.status_code == 200
    assert [root["id"] for root in discovery.json()["roots"]] == [track.id]


async def test_membership_revocation_denies_even_workspace_root(ontology):
    workspace, _, _, _, _, _, guest = ontology
    context = await workspace.get_context()
    for edge in await context.find_edges_between(
        guest.id, workspace.id, edge_class=IS_MEMBER_OF
    ):
        await edge.delete()
    with pytest.raises(InsufficientPermissionsError):
        await capture(ontology)
    with pytest.raises(InsufficientPermissionsError):
        await list_ontology_roots(principal_id=guest.id, workspace_id=workspace.id)


async def test_schema_preview_has_a_byte_and_field_bound(ontology):
    _, _, _, _, kind, _, _ = ontology
    kind.form_schema = {
        "fields": [
            {
                "key": f"field_{index}",
                "label": "Long label " * 30,
                "type": "text",
                "default": "private" * 100,
            }
            for index in range(100)
        ]
    }
    kind.form_schema["fields"][0]["label"] = {"internal": "private nested value"}
    kind.form_schema["fields"][0]["type"] = {"internal": "private nested type"}
    await kind.save()
    payload = await capture(ontology, root_id=kind.id)
    node = next(node for node in payload["nodes"] if node["id"] == kind.id)
    assert len(node["schema_fields"]) == 24
    assert all(len(field["label"]) <= 128 for field in node["schema_fields"])
    assert "default" not in json.dumps(payload)
    assert "private nested" not in json.dumps(payload)
    assert node["schema_fields"][0]["label"] == "field_0"
    assert len(json.dumps(payload, ensure_ascii=False).encode()) < 65536
