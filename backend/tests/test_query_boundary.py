"""W3.0: generic reads share one packaged-App boundary."""

from __future__ import annotations

import json
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
