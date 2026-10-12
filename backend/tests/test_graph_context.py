"""Real graph and tool-perimeter qualification for model context."""

import json

import pytest
from pydantic import ValidationError

from app.agentive.tooling.catalogue import build_tool_catalogue
from app.agentive.tooling.dispatch import dispatch_tool
from app.api.errors import InsufficientPermissionsError
from app.models.edges import (
    ANCHORS,
    COLLABORATES_ON,
    CONTAINS,
    EXCLUDED_FROM,
    IS_MEMBER_OF,
    IS_OF_TYPE,
    OWNS,
    REFERENCES,
)
from app.models.nodes import App, Entry, EntryType, Track
from app.schemas.graph_context import GraphContextRequest
from app.services.app_graph import catalog_app, catalog_track, catalog_workspace
from app.services.graph_context import capture_workspace_graph
from app.services.personal_workspace import ensure_personal_workspace

pytestmark = pytest.mark.smoke


@pytest.fixture
async def graph(test_user, test_user2):
    ws = await ensure_personal_workspace(test_user)
    await test_user2.connect(ws, edge=IS_MEMBER_OF, role="guest")
    app = await App.create(name="Research", workspace_id=ws.id)
    await catalog_app(app)
    await ws.connect(app, edge=CONTAINS)
    await test_user.connect(app, edge=OWNS)
    await test_user2.connect(app, edge=COLLABORATES_ON, role="viewer")
    track = await Track.create(title="Decisions", workspace_id=ws.id)
    await catalog_track(track)
    await app.connect(track, edge=CONTAINS)
    await test_user.connect(track, edge=OWNS)
    first = await Entry.create(
        title="Expand capacity",
        track_id=track.id,
        body="sensitive full text",
        custom_fields={"secret": "sensitive value"},
    )
    await track.connect(first, edge=CONTAINS)
    second = await Entry.create(title="Supplier dependency", track_id=track.id)
    await track.connect(second, edge=CONTAINS)
    third = await Entry.create(title="Risk mitigation", track_id=track.id)
    await track.connect(third, edge=CONTAINS)
    await first.connect(
        second, edge=REFERENCES, field_key="depends_on", cross_track=True
    )
    await second.connect(third, edge=REFERENCES, field_key="mitigated_by")
    kind = await EntryType.create(name="Decision", track_id=track.id)
    await track.connect(kind, edge=CONTAINS)
    await first.connect(kind, edge=IS_OF_TYPE)
    return ws, app, track, first, second, third, kind, test_user, test_user2


@pytest.fixture
async def recorded_graph(graph, tmp_path):
    """Qualify semantic detail on a complete native SQL store under real access.

    The capture's cooperative deadline is unchanged. JSON lookup timing is
    not a reliable way to assert complete projection semantics after adding
    fresh primary identity and containment checks.
    """
    from jvspatial.core import GraphContext
    from jvspatial.core.context import get_default_context, scoped_default_context
    from jvspatial.db.sqlite import SQLiteDB

    source = get_default_context().database
    database = SQLiteDB(db_path=tmp_path / "recorded-context.db")
    context = GraphContext(database)
    originals = []
    try:
        for collection in ("node", "edge", "object"):
            await database.bulk_save(collection, await source.find(collection, {}))
        for node in graph:
            originals.append((node, await node.get_context()))
            await node.set_context(context)
        with scoped_default_context(context):
            yield graph
    finally:
        for node, original in originals:
            await node.set_context(original)
        await database.close()


async def _capture(graph, **kwargs):
    ws, _, _, _, _, _, _, _, viewer = graph
    return await capture_workspace_graph(
        principal_id=viewer.id,
        workspace_id=ws.id,
        spec=GraphContextRequest(**kwargs),
    )


async def test_workspace_graph_has_paths_and_declared_meanings(recorded_graph):
    graph = recorded_graph
    ws, app, track, first, second, _, _, _, _ = graph
    data = await _capture(graph, max_depth=4)
    nodes = {n["id"]: n for n in data["nodes"]}
    assert {ws.id, app.id, track.id, first.id, second.id} <= nodes.keys()
    assert nodes[track.id]["label"] == "Decisions"
    assert nodes[first.id]["context"] == {
        "title": "Expand capacity",
        "status": "active",
    }
    # Catalog membership adds a second valid rooted path; first-discovery
    # order may choose either without changing evidence or authority.
    path = data["paths"][first.id]
    assert path["nodes"] in (
        [ws.id, app.id, track.id, first.id],
        [ws.id, f"n.Tracks.{ws.id}", track.id, first.id],
    )
    edges = {edge["id"]: edge for edge in data["edges"]}
    assert [
        (edges[key]["source"], edges[key]["target"]) for key in path["edges"]
    ] == list(zip(path["nodes"], path["nodes"][1:]))
    assert data["ontology"]["evidence_class"] == "application_declared"
    assert data["meta"]["consistency"] == "best_effort"
    assert "statistics" not in data
    assert "sensitive" not in json.dumps(data)
    for e in data["edges"]:
        assert e["source"] in nodes and e["target"] in nodes


async def test_relationship_evidence_retains_typed_metadata(recorded_graph):
    graph = recorded_graph
    first, second = graph[3:5]
    data = await _capture(
        graph, root_id=first.id, edge_types=("REFERENCES",), max_depth=1
    )
    assert {n["id"] for n in data["nodes"]} == {first.id, second.id}
    assert data["edges"][0]["context"]["field_key"] == "depends_on"
    assert data["paths"][second.id]["edges"] == [data["edges"][0]["id"]]
    assert second.id in data["expansion_points"]


async def test_denied_intermediate_never_becomes_a_bridge(graph):
    first, second, third = graph[3:6]
    viewer = graph[-1]
    await viewer.connect(second, edge=EXCLUDED_FROM)
    data = await _capture(
        graph, root_id=first.id, edge_types=("REFERENCES",), max_depth=4
    )
    assert {n["id"] for n in data["nodes"]} == {first.id}
    assert data["edges"] == []
    assert second.id not in json.dumps(data) and third.id not in json.dumps(data)


async def test_cross_workspace_target_is_not_exposed(graph):
    first, owner = graph[3], graph[-2]
    other_ws = await ensure_personal_workspace(graph[-1])
    assert other_ws.id != graph[0].id
    foreign_track = await Track.create(
        title="Other workspace", workspace_id=other_ws.id
    )
    await catalog_track(foreign_track)
    await other_ws.connect(foreign_track, edge=CONTAINS)
    await owner.connect(foreign_track, edge=OWNS)
    await graph[-1].connect(foreign_track, edge=COLLABORATES_ON, role="viewer")
    foreign = await Entry.create(title="Foreign secret", track_id=foreign_track.id)
    await foreign_track.connect(foreign, edge=CONTAINS)
    await first.connect(foreign, edge=REFERENCES, field_key="foreign")
    from app.services.permissions import can_view_entry

    assert await can_view_entry(graph[-1].id, foreign.id)
    data = await _capture(graph, root_id=first.id, edge_types=("REFERENCES",))
    with pytest.raises(InsufficientPermissionsError):
        await _capture(graph, root_id=foreign.id)
    assert foreign.id not in json.dumps(data)
    assert "Foreign secret" not in json.dumps(data)


async def test_installed_app_metadata_does_not_open_records(graph):
    app, first = graph[1], graph[3]
    app.installed_package_slug = "external-test-app"
    await app.save()
    data = await _capture(graph, max_depth=4)
    assert app.id in {n["id"] for n in data["nodes"]}
    assert first.id not in json.dumps(data)
    with pytest.raises(InsufficientPermissionsError):
        await _capture(graph, root_id=first.id)


async def test_paused_app_cannot_be_a_bridge(graph):
    app = graph[1]
    app.lifecycle_state = "paused"
    await app.save()
    data = await _capture(graph)
    assert app.id not in json.dumps(data)
    assert graph[2].id not in json.dumps(data)


async def test_expansion_rechecks_live_permission(recorded_graph):
    graph = recorded_graph
    first, second = graph[3:5]
    initial = await _capture(
        graph, root_id=first.id, edge_types=("REFERENCES",), max_depth=1
    )
    assert second.id in initial["expansion_points"]
    await graph[-1].connect(second, edge=EXCLUDED_FROM)
    with pytest.raises(InsufficientPermissionsError):
        await _capture(graph, root_id=second.id)


@pytest.mark.parametrize("view", ["relationships", "hierarchy", "ontology"])
async def test_view_and_budget_caps_have_no_dangling_evidence(graph, view):
    data = await _capture(graph, view=view, max_nodes=2, max_edges=1, max_bytes=4096)
    nodes = {n["id"] for n in data["nodes"]}
    assert len(nodes) <= 2 and len(data["edges"]) <= 1
    assert not data["meta"]["complete"]
    assert data["meta"]["stop_reasons"]
    assert (
        len(
            json.dumps(
                data, ensure_ascii=False, sort_keys=True, separators=(",", ":")
            ).encode()
        )
        <= 4096
    )
    for e in data["edges"]:
        assert e["source"] in nodes and e["target"] in nodes


async def test_anchor_uses_the_persisted_edge_name(recorded_graph):
    graph = recorded_graph
    first, track = graph[3], graph[2]
    await first.connect(track, edge=ANCHORS, field_key="details")
    data = await _capture(graph, root_id=first.id, edge_types=("Anchors",), max_depth=1)
    assert data["edges"][0]["entity"] == "Anchors"
    assert data["edges"][0]["context"]["field_key"] == "details"


async def test_http_and_dispatch_share_workspace_authority(graph, authenticated_client):
    ws, first, owner = graph[0], graph[3], graph[-2]
    response = await authenticated_client.post(
        "/api/graph-context",
        headers={"X-Integral-Scope": f"ws:{ws.id}"},
        json={"root_id": first.id, "max_depth": 0},
    )
    assert response.status_code == 200, response.text
    assert response.json()["nodes"][0]["id"] == first.id
    result = await dispatch_tool(
        "integral_get_graph_context",
        {"spec": {"root_id": first.id, "max_depth": 0}},
        principal_id=owner.user_id,
        scope=ws.id,
    )
    assert not result.is_error, result
    assert result.data["nodes"][0]["id"] == first.id
    denied = await authenticated_client.post(
        "/api/graph-context",
        headers={"X-Integral-Scope": "invalid"},
        json={},
    )
    assert denied.status_code == 400


@pytest.mark.unit
@pytest.mark.parametrize(
    "values",
    [
        {"workspace_id": "chosen-by-client"},
        {"node_fields": ["body"]},
        {"max_depth": 5},
        {"max_candidates": 2001},
        {"edge_types": ["OWNS"]},
    ],
)
def test_untrusted_scope_fields_and_excessive_budgets_rejected(values):
    with pytest.raises(ValidationError):
        GraphContextRequest(**values)


@pytest.mark.unit
def test_model_tool_advertises_the_actual_bounded_schema():
    tool = next(
        t for t in build_tool_catalogue() if t["name"] == "integral_get_graph_context"
    )
    schema = tool["input_schema"]["properties"]["spec"]
    assert schema["additionalProperties"] is False
    assert schema["properties"]["max_depth"]["maximum"] == 4
    assert "workspace_id" not in schema["properties"]


async def test_native_sqlite_projection_pages_candidates_without_workspace_scan(
    graph, tmp_path, monkeypatch
):
    from jvspatial.core import GraphContext
    from jvspatial.core.context import get_default_context, scoped_default_context
    from jvspatial.db.sqlite import SQLiteDB

    source = get_default_context().database
    db = SQLiteDB(db_path=tmp_path / "context.db")
    try:
        # Copy the small rooted test fixture, then run the complete Core
        # permission and projection stack on SQL rather than mocking access.
        for collection in ("node", "edge", "object"):
            await db.bulk_save(collection, await source.find(collection, {}))
        node_batches, edge_pages = [], []
        original_nodes, original_edges, original_find = (
            db.graph_node_batch,
            db.graph_edge_page,
            db.find,
        )

        async def nodes(ids):
            node_batches.append(len(ids))
            return await original_nodes(ids)

        async def edges(node_id, *, after=None, limit):
            edge_pages.append(limit)
            return await original_edges(node_id, after=after, limit=limit)

        async def find(collection, query=None, **kwargs):
            assert query, "projection performed an unscoped collection scan"
            assert query != {"entity": "Entry"}, "projection loaded the entry universe"
            return await original_find(collection, query, **kwargs)

        monkeypatch.setattr(db, "graph_node_batch", nodes)
        monkeypatch.setattr(db, "graph_edge_page", edges)
        monkeypatch.setattr(db, "find", find)
        with scoped_default_context(GraphContext(db)):
            data = await _capture(graph, max_depth=4)
        assert data["meta"]["execution_mode"] == "native"
        assert graph[3].id in {n["id"] for n in data["nodes"]}
        assert max(node_batches) <= 50
        assert max(edge_pages) <= 50
    finally:
        await db.close()


async def test_workspace_membership_is_required_even_for_known_root(graph):
    with pytest.raises(InsufficientPermissionsError):
        await capture_workspace_graph(
            principal_id="unknown-principal",
            workspace_id=graph[0].id,
            spec=GraphContextRequest(root_id=graph[3].id),
        )


async def test_http_rejects_malformed_and_untrusted_requests(
    graph, authenticated_client
):
    response = await authenticated_client.post(
        "/api/graph-context", content="{", headers={"Content-Type": "application/json"}
    )
    assert response.status_code == 400
    response = await authenticated_client.post(
        "/api/graph-context", json={"workspace_id": graph[0].id}
    )
    assert response.status_code == 400


async def test_capture_does_not_reuse_stale_request_or_process_permission_grants(
    graph, monkeypatch
):
    from app.middleware.permissions_cache import permissions_cache_get
    from app.services import permissions_process_cache as process_cache

    first, second, viewer = graph[3], graph[4], graph[-1]
    await viewer.connect(second, edge=EXCLUDED_FROM)
    monkeypatch.setattr(process_cache, "_ENABLED", True)
    process_cache.set_resolve_role_cached(viewer.id, "entry", second.id, "owner")
    permissions_cache_get()[("rr", "entry", second.id, viewer.id)] = "owner"
    try:
        data = await _capture(
            graph, root_id=first.id, edge_types=("REFERENCES",), max_depth=4
        )
        assert {n["id"] for n in data["nodes"]} == {first.id}
        assert data["edges"] == []
    finally:
        process_cache.invalidate_user(viewer.id)


@pytest.mark.unit
async def test_endpoint_requires_authenticated_identity():
    from fastapi import Request

    from app.api.errors import MissingAuthenticationError
    from app.api.graph_context import get_graph_context

    request = Request(
        {"type": "http", "method": "POST", "path": "/api/graph-context", "headers": []}
    )
    with pytest.raises(MissingAuthenticationError):
        await get_graph_context(request)


async def test_published_dependency_without_projection_fails_closed(
    graph, authenticated_client, monkeypatch
):
    import sys

    monkeypatch.setitem(sys.modules, "jvspatial.core.graph_projection", None)
    response = await authenticated_client.post(
        "/api/graph-context", json={"root_id": graph[3].id}
    )
    assert response.status_code == 400
    assert "qualified JV Spatial dev dependency" in response.text


@pytest.mark.unit
async def test_mcp_read_scope_advertises_the_bounded_graph_tool():
    from app.agentive.mcp.server import _list_tools_impl

    tools = await _list_tools_impl(["integral:read"])
    tool = next(t for t in tools if t.name == "integral_get_graph_context")
    assert tool.inputSchema["properties"]["spec"]["additionalProperties"] is False


async def test_personal_workspace_does_not_select_a_guest_workspace(graph):
    from app.models.nodes import User
    from app.services.app_graph import catalog_user

    visitor = await User.create(display_name="New guest")
    await catalog_user(visitor)
    await visitor.connect(graph[0], edge=IS_MEMBER_OF, role="guest")
    personal = await ensure_personal_workspace(visitor)
    assert personal.id != graph[0].id
    assert personal.kind == "personal"
    assert (await ensure_personal_workspace(visitor)).id == personal.id


async def _declare_recorded(graph):
    from app.services.operational_model_compile import normalize_entry_type_form_schema

    kind, first = graph[6], graph[3]
    kind.form_schema = normalize_entry_type_form_schema(
        {
            "fields": [
                {"key": "readiness", "type": "text"},
                {"key": "observed", "type": "date"},
                {"key": "verification", "type": "text"},
                {"key": "due", "type": "date"},
                {"key": "depends_on", "type": "relation"},
            ],
            "graph_context": {
                "schema_revision": 1,
                "fields": {
                    "readiness": {
                        "observed_at": "observed",
                        "verification": "verification",
                    },
                    "observed": {},
                    "verification": {},
                    "due": {},
                },
                "relations": {
                    "depends_on": {
                        "description": "Source requires the recorded target before proceeding.",
                        "direction": "requires",
                        "conditions": "Does not certify completion.",
                    }
                },
            },
        }
    )
    await kind.save()
    first.type_id = kind.id
    first.custom_fields.update(
        readiness="pending", observed="2026-10-11", verification="unconfirmed", due=None
    )
    await first.save()


async def test_recorded_fields_evidence_and_qualified_relation(recorded_graph):
    graph = recorded_graph
    await _declare_recorded(graph)
    data = await _capture(
        graph, root_id=graph[3].id, edge_types=("REFERENCES",), detail="recorded"
    )
    node = next(n for n in data["nodes"] if n["id"] == graph[3].id)
    assert data["format_version"] == 2
    assert node["context"]["custom_fields.readiness"] == "pending"
    assert node["fields"]["custom_fields.due"]["state"] == "null"
    assert (
        node["fields"]["custom_fields.readiness"]["evidence"]["observed_at"]
        == "2026-10-11"
    )
    relation = next(e for e in data["edges"] if e["source"] == graph[3].id)["relation"]
    assert relation["status"] == "resolved"
    assert len(data["relation_definitions"]) == 1
    assert "sensitive" not in json.dumps(data)
    assert len(json.dumps(data, separators=(",", ":")).encode()) <= 24576


async def test_recorded_old_schema_never_gets_new_meaning_or_fields(recorded_graph):
    graph = recorded_graph
    await _declare_recorded(graph)
    graph[3].schema_revision = 2
    await graph[3].save()
    data = await _capture(
        graph, root_id=graph[3].id, edge_types=("REFERENCES",), detail="recorded"
    )
    node = next(n for n in data["nodes"] if n["id"] == graph[3].id)
    assert "custom_fields.readiness" not in node["context"]
    assert data["relation_definitions"] == {}
    assert all(e["relation"]["status"] == "unresolved" for e in data["edges"])


async def test_recorded_authorization_is_rechecked_after_type_discovery(
    recorded_graph, monkeypatch
):
    graph = recorded_graph
    from app.services import graph_context as service

    await _declare_recorded(graph)
    original = service.build_recorded_profile

    async def revoke(*args):
        result = await original(*args)
        await graph[-1].connect(graph[4], edge=EXCLUDED_FROM)
        return result

    monkeypatch.setattr(service, "build_recorded_profile", revoke)
    data = await _capture(
        graph, root_id=graph[3].id, edge_types=("REFERENCES",), detail="recorded"
    )
    assert {n["id"] for n in data["nodes"]} == {graph[3].id}
    assert graph[4].id not in json.dumps(data)
    assert graph[5].id not in json.dumps(data)


@pytest.mark.parametrize(
    "declaration",
    [
        {"schema_revision": 1, "fields": {"secret": {}}},
        {"schema_revision": 1, "fields": {"depends_on": {}}},
        {"schema_revision": 1, "fields": {"readiness": {"source": "secret"}}},
        {"schema_revision": 1, "fields": {"custom_fields.readiness": {}}},
        {
            "schema_revision": 1,
            "relations": {"readiness": {"description": "x", "direction": "x"}},
        },
    ],
)
def test_context_declaration_rejects_unselected_hidden_and_non_scalar(declaration):
    from app.api.errors import BadRequestError
    from app.services.operational_model_compile import normalize_entry_type_form_schema

    with pytest.raises(BadRequestError):
        normalize_entry_type_form_schema(
            {
                "fields": [
                    {"key": "readiness", "type": "text"},
                    {"key": "secret", "type": "text", "hidden": True},
                    {"key": "depends_on", "type": "relation"},
                ],
                "graph_context": declaration,
            }
        )


async def test_recorded_same_key_different_type_does_not_inherit_meaning(
    recorded_graph,
):
    graph = recorded_graph
    await _declare_recorded(graph)
    other = await EntryType.create(name="Other", track_id=graph[2].id)
    await graph[2].connect(other, edge=CONTAINS)
    graph[4].type_id = other.id
    graph[4].custom_fields = {"readiness": "SECRET_UNDECLARED_STATE"}
    await graph[4].save()
    await graph[4].connect(graph[5], edge=REFERENCES, field_key="depends_on")
    data = await _capture(
        graph, root_id=graph[3].id, edge_types=("REFERENCES",), detail="recorded"
    )
    assert "SECRET_UNDECLARED_STATE" not in json.dumps(data)
    for edge in data["edges"]:
        if edge["source"] == graph[4].id:
            assert edge["relation"]["status"] == "unresolved"


async def test_recorded_cross_workspace_and_private_paths_remain_closed(recorded_graph):
    graph = recorded_graph
    await _declare_recorded(graph)
    await graph[-1].connect(graph[4], edge=EXCLUDED_FROM)
    other_ws = await ensure_personal_workspace(graph[-1])
    other_track = await Track.create(title="Other workspace", workspace_id=other_ws.id)
    await catalog_track(other_track)
    await other_ws.connect(other_track, edge=CONTAINS)
    foreign = await Entry.create(title="FOREIGN_DETAIL", track_id=other_track.id)
    await other_track.connect(foreign, edge=CONTAINS)
    await graph[3].connect(foreign, edge=REFERENCES, field_key="depends_on")
    data = await _capture(
        graph, root_id=graph[3].id, edge_types=("REFERENCES",), detail="recorded"
    )
    assert {node["id"] for node in data["nodes"]} == {graph[3].id}
    assert data["edges"] == []
    assert "FOREIGN_DETAIL" not in json.dumps(data)


async def test_recorded_budget_is_shared_across_passes(recorded_graph, monkeypatch):
    graph = recorded_graph
    from jvspatial.core.context import get_default_context

    ctx = get_default_context()
    original = ctx.project_graph
    spent = []

    async def measured(request, **kwargs):
        result = await original(request, **kwargs)
        spent.append((request.max_candidates, result.statistics["candidate_records"]))
        return result

    monkeypatch.setattr(ctx, "project_graph", measured)
    await _capture(graph, root_id=graph[3].id, detail="recorded", max_candidates=50)
    assert len(spent) == 2
    assert spent[1][0] == 50 - spent[0][1]
    assert sum(count for _, count in spent) <= 50


async def test_recorded_conflicting_units_and_evidence_are_not_cross_attributed(
    recorded_graph,
):
    graph = recorded_graph
    from app.services.operational_model_compile import normalize_entry_type_form_schema

    await _declare_recorded(graph)
    other = await EntryType.create(
        name="Other meaning",
        track_id=graph[2].id,
        form_schema=normalize_entry_type_form_schema(
            {
                "fields": [{"key": "readiness", "type": "text"}],
                "graph_context": {
                    "schema_revision": 1,
                    "fields": {"readiness": {"unit": "hours"}},
                },
            }
        ),
    )
    await graph[2].connect(other, edge=CONTAINS)
    graph[4].type_id = other.id
    graph[4].custom_fields = {"readiness": "two hours"}
    await graph[4].save()
    data = await _capture(
        graph, root_id=graph[3].id, edge_types=("REFERENCES",), detail="recorded"
    )
    for node in data["nodes"]:
        if node["id"] in {graph[3].id, graph[4].id}:
            field = node["fields"]["custom_fields.readiness"]
            assert "unit" not in field
            assert "observed_at" not in field["evidence"]
            assert "verification" not in field["evidence"]


async def test_recorded_byte_trim_remains_endpoint_and_definition_closed(
    recorded_graph,
):
    graph = recorded_graph
    await _declare_recorded(graph)
    data = await _capture(graph, root_id=graph[3].id, detail="recorded", max_bytes=8192)
    assert len(json.dumps(data, separators=(",", ":")).encode()) <= 8192
    ids = {node["id"] for node in data["nodes"]}
    definitions = set(data["relation_definitions"])
    for edge in data["edges"]:
        assert edge["source"] in ids and edge["target"] in ids
        assert set(edge["relation"]["definition_ids"]) <= definitions


async def test_recorded_legacy_container_in_scalar_field_is_never_exposed(
    recorded_graph,
):
    graph = recorded_graph
    await _declare_recorded(graph)
    graph[3].custom_fields["readiness"] = {"secret": "NESTED_PRIVATE_SENTINEL"}
    await graph[3].save()
    data = await _capture(graph, root_id=graph[3].id, detail="recorded")
    assert "NESTED_PRIVATE_SENTINEL" not in json.dumps(data)
    assert (
        "custom_fields.readiness"
        not in next(n for n in data["nodes"] if n["id"] == graph[3].id)["context"]
    )


async def test_recorded_schema_access_revocation_does_not_leave_relation_meanings(
    recorded_graph, monkeypatch
):
    graph = recorded_graph
    from app.services import graph_context as service

    await _declare_recorded(graph)
    await graph[-1].connect(graph[3], edge=COLLABORATES_ON, role="viewer")
    original = service.build_recorded_profile

    async def revoke(*args):
        result = await original(*args)
        await graph[-1].connect(graph[2], edge=EXCLUDED_FROM)
        return result

    monkeypatch.setattr(service, "build_recorded_profile", revoke)
    data = await _capture(graph, root_id=graph[3].id, detail="recorded")
    node = next(n for n in data["nodes"] if n["id"] == graph[3].id)
    assert "custom_fields.readiness" not in node["context"]
    assert data["relation_definitions"] == {}


async def test_recorded_root_byte_limit_is_not_reported_as_permission_denial(
    recorded_graph,
):
    graph = recorded_graph
    from app.api.errors import BadRequestError

    await _declare_recorded(graph)
    with pytest.raises(BadRequestError, match="Increase max_bytes"):
        await _capture(graph, root_id=graph[3].id, detail="recorded", max_bytes=4096)
