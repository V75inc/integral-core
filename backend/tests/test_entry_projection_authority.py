"""Entry projections must expose exactly the current canonical read audience."""

import uuid

import pytest
from jvspatial.api.auth.models import User as AuthUser

from app.exceptions import AuthorizationUnavailableError
from app.models.edges import COLLABORATES_ON, CONTAINS, EXCLUDED_FROM, IS_MEMBER_OF
from app.models.nodes import Entry, User
from app.services.app_graph import catalog_user
from app.services.entry_listing import fetch_accessible_entries_page
from app.services.permissions import (
    can_view_entry,
    count_user_accessible_entries,
    get_user_accessible_entries,
    get_user_accessible_entry_page,
    iter_user_accessible_entry_batches,
    resolve_role,
)
from tests.test_resource_location import graph
from tests.test_workspace_owner_governance import private_member_graph

pytestmark = [pytest.mark.smoke, pytest.mark.asyncio]

PROJECTIONS = ("list", "page", "workspace_page", "count", "offset", "stream")


async def reader_in(workspace):
    identity = await AuthUser.create(
        email=f"projection-{uuid.uuid4().hex}@example.com", password_hash="fixture"
    )
    reader = await User.create(user_id=identity.id, display_name="Projection reader")
    await catalog_user(reader)
    await reader.connect(workspace, edge=IS_MEMBER_OF, role="member")
    return reader


async def project(projection, principal, workspace, track):
    if projection == "list":
        rows = await get_user_accessible_entries(
            principal, track_id=track.id, workspace_id=workspace.id, strict=False
        )
        return {row.id for row in rows}, len(rows)
    if projection in {"page", "workspace_page"}:
        rows, metadata = await fetch_accessible_entries_page(
            principal,
            track_id=track.id if projection == "page" else None,
            workspace_id=workspace.id,
            include_total=True,
            limit=20,
        )
        return {row.id for row in rows}, metadata["total"]
    if projection == "count":
        return None, await count_user_accessible_entries(principal, track.id)
    if projection == "offset":
        metadata = await get_user_accessible_entry_page(
            principal, track.id, limit=20, workspace_id=workspace.id
        )
        return {row.id for row in metadata["entries"]}, metadata["total"]
    rows = []
    async for batch in iter_user_accessible_entry_batches(
        principal, track.id, batch_size=1, workspace_id=workspace.id
    ):
        rows.extend(batch)
    return {row.id for row in rows}, len(rows)


def assert_projection(projection, actual, expected):
    ids, total = actual
    if projection != "count":
        assert ids == expected
    assert total == len(expected)


@pytest.mark.parametrize("projection", PROJECTIONS)
@pytest.mark.parametrize("principal_form", ("graph", "auth"))
async def test_entry_exclusion_filters_rows_and_totals(projection, principal_form):
    _, workspace, _, track, excluded = await graph()
    reader = await reader_in(workspace)
    principal = reader.id if principal_form == "graph" else reader.user_id
    await reader.connect(track, edge=COLLABORATES_ON, role="viewer")
    admitted = await Entry.create(title="Admitted", track_id=track.id)
    await track.connect(admitted, edge=CONTAINS)
    assert_projection(
        projection,
        await project(projection, principal, workspace, track),
        {excluded.id, admitted.id},
    )
    await reader.connect(excluded, edge=EXCLUDED_FROM)
    assert not await can_view_entry(principal, excluded.id)
    assert await can_view_entry(principal, admitted.id)
    assert_projection(
        projection,
        await project(projection, principal, workspace, track),
        {admitted.id},
    )


@pytest.mark.parametrize("projection", PROJECTIONS)
async def test_owner_data_governance_survives_entry_exclusion(projection):
    owner, _, workspace, _, track, entry = await private_member_graph()
    assert await resolve_role(owner.id, "entry", entry.id) is None
    assert await can_view_entry(owner.id, entry.id)
    assert_projection(
        projection, await project(projection, owner.id, workspace, track), {entry.id}
    )


@pytest.mark.parametrize("projection", PROJECTIONS)
@pytest.mark.parametrize("state", ("installing", "awaiting_settings", "failed"))
async def test_nonexecuting_retained_data_is_absent_from_regular_projection(
    projection, state
):
    owner, _, workspace, app, track, entry = await private_member_graph()
    app.lifecycle_state = state
    await app.save()
    assert not await can_view_entry(owner.id, entry.id)
    assert_projection(
        projection, await project(projection, owner.id, workspace, track), set()
    )


@pytest.mark.parametrize(
    "state", ("active", "paused", "installing", "awaiting_settings", "failed")
)
async def test_ordinary_inventory_cannot_borrow_workspace_governance(state):
    from app.services.permissions import (
        get_user_accessible_apps,
        get_user_accessible_tracks,
    )
    from app.services.workspace_governance import ordinary_authority_only

    owner, _, workspace, app, track, entry = await private_member_graph()
    app.lifecycle_state = state
    await app.save()
    with ordinary_authority_only():
        assert not await can_view_entry(owner.id, entry.id)
        assert await get_user_accessible_apps(owner.id) == []
        assert await get_user_accessible_tracks(owner.id) == []
        assert (
            await get_user_accessible_entries(owner.id, workspace_id=workspace.id) == []
        )


@pytest.mark.parametrize("state", ("installing", "awaiting_settings", "failed"))
async def test_retained_owner_data_never_activates_ordinary_direct_entry_grant(state):
    from app.services.workspace_governance import ordinary_authority_only

    owner, _, workspace, app, track, entry = await private_member_graph()
    await owner.connect(entry, edge=COLLABORATES_ON, role="viewer")
    app.lifecycle_state = state
    await app.save()
    assert not await can_view_entry(owner.id, entry.id)
    with ordinary_authority_only():
        assert not await can_view_entry(owner.id, entry.id)
        assert (
            await get_user_accessible_entries(
                owner.id, track_id=track.id, workspace_id=workspace.id
            )
            == []
        )


@pytest.mark.parametrize("projection", PROJECTIONS)
@pytest.mark.parametrize("damage", ("missing_parent", "extra_foreign_parent"))
async def test_noncanonical_entry_never_counts_or_appears(projection, damage):
    _, workspace, _, track, entry = await graph()
    reader = await reader_in(workspace)
    await reader.connect(track, edge=COLLABORATES_ON, role="viewer")
    assert_projection(
        projection, await project(projection, reader.id, workspace, track), {entry.id}
    )
    if damage == "missing_parent":
        context = await track.get_context()
        for edge in await context.find_edges_between(
            track.id, entry.id, edge_class=CONTAINS
        ):
            await edge.delete()
    else:
        _, _, _, foreign_track, _ = await graph()
        await reader.connect(foreign_track, edge=COLLABORATES_ON, role="viewer")
        await foreign_track.connect(entry, edge=CONTAINS)
    assert not await can_view_entry(reader.id, entry.id)
    assert_projection(
        projection, await project(projection, reader.id, workspace, track), set()
    )


@pytest.mark.parametrize("projection", PROJECTIONS)
@pytest.mark.parametrize("principal_form", ("graph", "auth"))
async def test_direct_entry_grant_is_discoverable_without_parent_access(
    projection, principal_form
):
    _, workspace, app, track, entry = await graph()
    reader = await reader_in(workspace)
    principal = reader.id if principal_form == "graph" else reader.user_id
    await reader.connect(entry, edge=COLLABORATES_ON, role="viewer")
    assert await resolve_role(principal, "app", app.id) is None
    assert await resolve_role(principal, "track", track.id) is None
    assert await can_view_entry(principal, entry.id)
    assert_projection(
        projection, await project(projection, principal, workspace, track), {entry.id}
    )


@pytest.mark.parametrize("projection", PROJECTIONS)
async def test_malformed_direct_entry_grant_is_not_hidden_by_hydration(projection):
    _, workspace, _, track, entry = await graph()
    reader = await reader_in(workspace)
    await reader.connect(track, edge=COLLABORATES_ON, role="viewer")
    grant = await reader.connect(entry, edge=COLLABORATES_ON, role="viewer")
    assert_projection(
        projection, await project(projection, reader.id, workspace, track), {entry.id}
    )
    context = await reader.get_context()
    await context.database.find_one_and_update(
        "edge", {"id": grant.id}, {"$set": {"context.role": {"malformed": True}}}
    )
    with pytest.raises(AuthorizationUnavailableError):
        await can_view_entry(reader.id, entry.id)
    with pytest.raises(AuthorizationUnavailableError):
        await project(projection, reader.id, workspace, track)


@pytest.mark.parametrize("projection", PROJECTIONS)
async def test_entry_candidate_storage_failure_is_unavailable(projection, monkeypatch):
    _, workspace, _, track, entry = await graph()
    reader = await reader_in(workspace)
    await reader.connect(track, edge=COLLABORATES_ON, role="viewer")
    assert_projection(
        projection, await project(projection, reader.id, workspace, track), {entry.id}
    )
    context = await reader.get_context()
    database = context.database
    original_find = database.find
    original_count = database.count

    def selects_entries(query):
        return query.get("entity") == Entry._entity_name() or any(
            selects_entries(part)
            for key in ("$and", "$or")
            for part in query.get(key, [])
        )

    async def failing_find(collection, query, **kwargs):
        if selects_entries(query):
            raise RuntimeError("injected primary candidate read outage")
        return await original_find(collection, query, **kwargs)

    async def failing_count(collection, query, **kwargs):
        if selects_entries(query):
            raise RuntimeError("injected primary candidate count outage")
        return await original_count(collection, query, **kwargs)

    monkeypatch.setattr(database, "find", failing_find)
    monkeypatch.setattr(database, "count", failing_count)
    with pytest.raises(AuthorizationUnavailableError):
        await project(projection, reader.id, workspace, track)


async def test_direct_entry_http_read_does_not_export_private_parent_content(
    authenticated_client, test_user
):
    _, workspace, app, track, entry = await graph()
    await test_user.connect(workspace, edge=IS_MEMBER_OF, role="guest")
    await test_user.connect(entry, edge=COLLABORATES_ON, role="viewer")
    app.description = "Private App description sentinel"
    await app.save()
    track.purpose = "Private Track purpose sentinel"
    await track.save()
    assert await resolve_role(test_user.id, "app", app.id) is None
    assert await resolve_role(test_user.id, "track", track.id) is None
    response = await authenticated_client.get(f"/api/entries/{entry.id}")
    assert response.status_code == 200, response.text
    exported = response.json()["entry"]
    assert exported["id"] == entry.id
    assert set(exported["track"]) <= {"id", "title", "icon", "workspace_id"}
    assert set(exported["app"]) <= {"id", "name", "icon", "workspace_id"}
    assert "sentinel" not in response.text


async def test_pages_refill_across_excluded_windows_and_recheck_next_request(
    monkeypatch,
):
    from app.services import entry_access_projection
    from app.services.pagination import DEFAULT_ENTITY_SORT

    monkeypatch.setattr(entry_access_projection, "WINDOW", 3)
    _, workspace, _, track, initial = await graph()
    reader = await reader_in(workspace)
    await reader.connect(track, edge=COLLABORATES_ON, role="viewer")
    await reader.connect(initial, edge=EXCLUDED_FROM)
    entries = []
    for index in range(12):
        entry = await Entry.create(
            title=f"Projection {index:02}",
            track_id=track.id,
            updated_at=f"2026-10-10T00:00:{index:02}Z",
        )
        await track.connect(entry, edge=CONTAINS)
        entries.append(entry)
        if index >= 5:
            await reader.connect(entry, edge=EXCLUDED_FROM)

    first, metadata = await fetch_accessible_entries_page(
        reader.id,
        track_id=track.id,
        workspace_id=workspace.id,
        limit=2,
        include_total=True,
    )
    assert [entry.id for entry in first] == [entries[4].id, entries[3].id]
    assert metadata["total"] == 5 and metadata["has_more"]
    offset = await get_user_accessible_entry_page(
        reader.id,
        track.id,
        workspace_id=workspace.id,
        limit=2,
        offset=2,
        sort=list(DEFAULT_ENTITY_SORT),
    )
    assert [entry.id for entry in offset["entries"]] == [entries[2].id, entries[1].id]
    assert offset["total"] == 5

    await reader.connect(entries[2], edge=EXCLUDED_FROM)
    second, next_metadata = await fetch_accessible_entries_page(
        reader.id,
        track_id=track.id,
        workspace_id=workspace.id,
        limit=2,
        include_total=True,
        cursor=metadata["next_cursor"],
    )
    assert [entry.id for entry in second] == [entries[1].id, entries[0].id]
    assert next_metadata["total"] == 4
    assert not next_metadata["has_more"] and next_metadata["next_cursor"] is None


async def test_parent_navigation_is_ephemeral_payload_never_authority():
    _, workspace, _, track, entry = await graph()
    reader = await reader_in(workspace)
    await reader.connect(entry, edge=COLLABORATES_ON, role="viewer")
    rows = await get_user_accessible_entries(reader.id, track_id=track.id)
    assert len(rows) == 1 and rows[0]._read_parent_navigation is not None
    assert "_read_parent_navigation" not in rows[0].model_dump()
    assert "__parent_navigation" not in await rows[0].export(flat=True)
    current = await Entry.get(entry.id)
    assert current._read_parent_navigation is None
    context = await reader.get_context()
    for grant in await context.find_edges_between(
        reader.id, entry.id, edge_class=COLLABORATES_ON
    ):
        await grant.delete()
    assert not await can_view_entry(reader.id, rows[0].id)
    assert await get_user_accessible_entries(reader.id, track_id=track.id) == []


async def test_saving_selected_entry_drops_parent_payload_from_warm_cache():
    _, workspace, _, track, entry = await graph()
    reader = await reader_in(workspace)
    await reader.connect(entry, edge=COLLABORATES_ON, role="viewer")
    rows = await get_user_accessible_entries(reader.id, track_id=track.id)
    selected = rows[0]
    assert selected._read_parent_navigation is not None
    # Internal persistence stores the same object in the framework cache.
    await selected.save()
    assert selected._read_parent_navigation is None
    warmed = await Entry.get(entry.id)
    assert warmed._read_parent_navigation is None
    _, destination, destination_app, destination_track, _ = await graph()
    await reader.connect(destination, edge=IS_MEMBER_OF, role="guest")
    context = await entry.get_context()
    for wire in await context.find_edges_between(
        track.id, entry.id, edge_class=CONTAINS
    ):
        await wire.delete()
    await destination_track.connect(warmed, edge=CONTAINS)
    warmed.track_id = destination_track.id
    await warmed.save()
    assert await can_view_entry(reader.id, entry.id)
    moved = await get_user_accessible_entries(
        reader.id,
        track_id=destination_track.id,
        workspace_id=destination.id,
    )
    assert len(moved) == 1
    assert moved[0]._read_parent_navigation["track"]["id"] == destination_track.id
    assert moved[0]._read_parent_navigation["app"]["id"] == destination_app.id
    assert moved[0]._read_parent_navigation["track"]["workspace_id"] == destination.id
    assert await get_user_accessible_entries(reader.id, track_id=track.id) == []


@pytest.mark.parametrize("secondary_direction", (1, -1))
@pytest.mark.parametrize("compound", (True, False))
@pytest.mark.parametrize("null_form", ("explicit", "missing"))
@pytest.mark.parametrize("secondary_type", ("text", "number"))
async def test_compound_sort_pages_preserve_secondary_keys_and_null_tail(
    secondary_direction,
    compound,
    null_form,
    secondary_type,
):
    from app.services.entry_access_projection import select_page

    _, workspace, _, track, initial = await graph()
    reader = await reader_in(workspace)
    await reader.connect(track, edge=COLLABORATES_ON, role="viewer")
    await reader.connect(initial, edge=EXCLUDED_FROM)
    entries = []
    for index, secondary in enumerate(("c", "a", "b", None)):
        entry = await Entry.create(
            track_id=track.id,
            title="Same primary",
            custom_fields={"secondary": secondary},
        )
        await track.connect(entry, edge=CONTAINS)
        entries.append(entry)
    entries.sort(key=lambda entry: entry.id)
    values = (10, 9, 2, None) if secondary_type == "number" else ("c", "b", "a", None)
    for entry, secondary in zip(entries, values):
        entry.custom_fields = (
            {}
            if secondary is None and null_form == "missing"
            else {"secondary": secondary}
        )
        await entry.save()
    order = (
        [entries[2].id, entries[1].id, entries[0].id]
        if secondary_direction == 1
        else [entries[0].id, entries[1].id, entries[2].id]
    ) + [entries[3].id]
    sort = [
        ("context.title", 1),
        ("context.custom_fields.secondary", secondary_direction),
        ("id", 1),
    ]
    if not compound:
        sort = sort[1:]
    cursor = None
    found = []
    for _ in range(5):
        rows, metadata = await select_page(
            reader.id,
            {"context.track_id": track.id},
            workspace_id=workspace.id,
            track_id=track.id,
            sort=sort,
            cursor=cursor,
            limit=1,
            include_total=True,
        )
        found.extend(row.id for row in rows)
        assert metadata["total"] == 4
        if not metadata["has_more"]:
            break
        cursor = metadata["next_cursor"]
    assert found == order


async def test_portable_sort_keeps_bounded_page_heap_and_current_audience(monkeypatch):
    import heapq
    from types import SimpleNamespace

    from app.services import entry_access_projection

    _, workspace, _, track, initial = await graph()
    reader = await reader_in(workspace)
    await reader.connect(track, edge=COLLABORATES_ON, role="viewer")
    await reader.connect(initial, edge=EXCLUDED_FROM)
    entries = []
    for value in range(25):
        entry = await Entry.create(track_id=track.id, custom_fields={"amount": value})
        await track.connect(entry, edge=CONTAINS)
        entries.append(entry)
        if value >= 15:
            await reader.connect(entry, edge=EXCLUDED_FROM)
    await reader.connect(entries[24], edge=COLLABORATES_ON, role="viewer")
    peak = 0

    def push(heap, item):
        nonlocal peak
        heapq.heappush(heap, item)
        peak = max(peak, len(heap))

    def replace(heap, item):
        nonlocal peak
        heapq.heapreplace(heap, item)
        peak = max(peak, len(heap))

    # Replace the service binding, preserving other framework/event-loop heaps.
    monkeypatch.setattr(
        entry_access_projection,
        "heapq",
        SimpleNamespace(heappush=push, heapreplace=replace),
    )
    monkeypatch.setattr(entry_access_projection, "WINDOW", 3)
    rows, metadata = await entry_access_projection.select_page(
        reader.id,
        {"context.track_id": track.id},
        track_id=track.id,
        workspace_id=workspace.id,
        sort=[("context.custom_fields.amount", 1)],
        limit=3,
        offset=4,
        include_total=True,
    )
    assert [entry.id for entry in rows] == [entries[4].id, entries[5].id, entries[6].id]
    assert metadata["total"] == 16 and metadata["has_more"]
    assert peak == 8  # offset + page + one lookahead, independent of 25 candidates


@pytest.mark.parametrize("direction", (1, -1))
async def test_native_date_cursor_includes_explicit_null_and_missing_tail(direction):
    from app.services.entry_access_projection import select_page

    _, workspace, _, track, initial = await graph()
    reader = await reader_in(workspace)
    await reader.connect(track, edge=COLLABORATES_ON, role="viewer")
    await reader.connect(initial, edge=EXCLUDED_FROM)
    entries = []
    for value in ("2026-10-09T00:00:00Z", "2026-10-10T00:00:00Z", None, None):
        entry = await Entry.create(track_id=track.id, updated_at=value)
        await track.connect(entry, edge=CONTAINS)
        entries.append(entry)
    context = await track.get_context()
    await context.database.find_one_and_update(
        "node",
        {"id": entries[3].id},
        {"$unset": {"context.updated_at": True}},
    )
    dates = [entries[0].id, entries[1].id]
    expected = (dates if direction == 1 else dates[::-1]) + sorted(
        [entries[2].id, entries[3].id],
        reverse=direction < 0,
    )
    cursor = None
    actual = []
    for _ in range(5):
        rows, metadata = await select_page(
            reader.id,
            {"context.track_id": track.id},
            track_id=track.id,
            workspace_id=workspace.id,
            sort=[("context.updated_at", direction)],
            limit=1,
            cursor=cursor,
            include_total=True,
        )
        actual.extend(entry.id for entry in rows)
        assert metadata["total"] == 4
        if not metadata["has_more"]:
            break
        cursor = metadata["next_cursor"]
    assert actual == expected


async def test_ranked_selection_is_bounded_before_pagination_and_rechecks_denies():
    _, workspace, _, track, excluded = await graph()
    reader = await reader_in(workspace)
    await reader.connect(track, edge=COLLABORATES_ON, role="viewer")
    await reader.connect(excluded, edge=EXCLUDED_FROM)
    chosen = await Entry.create(title="Older chosen record", track_id=track.id)
    await track.connect(chosen, edge=CONTAINS)
    for i in range(5):
        unrelated = await Entry.create(title=f"Unrelated {i}", track_id=track.id)
        await track.connect(unrelated, edge=CONTAINS)
    _, foreign_workspace, _, foreign_track, foreign_entry = await graph()
    assert foreign_workspace.id != workspace.id
    rows, metadata = await fetch_accessible_entries_page(
        reader.id,
        workspace_id=workspace.id,
        entry_ids=[chosen.id, excluded.id, foreign_entry.id],
        limit=2,
        include_total=True,
    )
    assert [row.id for row in rows] == [chosen.id]
    assert metadata["total"] == 1 and not metadata["has_more"]
    empty, metadata = await fetch_accessible_entries_page(
        reader.id, workspace_id=workspace.id, entry_ids=[], include_total=True
    )
    assert empty == [] and metadata["total"] == 0


async def test_ranked_selection_parser_rejects_unbounded_ids():
    from app.api.errors import BadRequestError
    from app.services.entry_listing import selected_entry_ids

    assert selected_entry_ids(None) is None
    assert selected_entry_ids("") == []
    assert selected_entry_ids(" e1,e1,e2 ") == ["e1", "e2"]
    with pytest.raises(BadRequestError):
        selected_entry_ids(",".join(f"e{i}" for i in range(51)))
    with pytest.raises(BadRequestError):
        selected_entry_ids("x" * 201)
