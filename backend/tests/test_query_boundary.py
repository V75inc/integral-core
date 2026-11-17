"""W3.0: generic reads share one packaged-App boundary."""

from __future__ import annotations

import json
import os
from types import SimpleNamespace

import pytest
from fastapi import Request

from app.models.edges import (
    COLLABORATES_ON,
    CONTAINS,
    EXCLUDED_FROM,
    IS_MEMBER_OF,
    OWNS,
    REFERENCES,
)
from app.models.nodes import App, Entry, Track, User, Workspace
from app.services import agent_insights
from app.services.app_export import export_app_bundle
from app.services.dashboard_service import _resolve_count
from app.services.query_boundary import relation_visible
from app.utils.time import utc_now_iso

_SECRET = "secret-asset-tag-value"


async def _world():
    """One user, an open App, a packaged App, and a paused open App."""
    from app.services.app_graph import (
        catalog_app,
        catalog_track,
        catalog_user,
        catalog_workspace,
    )

    now = utc_now_iso()
    user = await User.create(
        user_id="boundary-user",
        display_name="Boundary User",
        created_at=now,
        updated_at=now,
    )
    await catalog_user(user)
    workspace = await Workspace.create(
        kind="personal",
        workspace_type="personal",
        name="Boundary Workspace",
        name_fold="boundary workspace",
        created_at=now,
        updated_at=now,
    )
    await user.connect(workspace, edge=IS_MEMBER_OF, role="owner", joined_at=now)
    await catalog_workspace(workspace)

    async def _app(name, **fields):
        app = await App.create(
            name=name,
            owner_user_id=user.id,
            workspace_id=workspace.id,
            visibility="private",
            created_at=now,
            updated_at=now,
            **fields,
        )
        await user.connect(app, edge=OWNS, role="owner", granted_at=now)
        await catalog_app(app)
        return app

    async def _track(app, title):
        track = await Track.create(
            title=title,
            owner_id=user.id,
            workspace_id=workspace.id,
            visibility="private",
            created_at=now,
            updated_at=now,
        )
        await user.connect(track, edge=OWNS, role="owner", granted_at=now)
        await app.connect(track, edge=CONTAINS, added_at=now)
        await catalog_track(track)
        return track

    async def _entry(track, title, **fields):
        entry = await Entry.create(
            title=title,
            author_id=user.id,
            track_id=track.id,
            created_at=now,
            updated_at=now,
            **fields,
        )
        await track.connect(entry, edge=CONTAINS, added_at=now)
        return entry

    open_app = await _app("Open Notes")
    open_track = await _track(open_app, "Notes")
    open_entry = await _entry(open_track, "Open note")
    other_open = await _entry(open_track, "Other open note")

    packaged = await _app("Asset Register", installed_package_slug="asset-register")
    packaged_track = await _track(packaged, "Assets")
    packaged_entry = await _entry(
        packaged_track, "Tagged drill", custom_fields={"tag": _SECRET}
    )
    packaged_other = await _entry(packaged_track, "Other packaged note")

    paused = await _app("Paused Notes", lifecycle_state="paused")
    paused_track = await _track(paused, "Paused")
    paused_entry = await _entry(paused_track, "Paused secret note")

    await other_open.connect(
        open_entry, edge=REFERENCES, field_key="sibling", cross_track=False
    )
    await packaged_entry.connect(
        open_entry, edge=REFERENCES, field_key="asset", cross_track=True
    )

    return SimpleNamespace(
        user=user,
        workspace=workspace,
        open_app=open_app,
        open_track=open_track,
        open_entry=open_entry,
        other_open=other_open,
        packaged=packaged,
        packaged_track=packaged_track,
        packaged_entry=packaged_entry,
        packaged_other=packaged_other,
        paused=paused,
        paused_track=paused_track,
        paused_entry=paused_entry,
    )


def _blob(payload) -> str:
    return json.dumps(payload, default=str)


@pytest.mark.asyncio
@pytest.mark.parametrize("direct_only", [False, True])
async def test_plain_count_uses_current_audience_without_selecting_a_page(
    monkeypatch, direct_only
):
    from app.services import permissions
    from tests.test_entry_projection_authority import reader_in
    from tests.test_resource_location import graph

    owner, workspace, _, track, entry = await graph()
    principal = await reader_in(workspace) if direct_only else owner
    grant = (
        await principal.connect(entry, edge=COLLABORATES_ON, role="viewer")
        if direct_only
        else None
    )
    args = dict(
        user_id=principal.id,
        track_id=track.id,
        workspace_id=workspace.id,
        sort_by="created_at",
        limit=1,
    )
    page = await agent_insights.query_entries(**args)
    assert page["total"] == 1

    async def forbidden(*args, **kwargs):
        pytest.fail("Plain scalar counts must not select/hydrate a record page")

    monkeypatch.setattr(permissions, "get_user_accessible_entry_page", forbidden)
    result = await agent_insights.query_entries(**args, count_only=True)
    assert result["total"] == page["total"]
    assert result["entries"] == []
    foreign = await agent_insights.query_entries(
        **{**args, "workspace_id": "n.Workspace.foreign"}, count_only=True
    )
    assert foreign["total"] == 0
    if grant is not None:
        await grant.delete()
        assert (await agent_insights.query_entries(**args, count_only=True))[
            "total"
        ] == 0


@pytest.mark.asyncio
@pytest.mark.parametrize("source", ["packaged_track", "paused_track"])
async def test_plain_count_keeps_generic_read_refusals(source):
    world = await _world()
    result = await agent_insights.query_entries(
        user_id=world.user.id,
        track_id=getattr(world, source).id,
        workspace_id=world.workspace.id,
        count_only=True,
    )
    assert result["total"] is None
    assert result["refused"]
    assert not result.get("records_read")


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "filters",
    [
        {"status": "active"},
        {"tags": ["absent"]},
        {"since": "2100-01-01"},
        {"query": "note"},
        {"filters": [{"field": "title", "op": "eq", "value": "Open note"}]},
    ],
)
async def test_filtered_count_keeps_canonical_query_results(filters):
    world = await _world()
    args = dict(
        user_id=world.user.id,
        track_id=world.open_track.id,
        workspace_id=world.workspace.id,
        limit=1,
        **filters,
    )
    page = await agent_insights.query_entries(**args)
    counted = await agent_insights.query_entries(**args, count_only=True)
    assert counted == page


@pytest.mark.asyncio
async def test_plain_count_counts_admitted_entity_without_rendering_its_payload():
    from tests.test_resource_location import graph

    owner, workspace, _, track, entry = await graph()
    context = await entry.get_context()
    await context.database.find_one_and_update(
        "node", {"id": entry.id}, {"$set": {"context.title": {"invalid": "display"}}}
    )
    result = await agent_insights.query_entries(
        user_id=owner.id,
        track_id=track.id,
        workspace_id=workspace.id,
        count_only=True,
    )
    assert result["total"] == 1
    assert result["entries"] == []


@pytest.mark.asyncio
async def test_scoped_count_helper_keeps_candidate_and_parent_scope():
    from app.services.permissions import count_user_accessible_entries
    from tests.test_resource_location import graph

    owner, workspace, app, track, _ = await graph()
    args = dict(workspace_id=workspace.id, app_id=app.id)
    assert await count_user_accessible_entries(owner.id, track.id, **args) == 1
    assert (
        await count_user_accessible_entries(
            owner.id, track.id, **{**args, "app_id": "n.App.foreign"}
        )
        == 0
    )
    assert (
        await count_user_accessible_entries(
            owner.id, track.id, **args, candidate_query={"id": "n.Entry.absent"}
        )
        == 0
    )


@pytest.mark.asyncio
async def test_plain_count_refuses_ambiguous_binding():
    from app.exceptions import AuthorizationUnavailableError
    from app.services.permissions import count_user_accessible_entries
    from tests.test_resource_location import graph

    owner, workspace, _, track, entry = await graph()
    _, _, _, foreign_track, _ = await graph()
    await foreign_track.connect(entry, edge=CONTAINS)
    args = dict(user_id=owner.id, track_id=track.id, workspace_id=workspace.id)
    try:
        total = await count_user_accessible_entries(**args)
    except AuthorizationUnavailableError:
        with pytest.raises(AuthorizationUnavailableError):
            await agent_insights.query_entries(**args, count_only=True)
    else:
        assert total == 0  # Portable admission withholds the ambiguous entity.
        result = await agent_insights.query_entries(**args, count_only=True)
        assert result["total"] == 0 and result["entries"] == []


@pytest.mark.asyncio
@pytest.mark.parametrize("scope", ["app", "track"])
async def test_exact_digest_does_not_discover_unrelated_track_inventory(
    monkeypatch, scope
):
    from app.services import permissions

    world = await _world()

    async def forbidden(*args, **kwargs):
        pytest.fail("An exact digest must discover only its target candidates")

    monkeypatch.setattr(permissions, "get_user_accessible_tracks", forbidden)
    result = await agent_insights.activity_digest(
        user_id=world.user.id,
        scope=scope,
        scope_id=world.open_app.id if scope == "app" else world.open_track.id,
        workspace_id=world.workspace.id,
    )
    assert result["total_tracks"] == 1
    assert result["total_entries"] == 2
    assert {row["track_id"] for row in result["track_summaries"]} == {
        world.open_track.id
    }


@pytest.mark.asyncio
@pytest.mark.parametrize("scope", ["app", "track"])
async def test_exact_digest_preserves_direct_leaf_privacy_and_revocation(scope):
    from tests.test_entry_projection_authority import reader_in
    from tests.test_resource_location import graph

    _, workspace, app, track, entry = await graph()
    reader = await reader_in(workspace)
    grant = await reader.connect(entry, edge=COLLABORATES_ON, role="viewer")
    args = dict(
        user_id=reader.id,
        scope=scope,
        scope_id=app.id if scope == "app" else track.id,
        workspace_id=workspace.id,
    )
    result = await agent_insights.activity_digest(**args)
    assert result["total_entries"] == 1
    assert result["track_summaries"][0]["title"] == "Shared records"
    assert result["track_summaries"][0]["action_url"] is None
    await grant.delete()
    result = await agent_insights.activity_digest(**args)
    assert result["total_tracks"] == 0
    assert result["track_summaries"] == []


@pytest.mark.asyncio
async def test_exact_digest_keeps_template_parent_listability_separate_from_leaves():
    from tests.test_entry_projection_authority import reader_in
    from tests.test_resource_location import graph

    _, workspace, app, track, entry = await graph()
    track.template_id = "template-only-identity"
    await track.save()
    reader = await reader_in(workspace)
    await reader.connect(track, edge=COLLABORATES_ON, role="viewer")
    args = dict(
        user_id=reader.id, scope="app", scope_id=app.id, workspace_id=workspace.id
    )
    assert (await agent_insights.activity_digest(**args))["total_tracks"] == 0
    await reader.connect(entry, edge=COLLABORATES_ON, role="viewer")
    result = await agent_insights.activity_digest(**args)
    assert result["total_entries"] == 1
    assert result["track_summaries"][0]["title"] == "Shared records"
    assert result["track_summaries"][0]["action_url"] is None


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "source", ["track", "app_page", "template_parent", "stalled_page"]
)
async def test_exact_digest_normalizes_source_failure(monkeypatch, source):
    from app.exceptions import AuthorizationUnavailableError
    from tests.test_resource_location import graph

    owner, workspace, app, track, _ = await graph()
    track.template_id = "source-failure-template"
    await track.save()

    async def unavailable(*args, **kwargs):
        raise RuntimeError("private-fixture-driver-diagnostic")

    if source == "track":
        monkeypatch.setattr(Track, "get", unavailable)
    elif source == "app_page":
        monkeypatch.setattr(App, "nodes_page", unavailable)
    elif source == "stalled_page":

        async def stalled(*args, **kwargs):
            return [track], "same-cursor"

        monkeypatch.setattr(App, "nodes_page", stalled)
    else:
        monkeypatch.setattr(Track, "nodes", unavailable)
    with pytest.raises(AuthorizationUnavailableError) as error:
        await agent_insights.activity_digest(
            user_id=owner.id,
            workspace_id=workspace.id,
            scope="track" if source == "track" else "app",
            scope_id=track.id if source == "track" else app.id,
        )
    assert "private-fixture-driver-diagnostic" not in str(error.value)


@pytest.mark.asyncio
@pytest.mark.postgres
@pytest.mark.skipif(
    os.getenv("INTEGRAL_TEST_DB") != "postgres", reason="PostgreSQL paging proof"
)
async def test_exact_digest_pages_all_children_and_deduplicates_candidates(monkeypatch):
    from app.services.app_graph import catalog_track
    from tests.test_resource_location import graph

    owner, workspace, app, track, _ = await graph()
    expected = {track.id}
    for index in range(204):
        child = await Track.create(
            title=f"Empty child {index}", workspace_id=workspace.id
        )
        await app.connect(child, edge=CONTAINS)
        await catalog_track(child)
        expected.add(child.id)
    original = App.nodes_page
    calls = []

    async def observed(self, *args, **kwargs):
        page, cursor = await original(self, *args, **kwargs)
        if self.id == app.id:
            calls.append((len(page), cursor))
            if len(calls) == 1:
                page = page + page[:1]
        return page, cursor

    monkeypatch.setattr(App, "nodes_page", observed)
    tracks, readable = await agent_insights._digest_scope_tracks(
        owner.id, "app", app.id, workspace.id
    )
    assert {row.id for row in tracks} == readable == expected
    assert len(tracks) == 205
    assert len(calls) == 2 and calls[0][0] == 200 and calls[-1][1] is None


@pytest.mark.asyncio
async def test_generic_reads_hide_packaged_and_paused_entries():
    """Open rows come back. Packaged and paused rows are a refusal, not zero."""
    world = await _world()
    listed = await agent_insights.query_entries(
        user_id=world.user.id, workspace_id=world.workspace.id
    )
    blob = _blob(listed)
    assert world.open_entry.id in blob
    assert _SECRET not in blob
    assert world.packaged_entry.id not in blob
    assert listed["boundary"]["excluded_tracks"] == 2
    assert listed["boundary"]["declared_query_required"] is True

    named = await agent_insights.query_entries(
        user_id=world.user.id,
        workspace_id=world.workspace.id,
        track_id=world.packaged_track.id,
    )
    assert named["refused"]["code"] == "app_domain"
    assert named["refused"]["declared_query_required"] is True
    assert named["entries"] == []
    assert _SECRET not in _blob(named)

    paused = await agent_insights.query_entries(
        user_id=world.user.id,
        workspace_id=world.workspace.id,
        track_id=world.paused_track.id,
    )
    assert paused["refused"]["code"] == "app_unavailable"
    assert "Paused secret note" not in _blob(paused)

    counted = await agent_insights.count_entries_grouped(
        user_id=world.user.id,
        group_by="track",
        track_id=world.packaged_track.id,
        workspace_id=world.workspace.id,
    )
    assert counted["refused"]["code"] == "app_domain"
    assert counted["groups"] == []

    digest = await agent_insights.activity_digest(
        user_id=world.user.id,
        scope="user",
        workspace_id=world.workspace.id,
    )
    assert _SECRET not in _blob(digest)

    assert digest["excluded_tracks"] == 2

    from app.agentive.services.query_spec import execute_query_spec
    from app.schemas.query_spec import QuerySpec

    spec = await execute_query_spec(
        principal_id=world.user.id,
        workspace_id=world.workspace.id,
        spec=QuerySpec(resource="entry", select=["id", "title"], limit=50),
    )
    spec_blob = spec.model_dump_json()
    assert world.open_entry.id in spec_blob
    assert _SECRET not in spec_blob
    assert spec.boundary["declared_query_required"] is True

    count = await _resolve_count(
        user_id=world.user.id,
        app_id=world.packaged.id,
        workspace_id=world.workspace.id,
        data_source={},
    )
    assert count["value"] is None
    assert count["refused"]["code"] == "app_domain"
    assert _SECRET not in _blob(count)


@pytest.mark.asyncio
async def test_filtered_grouped_count_streams_authorized_track_entries():
    world = await _world()
    counted = await agent_insights.count_entries_grouped(
        user_id=world.user.id,
        group_by="status",
        track_id=world.open_track.id,
        status="active",
        workspace_id=world.workspace.id,
    )
    assert counted["total_matched"] == 2
    assert counted["groups"] == [{"key": "active", "label": "active", "count": 2}]


@pytest.mark.asyncio
async def test_open_app_reads_still_enforce_entry_level_exclusion():
    """The App-domain allow decision never overrides per-Entry access."""
    world = await _world()
    reader = await User.create(user_id="boundary-reader", display_name="Reader")
    now = utc_now_iso()
    await reader.connect(
        world.workspace, edge=IS_MEMBER_OF, role="member", joined_at=now
    )
    await reader.connect(
        world.open_track,
        edge=COLLABORATES_ON,
        role="viewer",
        granted_at=now,
    )
    await reader.connect(world.other_open, edge=EXCLUDED_FROM, granted_at=now)

    listed = await agent_insights.query_entries(
        user_id=reader.id,
        workspace_id=world.workspace.id,
        track_id=world.open_track.id,
    )
    blob = _blob(listed)
    assert world.open_entry.id in blob
    assert world.other_open.id not in blob

    grouped = await agent_insights.count_entries_grouped(
        user_id=reader.id,
        group_by="track",
        track_id=world.open_track.id,
        workspace_id=world.workspace.id,
    )
    assert grouped["total_matched"] == 1

    digest = await agent_insights.activity_digest(
        user_id=reader.id,
        scope="track",
        scope_id=world.open_track.id,
        workspace_id=world.workspace.id,
    )
    assert world.open_entry.title in _blob(digest)
    assert world.other_open.title not in _blob(digest)


@pytest.mark.asyncio
async def test_same_app_relations_stay_and_cross_app_packaged_hops_do_not():
    """An App's own link stays. A link in from a packaged App does not."""
    world = await _world()
    assert await relation_visible(world.open_entry, world.other_open) is True
    assert await relation_visible(world.open_entry, world.packaged_entry) is False
    assert await relation_visible(world.packaged_entry, world.packaged_other) is True
    world.packaged.lifecycle_state = "paused"
    assert await relation_visible(world.packaged_entry, world.packaged_other) is True
    world.packaged.lifecycle_state = "active"


@pytest.mark.asyncio
async def test_related_endpoint_omits_the_packaged_hop(monkeypatch):
    """The relation list keeps the open sibling and counts the packaged one."""
    world = await _world()

    async def _allow(**_kwargs):
        return SimpleNamespace(allowed=True)

    monkeypatch.setattr("app.api.entry_relations.policy_evaluate", _allow)
    from app.api.entry_relations import list_entry_relations

    request = Request({"type": "http", "headers": [], "query_string": b""})
    request.state.user = {"id": world.user.id}
    payload = await list_entry_relations(request, world.open_entry.id)
    ids = {row["id"] for row in payload["entries"]}
    assert world.other_open.id in ids
    assert world.packaged_entry.id not in ids
    assert payload["boundary"]["excluded_relations"] == 1
    assert _SECRET not in _blob(payload)


@pytest.mark.asyncio
async def test_paused_packaged_app_still_exports():
    """Export is the retention exception. Generic read of that App is not."""
    world = await _world()
    world.packaged.lifecycle_state = "paused"
    await world.packaged.save()
    bundle = await export_app_bundle(app_id=world.packaged.id)
    assert bundle["policy"]["boundary"] == "retention_export_exception"
    assert _SECRET in _blob(bundle)
    named = await agent_insights.query_entries(
        user_id=world.user.id,
        workspace_id=world.workspace.id,
        track_id=world.packaged_track.id,
    )
    assert named["refused"]["code"] == "app_unavailable"


@pytest.mark.asyncio
async def test_track_entry_traversal_drops_packaged_targets():
    """A track→entries hop does not return packaged-App rows or their ids."""
    from app.agentive.services.query_spec import execute_query_spec
    from app.schemas.query_spec import QuerySpec, QueryTraversal

    world = await _world()
    spec = await execute_query_spec(
        principal_id=world.user.id,
        workspace_id=world.workspace.id,
        spec=QuerySpec(
            resource="track",
            select=["id"],
            traversal=[
                QueryTraversal(edge="entries", select=["id", "title"], limit=20)
            ],
            limit=20,
            cost_ceiling=1000,
        ),
    )
    blob = spec.model_dump_json()
    assert world.open_entry.id in blob
    assert world.packaged_entry.id not in blob
    assert _SECRET not in blob
    assert spec.boundary["declared_query_required"] is True


@pytest.mark.asyncio
async def test_mission_control_preview_omits_packaged_entries(monkeypatch):
    """Mission control counts only entries visible in its generic-read snapshot."""
    from app.api.mission_control import get_mission_control_snapshot

    world = await _world()
    request = Request({"type": "http", "headers": [], "query_string": b""})
    request.state.user = {"id": world.user.id}
    snap = await get_mission_control_snapshot(request)
    blob = _blob(snap)
    assert world.open_entry.id in blob
    assert world.packaged_entry.id not in blob
    assert _SECRET not in blob
    assert snap["boundary"]["excluded_tracks"] >= 1
    track_counts = {track["id"]: track["entry_count"] for track in snap["tracks"]}
    assert track_counts[world.open_track.id] == 2
    assert track_counts[world.packaged_track.id] == 0
    assert track_counts[world.paused_track.id] == 0


@pytest.mark.asyncio
async def test_dashboard_recent_entries_refuses_a_packaged_app():
    """A refusal is not an empty list of records."""
    from app.services.dashboard_service import resolve_widget_data

    world = await _world()
    data = await resolve_widget_data(
        user_id=world.user.id,
        app_id=world.packaged.id,
        workspace_id=world.workspace.id,
        widget={"type": "recent_entries", "data_source": {"limit": 10}},
    )
    assert data["entries"] == []
    assert data["refused"]["code"] == "app_domain"
    assert data["value"] is None
    assert _SECRET not in _blob(data)


@pytest.mark.asyncio
async def test_stub_track_without_nodes_is_open():
    """Unit-test tracks have no graph. They are not a packaged App."""
    from app.services.query_boundary import generic_entry_read, keep_open_entries

    track = SimpleNamespace(id="n.Track.stub", title="Stub")
    decision = await generic_entry_read(track)
    assert decision.allowed is True
    assert decision.code == "open"

    entry = SimpleNamespace(id="n.Entry.stub", track_id="")
    kept, excluded = await keep_open_entries([entry])
    assert [row.id for row in kept] == ["n.Entry.stub"]
    assert excluded == 0


@pytest.mark.asyncio
async def test_related_returns_outbound_and_anchors_without_packaged_hops(
    monkeypatch,
):
    """W3.3: both directions and anchors, with field key and track/app."""
    from app.models.edges import ANCHORS

    world = await _world()
    await world.open_entry.connect(
        world.other_open, edge=REFERENCES, field_key="lookup", cross_track=False
    )
    await world.open_entry.connect(
        world.packaged_entry,
        edge=REFERENCES,
        field_key="secret_link",
        cross_track=True,
    )
    await world.open_entry.connect(
        world.open_track, edge=ANCHORS, field_key="details", role="detail"
    )

    async def _allow(**_kwargs):
        return SimpleNamespace(allowed=True)

    monkeypatch.setattr("app.api.entry_relations.policy_evaluate", _allow)
    from app.api.entry_relations import list_entry_relations

    request = Request(
        {
            "type": "http",
            "headers": [],
            "query_string": b"direction=both&include_anchors=true",
        }
    )
    request.state.user = {"id": world.user.id}
    payload = await list_entry_relations(request, world.open_entry.id)
    rows = {
        (row["direction"], row["edge"], row["field_key"]): row
        for row in payload["related"]
    }
    outbound = rows[("out", "REFERENCES", "lookup")]
    assert outbound["id"] == world.other_open.id
    assert outbound["track_id"] == world.open_track.id
    assert outbound["app_id"] == world.open_app.id
    anchor = rows[("out", "ANCHORS", "details")]
    assert anchor["kind"] == "track"
    assert anchor["track_id"] == world.open_track.id
    assert anchor["app_id"] == world.open_app.id
    assert world.packaged_entry.id not in {row["id"] for row in payload["related"]}
    assert _SECRET not in _blob(payload)

    plain = Request({"type": "http", "headers": [], "query_string": b""})
    plain.state.user = {"id": world.user.id}
    inbound = await list_entry_relations(plain, world.open_entry.id)
    assert inbound["related"]
    assert all(row["direction"] == "in" for row in inbound["related"])
    assert world.other_open.id in {row["id"] for row in inbound["entries"]}


def _search_request(world, *, body=None, service=False, session=True, principal=None):
    """Simulate a request AFTER normal authentication; signature is still checked."""
    import time

    import jwt

    from app.config import settings

    principal = principal or world.user.id
    headers = [(b"x-integral-scope", f"ws:{world.workspace.id}".encode())]
    if session:
        token = jwt.encode(
            {
                "user_id": principal,
                "jti": "native-search-test",
                "exp": int(time.time()) + 300,
                **({"svc_auth": True} if service else {}),
            },
            settings.SECRET_KEY,
            algorithm=settings.ALGORITHM,
        )
        headers.append((b"authorization", f"Bearer {token}".encode()))

    async def receive():
        return {"type": "http.request", "body": json.dumps(body or {}).encode()}

    request = Request(
        {"type": "http", "headers": headers, "query_string": b""}, receive
    )
    request.state.user = {"id": principal}
    return request


@pytest.mark.asyncio
@pytest.mark.smoke
@pytest.mark.parametrize("mode", ["lexical", "semantic", "hybrid"])
@pytest.mark.parametrize(
    "session,service", [(True, False), (True, True), (False, False)]
)
async def test_native_search_keeps_installed_app_hits_but_machine_queries_do_not(
    monkeypatch, mode, session, service
):
    from app.api import retrieve as retrieve_api
    from app.schemas.retrieve import RetrievedEntry

    world = await _world()
    hit = RetrievedEntry(
        entry_id=world.packaged_entry.id,
        track_id=world.packaged_track.id,
        title=world.packaged_entry.title,
        score=1,
        mode_origin=["lexical"],
    )

    async def dispatch(*args, **kwargs):
        # Channel matching/permissions are covered by the retrieval suites; this
        # regression pins the final REST adapter that discarded admitted hits.
        return [hit], 0, 1, mode, False

    monkeypatch.setattr(retrieve_api, "_dispatch_retrieve_modes", dispatch)
    request = _search_request(
        world,
        session=session,
        service=service,
        body={
            "query": "drill",
            "mode": mode,
            "scope": f"track:{world.packaged_track.id}",
        },
    )
    response = await retrieve_api.retrieve(request)
    if session and not service:
        assert [row["entry_id"] for row in response["results"]] == [
            world.packaged_entry.id
        ]
        assert response["boundary"] is None
    else:
        assert response["results"] == []
        assert response["boundary"]["declared_query_required"]
        assert world.packaged_entry.id not in _blob(response)


@pytest.mark.asyncio
@pytest.mark.smoke
async def test_native_feed_and_ranked_hit_hydration_keep_authorized_app_records():
    from app.api.feed import get_feed

    world = await _world()
    request = _search_request(world)
    for selection in (
        {"track_id": world.packaged_track.id},
        {"app_id": world.packaged.id},
        {"entry_ids": world.packaged_entry.id},
        {},
    ):
        result = await get_feed(request, **selection)
        assert world.packaged_entry.id in {row["id"] for row in result["entries"]}
        # The unfiltered fixture also contains a paused App, which remains
        # excluded; that must not discard the active installed-App records.
        if selection:
            assert not result.get("boundary")
        assert world.paused_entry.id not in {row["id"] for row in result["entries"]}
        assert not result.get("refused")
    for request in (
        _search_request(world, service=True),
        _search_request(world, session=False),
    ):
        result = await get_feed(request, track_id=world.packaged_track.id)
        assert result["entries"] == []
        assert result["refused"]["code"] == "app_domain"


@pytest.mark.asyncio
@pytest.mark.smoke
async def test_native_read_still_closes_paused_and_removed_apps():
    from app.api.feed import get_feed
    from app.services.query_boundary import keep_open_entries

    world = await _world()
    for state in ("paused", "uninstalled"):
        world.packaged.lifecycle_state = state
        await world.packaged.save()
        rows, excluded = await keep_open_entries(
            [world.packaged_entry], native_user=True
        )
        assert rows == [] and excluded == 1
        response = await get_feed(_search_request(world), app_id=world.packaged.id)
        assert response["entries"] == []
        assert response["refused"]["code"] == "app_unavailable"


@pytest.mark.asyncio
@pytest.mark.smoke
async def test_native_search_has_no_client_controlled_boundary_override():
    from app.api.retrieve import retrieve

    world = await _world()
    response = await retrieve(
        _search_request(
            world,
            session=False,
            body={
                "query": "drill",
                "mode": "lexical",
                "native_user": True,
            },
        )
    )
    assert response.status_code == 422


@pytest.mark.asyncio
@pytest.mark.smoke
async def test_native_search_cannot_change_host_workspace():
    from app.api.errors import InsufficientPermissionsError
    from app.api.retrieve import retrieve

    world = await _world()
    with pytest.raises(InsufficientPermissionsError):
        await retrieve(
            _search_request(
                world,
                body={
                    "query": "drill",
                    "mode": "lexical",
                    "scope": "workspace:outside",
                },
            )
        )


@pytest.mark.asyncio
@pytest.mark.smoke
async def test_native_session_is_not_a_record_permission_grant():
    from app.api.feed import get_feed
    from app.middleware.permissions_cache import isolated_permissions_cache
    from app.services.app_graph import catalog_user

    world = await _world()
    now = utc_now_iso()
    reader = await User.create(
        user_id="search-reader",
        display_name="Search reader",
        created_at=now,
        updated_at=now,
    )
    await catalog_user(reader)
    await reader.connect(
        world.workspace, edge=IS_MEMBER_OF, role="member", joined_at=now
    )
    request = _search_request(world, principal=reader.id)
    with isolated_permissions_cache():
        response = await get_feed(request, entry_ids=world.packaged_entry.id)
        assert response["entries"] == []
    await reader.connect(
        world.packaged_track, edge=COLLABORATES_ON, role="viewer", granted_at=now
    )
    with isolated_permissions_cache():
        response = await get_feed(request, entry_ids=world.packaged_entry.id)
        assert [row["id"] for row in response["entries"]] == [world.packaged_entry.id]
    await reader.connect(world.packaged_entry, edge=EXCLUDED_FROM, granted_at=now)
    with isolated_permissions_cache():
        response = await get_feed(request, entry_ids=world.packaged_entry.id)
        assert response["entries"] == []


@pytest.mark.asyncio
@pytest.mark.parametrize("filtered", [False, True])
async def test_exact_track_query_never_discovers_unrelated_inventory(
    monkeypatch, filtered
):
    """Exact candidate reads keep row admission without an all-Track listing."""
    world = await _world()

    async def inventory_forbidden(_user_id):
        raise AssertionError("An exact Track query must not enumerate inventories")

    monkeypatch.setattr(
        "app.services.permissions.get_user_accessible_tracks", inventory_forbidden
    )
    result = await agent_insights.query_entries(
        user_id=world.user.id,
        track_id=world.open_track.id,
        workspace_id=world.workspace.id,
        **({"status": "active"} if filtered else {}),
    )
    assert {row["id"] for row in result["entries"]} == {
        world.open_entry.id,
        world.other_open.id,
    }
    assert result["total"] == 2
    grouped = await agent_insights.count_entries_grouped(
        user_id=world.user.id,
        track_id=world.open_track.id,
        workspace_id=world.workspace.id,
        group_by="status",
    )
    assert grouped["total_matched"] == 2
    missing = await agent_insights.query_entries(
        user_id=world.user.id,
        track_id="n.Track.missing",
        workspace_id=world.workspace.id,
    )
    assert missing["entries"] == [] and missing["total"] == 0
    outside = await agent_insights.query_entries(
        user_id=world.user.id,
        track_id=world.open_track.id,
        workspace_id="n.Workspace.not-the-parent",
    )
    assert outside["entries"] == [] and outside["total"] == 0


@pytest.mark.asyncio
async def test_exact_track_entry_only_grant_and_live_revocation(monkeypatch):
    from app.services.app_graph import catalog_user
    from app.services.permissions import can_view_track

    world = await _world()
    reader = await User.create(user_id="entry-only-reader", display_name="Reader")
    await catalog_user(reader)
    await reader.connect(
        world.workspace, edge=IS_MEMBER_OF, role="guest", joined_at=utc_now_iso()
    )
    await reader.connect(
        world.open_entry, edge=COLLABORATES_ON, role="viewer", granted_at=utc_now_iso()
    )
    assert not await can_view_track(reader.id, world.open_track.id)

    async def inventory_forbidden(_user_id):
        raise AssertionError("Exact Entry reads must not require Track inventory")

    monkeypatch.setattr(
        "app.services.permissions.get_user_accessible_tracks", inventory_forbidden
    )

    async def read():
        return await agent_insights.query_entries(
            user_id=reader.id,
            track_id=world.open_track.id,
            workspace_id=world.workspace.id,
        )

    admitted = await read()
    assert [row["id"] for row in admitted["entries"]] == [world.open_entry.id]
    grouped = await agent_insights.count_entries_grouped(
        user_id=reader.id,
        track_id=world.open_track.id,
        workspace_id=world.workspace.id,
        group_by="track",
    )
    assert grouped["total_matched"] == 1
    assert grouped["groups"][0]["label"] == "Shared records"
    # Mutate the graph directly, bypassing process invalidators: the next read
    # must see the revocation just as it would from another API worker.
    await reader.disconnect(world.open_entry, edge_type=COLLABORATES_ON)
    revoked = await read()
    assert revoked["entries"] == [] and revoked["total"] == 0
    grouped = await agent_insights.count_entries_grouped(
        user_id=reader.id,
        track_id=world.open_track.id,
        workspace_id=world.workspace.id,
        group_by="status",
    )
    assert grouped["total_matched"] == 0


@pytest.mark.asyncio
@pytest.mark.parametrize("created", [None, "", "non-ISO date", "__missing__"])
async def test_count_window_keeps_nullable_timestamps_and_update_date_filters(
    monkeypatch, created
):
    from app.services import permissions

    world = await _world()
    context = await world.open_track.get_context()
    for entry, created_at, updated_at in (
        (world.open_entry, created, "2026-05-15T00:00:00+00:00"),
        (world.other_open, "2026-04-15T00:00:00+00:00", None),
    ):
        row = await context.database.get("node", entry.id)
        if created_at == "__missing__":
            row["context"].pop("created_at", None)
        else:
            row["context"]["created_at"] = created_at
        row["context"]["updated_at"] = updated_at
        await context.database.save("node", row)
    pages = []
    original = permissions.get_user_accessible_entry_page

    async def observed(*args, **kwargs):
        pages.append(kwargs)
        return await original(*args, **kwargs)

    monkeypatch.setattr(permissions, "get_user_accessible_entry_page", observed)
    plain = await _resolve_count(
        user_id=world.user.id,
        app_id=world.open_app.id,
        workspace_id=world.workspace.id,
        data_source={"track_id": world.open_track.id},
    )
    assert plain == {"value": 2, "total_matched": 2}
    assert pages == []  # Scalar counts must not select or sort a display row.
    windowed = await _resolve_count(
        user_id=world.user.id,
        app_id=world.open_app.id,
        workspace_id=world.workspace.id,
        data_source={
            "track_id": world.open_track.id,
            "status": "active",
            "since": "2026-05-01T00:00:00+00:00",
            "until": "2026-06-01T00:00:00+00:00",
        },
    )
    assert windowed == {"value": 1, "total_matched": 1}


@pytest.mark.asyncio
@pytest.mark.parametrize("explicit_track", [True, False])
async def test_count_preserves_refusal_when_source_pauses_during_read(
    monkeypatch, explicit_track
):
    from app.services import dashboard_service as service

    world = await _world()
    original = service.query_entries

    async def pause_before_query(**kwargs):
        world.open_app.lifecycle_state = "paused"
        await world.open_app.save()
        return await original(**kwargs)

    monkeypatch.setattr(service, "query_entries", pause_before_query)
    result = await service._resolve_count(
        user_id=world.user.id,
        app_id=world.open_app.id,
        workspace_id=world.workspace.id,
        data_source={"track_id": world.open_track.id} if explicit_track else {},
    )
    assert result["value"] is None
    assert result["total_matched"] == 0
    assert result["refused"]["code"] == "app_unavailable"
    assert result["error_code"] == "app_unavailable"
