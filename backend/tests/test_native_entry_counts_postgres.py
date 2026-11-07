"""Qualify optional native aggregates against canonical current authority."""

import os

import pytest
from jvspatial.core.context import get_default_context

from app.exceptions import AuthorizationUnavailableError
from app.models.edges import CONTAINS, EXCLUDED_FROM
from app.services.permissions import count_user_accessible_entries
from app.services.workspace_governance import ordinary_authority_only
from tests.test_resource_location import graph

pytestmark = [
    pytest.mark.asyncio,
    pytest.mark.postgres,
    pytest.mark.skipif(
        os.getenv("INTEGRAL_TEST_DB") != "postgres", reason="PostgreSQL qualification"
    ),
]


@pytest.fixture
def native_database():
    database = get_default_context().database
    if not getattr(database, "supports_native_graph_binding_summary", False):
        if os.getenv("INTEGRAL_REQUIRE_NATIVE_BINDING_SUMMARY") == "1":
            pytest.fail(
                "The qualified candidate must provide native graph binding summaries"
            )
        pytest.skip("Optional framework optimization is not installed")
    return database


async def test_native_count_executes_and_rechecks_rooted_owner(
    native_database, monkeypatch
):
    owner, _, _, track, _ = await graph()
    original = native_database.summarize_graph_bindings
    called = []

    async def observed(*args, **kwargs):
        called.append(kwargs)
        return await original(*args, **kwargs)

    monkeypatch.setattr(native_database, "summarize_graph_bindings", observed)
    assert await count_user_accessible_entries(owner.id, track.id) == 1
    assert len(called) == 1


async def test_native_binding_count_preserves_nested_and_typed_predicates(
    native_database,
):
    from app.services.entry_access_projection import count_bound_records

    owner, workspace, _, track, entry = await graph()
    entry.custom_fields = {"flag": False}
    await entry.save()
    nested = {
        "$and": [
            {"context.track_id": track.id},
            {"$and": [{"context.track_id": track.id}, {"entity": "Entry"}]},
        ]
    }
    assert (
        await count_bound_records(owner.id, track.id, nested, workspace_id=workspace.id)
        == 1
    )
    # Python considers False == 0. Persistence comparisons retain their types;
    # query normalization must never collapse these contradictory constraints.
    # The pinned adapter refuses a numeric comparison against a stored
    # boolean. Preserve that unavailable outcome instead of dropping the
    # contradictory numeric predicate and authorizing a matching record.
    with pytest.raises(AuthorizationUnavailableError):
        await count_bound_records(
            owner.id,
            track.id,
            {
                "$and": [
                    nested,
                    {"context.custom_fields.flag": False},
                    {"context.custom_fields.flag": 0},
                ]
            },
            workspace_id=workspace.id,
        )


@pytest.mark.parametrize(
    "damage",
    [
        "visibility_null",
        "visibility_unknown",
        "legacy_conflict",
        "missing_parent",
        "extra_parent",
        "payload_only",
    ],
)
async def test_native_count_matches_raw_integrity_contract(native_database, damage):
    owner, _, _, track, entry = await graph()
    record = (await native_database.find("node", {"id": entry.id}))[0]
    if damage == "visibility_null":
        record["context"]["visibility"] = None
    elif damage == "visibility_unknown":
        record["context"]["visibility"] = "unknown"
    elif damage == "legacy_conflict":
        record["track_id"] = "n.Track.inconsistent"
    elif damage == "payload_only":
        record["context"]["title"] = None
    if damage == "missing_parent":
        edge = (
            await native_database.find(
                "edge", {"source": track.id, "target": entry.id, "entity": "CONTAINS"}
            )
        )[0]
        await native_database.delete("edge", edge["id"])
    elif damage == "extra_parent":
        _, _, _, foreign_track, _ = await graph()
        await foreign_track.connect(entry, edge=CONTAINS)
    else:
        await native_database.save("node", record)
    if damage in ("visibility_null", "visibility_unknown", "legacy_conflict"):
        with pytest.raises(AuthorizationUnavailableError):
            await count_user_accessible_entries(owner.id, track.id)
    else:
        assert await count_user_accessible_entries(owner.id, track.id) == (
            1 if damage == "payload_only" else 0
        )


async def test_ordinary_authority_keeps_entry_exclusion_in_native_candidates(
    native_database, monkeypatch
):
    owner, _, _, track, entry = await graph()
    await owner.connect(entry, edge=EXCLUDED_FROM)

    original = native_database.summarize_graph_bindings
    queries = []

    async def observed(query, **kwargs):
        queries.append(query)
        return await original(query, **kwargs)

    monkeypatch.setattr(native_database, "summarize_graph_bindings", observed)
    with ordinary_authority_only():
        assert await count_user_accessible_entries(owner.id, track.id) == 0
    assert len(queries) == 1
    assert entry.id in repr(queries[0]) and "$nin" in repr(queries[0])


async def test_inherited_editor_uses_complete_native_count(
    native_database, monkeypatch
):
    from app.models.edges import COLLABORATES_ON, IS_MEMBER_OF
    from app.models.nodes import User
    from app.services import entry_access_projection
    from app.services.app_graph import catalog_user

    _, workspace, _, track, _ = await graph()
    reader = await User.create(display_name="Inherited count QA")
    await catalog_user(reader)
    await reader.connect(workspace, edge=IS_MEMBER_OF, role="member")
    await reader.connect(track, edge=COLLABORATES_ON, role="editor")
    calls = []
    original = native_database.summarize_graph_bindings

    async def observed(*args, **kwargs):
        calls.append(kwargs)
        return await original(*args, **kwargs)

    async def forbidden(*args, **kwargs):
        pytest.fail("A uniform inherited count must not scan admission windows")

    monkeypatch.setattr(native_database, "summarize_graph_bindings", observed)
    monkeypatch.setattr(entry_access_projection, "admit_records", forbidden)
    assert await count_user_accessible_entries(reader.id, track.id) == 1
    assert len(calls) == 1


async def test_entry_exception_created_during_aggregate_refuses_total(
    native_database, monkeypatch
):
    from app.models.edges import COLLABORATES_ON, IS_MEMBER_OF
    from app.models.nodes import User
    from app.services.app_graph import catalog_user

    _, workspace, _, track, entry = await graph()
    reader = await User.create(display_name="Count revoke QA")
    await catalog_user(reader)
    await reader.connect(workspace, edge=IS_MEMBER_OF, role="member")
    await reader.connect(track, edge=COLLABORATES_ON, role="editor")
    original = native_database.summarize_graph_bindings

    async def revoke(*args, **kwargs):
        result = await original(*args, **kwargs)
        await reader.connect(entry, edge=EXCLUDED_FROM)
        return result

    monkeypatch.setattr(native_database, "summarize_graph_bindings", revoke)
    with pytest.raises(AuthorizationUnavailableError):
        await count_user_accessible_entries(reader.id, track.id)
    assert await count_user_accessible_entries(reader.id, track.id) == 0


async def test_native_source_failure_is_not_zero(native_database, monkeypatch):
    owner, _, _, track, _ = await graph()

    async def unavailable(*args, **kwargs):
        raise RuntimeError("native source failed")

    monkeypatch.setattr(native_database, "summarize_graph_bindings", unavailable)
    with pytest.raises(AuthorizationUnavailableError):
        await count_user_accessible_entries(owner.id, track.id)


async def test_ownership_changed_during_aggregate_cannot_return_a_total(
    native_database, monkeypatch
):
    from app.models.edges import IS_MEMBER_OF

    owner, workspace, _, track, _ = await graph()
    original = native_database.summarize_graph_bindings

    async def revoke_after_read(*args, **kwargs):
        result = await original(*args, **kwargs)
        edge = (
            await native_database.find(
                "edge",
                {
                    "source": owner.id,
                    "target": workspace.id,
                    "entity": IS_MEMBER_OF._entity_name(),
                },
            )
        )[0]
        edge["context"]["role"] = "member"
        await native_database.save("edge", edge)
        return result

    monkeypatch.setattr(native_database, "summarize_graph_bindings", revoke_after_read)
    with pytest.raises(AuthorizationUnavailableError):
        await count_user_accessible_entries(owner.id, track.id)


@pytest.mark.parametrize(
    "query,title,body",
    [
        ("kelvin", "\u212aelvin", ""),
        ("hi", "h\u0130", ""),
        ("id", "\u0130d", ""),
        ("budget OR invoice", "Unrelated", "INVOICE"),
        ("and or the", "No terms", ""),
        ("niño", "NIÑO", ""),
    ],
)
async def test_keyword_prefilter_preserves_canonical_matches_on_postgres(
    native_database, query, title, body
):
    from app.services.agent_insights import query_entries
    from app.services.retrieval.keyword_match import matches_keywords, tokenize

    owner, _, _, track, entry = await graph()
    entry.title = title
    entry.body = body
    await entry.save()
    result = await query_entries(
        user_id=owner.id, track_id=track.id, query=query, limit=20
    )
    expected = (
        [entry.id] if matches_keywords(title + " " + body, tokenize(query)) else []
    )
    assert [row["id"] for row in result["entries"]] == expected
    assert result["total"] == len(expected)


async def test_incidental_catalog_edge_is_not_an_entry_authority_fact(
    native_database, monkeypatch
):
    from unittest.mock import AsyncMock

    owner, _, _, track, entry = await graph()
    await native_database.save(
        "edge",
        {
            "id": "e.CATALOGS.incidental",
            "entity": "CATALOGS",
            "source": None,
            "target": entry.id,
            "context": {},
        },
    )
    assert await count_user_accessible_entries(owner.id, track.id) == 1
    monkeypatch.setattr(
        native_database, "summarize_graph_bindings", AsyncMock(return_value=None)
    )
    assert await count_user_accessible_entries(owner.id, track.id) == 1


@pytest.mark.parametrize("parent_grant", [True, False])
@pytest.mark.parametrize("direct_override", [True, False])
async def test_bounded_entry_exceptions_use_canonical_admission_without_full_scan(
    native_database, monkeypatch, parent_grant, direct_override
):
    from app.models.edges import COLLABORATES_ON, IS_MEMBER_OF
    from app.models.nodes import User
    from app.services import entry_access_projection
    from app.services.app_graph import catalog_user

    _, workspace, _, track, entry = await graph()
    reader = await User.create(display_name="Bounded exceptions QA")
    await catalog_user(reader)
    await reader.connect(workspace, edge=IS_MEMBER_OF, role="member")
    if parent_grant:
        await reader.connect(track, edge=COLLABORATES_ON, role="viewer")
    await reader.connect(entry, edge=EXCLUDED_FROM)
    if direct_override:
        await reader.connect(entry, edge=COLLABORATES_ON, role="viewer")
    original = entry_access_projection.admit_records
    inspected = []

    async def bounded(user_id, rows, **kwargs):
        inspected.append([row["id"] for row in rows])
        assert len(rows) == 1 and rows[0]["id"] == entry.id
        return await original(user_id, rows, **kwargs)

    async def no_stream(*args, **kwargs):
        pytest.fail("A complete bounded exception proof must not scan the Track")
        yield

    monkeypatch.setattr(entry_access_projection, "admit_records", bounded)
    monkeypatch.setattr(entry_access_projection, "iter_records", no_stream)
    assert await count_user_accessible_entries(reader.id, track.id) == int(
        direct_override
    )
    assert len(inspected) == 2


async def exception_reader(workspace, track, *, parent_grant=True):
    from app.models.edges import COLLABORATES_ON, IS_MEMBER_OF
    from app.models.nodes import User
    from app.services.app_graph import catalog_user

    reader = await User.create(display_name="Exception authority QA")
    await catalog_user(reader)
    await reader.connect(workspace, edge=IS_MEMBER_OF, role="member")
    if parent_grant:
        await reader.connect(track, edge=COLLABORATES_ON, role="viewer")
    return reader


@pytest.mark.parametrize("include_total", [True, False])
async def test_direct_only_page_does_not_scan_the_unshared_track(
    native_database, monkeypatch, include_total
):
    from app.models.edges import COLLABORATES_ON
    from app.models.nodes import Entry
    from app.services import entry_access_projection

    _, workspace, _, track, shared = await graph()
    for index in range(40):
        entry = await Entry.create(track_id=track.id, title=f"Private {index}")
        await track.connect(entry, edge=CONTAINS)
    reader = await exception_reader(workspace, track, parent_grant=False)
    await reader.connect(shared, edge=COLLABORATES_ON, role="viewer")
    inspected = []
    original = entry_access_projection.admit_records

    async def observed(user_id, records, **kwargs):
        inspected.append(len(records))
        return await original(user_id, records, **kwargs)

    monkeypatch.setattr(entry_access_projection, "admit_records", observed)
    entries, metadata = await entry_access_projection.select_page(
        reader.id,
        {"context.track_id": track.id},
        track_id=track.id,
        workspace_id=workspace.id,
        sort=[("id", 1)],
        limit=2,
        include_total=include_total,
    )
    assert [entry.id for entry in entries] == [shared.id]
    assert metadata["total"] == (1 if include_total else None)
    assert metadata["has_more"] is False and metadata["next_cursor"] is None
    assert sum(inspected) <= 16, "A sparse direct share must not walk private rows"


async def test_sparse_direct_pages_keep_cursor_order_and_exact_totals(
    native_database,
):
    from app.models.edges import COLLABORATES_ON
    from app.models.nodes import Entry
    from app.services.entry_access_projection import select_page

    _, workspace, _, track, first = await graph()
    records = [first]
    for index in range(8):
        entry = await Entry.create(track_id=track.id, title=f"Cursor fixture {index}")
        await track.connect(entry, edge=CONTAINS)
        records.append(entry)
    reader = await exception_reader(workspace, track, parent_grant=False)
    allowed = sorted(records, key=lambda record: record.id)[-3:]
    for record in allowed:
        await reader.connect(record, edge=COLLABORATES_ON, role="viewer")
    for include_total in (True, False):
        cursor, seen = None, []
        for _ in range(3):
            entries, metadata = await select_page(
                reader.id,
                {"context.track_id": track.id},
                track_id=track.id,
                workspace_id=workspace.id,
                sort=[("id", 1)],
                cursor=cursor,
                limit=2,
                include_total=include_total,
            )
            seen.extend(record.id for record in entries)
            assert metadata["total"] == (3 if include_total else None)
            if not metadata["has_more"]:
                break
            cursor = metadata["next_cursor"]
        else:
            pytest.fail("Sparse pagination must terminate")
        assert seen == [record.id for record in allowed]


@pytest.mark.parametrize("change", ["revoke", "source_failure"])
async def test_sparse_page_proof_is_fresh_after_selection(
    native_database, monkeypatch, change
):
    from app.models.edges import COLLABORATES_ON
    from app.models.nodes import Entry
    from app.services import entry_access_projection

    _, workspace, _, track, first = await graph()
    records = [first]
    for _ in range(6):
        entry = await Entry.create(track_id=track.id)
        await track.connect(entry, edge=CONTAINS)
        records.append(entry)
    reader = await exception_reader(workspace, track, parent_grant=False)
    target = max(records, key=lambda record: record.id)
    grant = await reader.connect(target, edge=COLLABORATES_ON, role="viewer")
    proof = entry_access_projection._inherited_count_authority
    calls = []

    async def change_after_selection(*args, **kwargs):
        calls.append(True)
        if len(calls) == 2:
            if change == "source_failure":
                raise RuntimeError("Synthetic final sparse-proof failure")
            await grant.delete()
        return await proof(*args, **kwargs)

    monkeypatch.setattr(
        entry_access_projection, "_inherited_count_authority", change_after_selection
    )
    with pytest.raises(AuthorizationUnavailableError):
        await entry_access_projection.select_page(
            reader.id,
            {"context.track_id": track.id},
            track_id=track.id,
            workspace_id=workspace.id,
            sort=[("id", 1)],
            limit=2,
            include_total=False,
        )
    assert len(calls) == 2


async def test_ordinary_no_total_page_needs_no_extra_audience_proof(
    native_database, monkeypatch
):
    from app.models.nodes import Entry
    from app.services import entry_access_projection

    _, workspace, _, track, _ = await graph()
    for _ in range(2):
        entry = await Entry.create(track_id=track.id)
        await track.connect(entry, edge=CONTAINS)
    reader = await exception_reader(workspace, track)

    async def unnecessary(*args, **kwargs):
        pytest.fail("An ordinary full page must not repeat the audience proof")

    monkeypatch.setattr(
        entry_access_projection, "_inherited_count_authority", unnecessary
    )
    records, metadata = await entry_access_projection.select_page(
        reader.id,
        {"context.track_id": track.id},
        track_id=track.id,
        workspace_id=workspace.id,
        sort=[("id", 1)],
        limit=2,
        include_total=False,
    )
    assert len(records) == 2 and metadata["has_more"]


async def test_absolute_entry_block_beats_explicit_grant_in_native_total(
    native_database,
):
    from app.models.edges import COLLABORATES_ON
    from app.services.resource_access_blocks import set_resource_block

    owner, workspace, _, track, entry = await graph()
    reader = await exception_reader(workspace, track)
    await reader.connect(entry, edge=COLLABORATES_ON, role="viewer")
    await set_resource_block(
        actor_id=owner.id,
        resource_type="entry",
        resource_id=entry.id,
        subject_id=reader.id,
        blocked=True,
    )
    assert await count_user_accessible_entries(reader.id, track.id) == 0
    await set_resource_block(
        actor_id=owner.id,
        resource_type="entry",
        resource_id=entry.id,
        subject_id=reader.id,
        blocked=False,
    )
    assert await count_user_accessible_entries(reader.id, track.id) == 1


async def test_bounded_direct_grant_revocation_during_aggregate_refuses_total(
    native_database, monkeypatch
):
    from app.models.edges import COLLABORATES_ON

    _, workspace, _, track, entry = await graph()
    reader = await exception_reader(workspace, track)
    await reader.connect(entry, edge=EXCLUDED_FROM)
    grant = await reader.connect(entry, edge=COLLABORATES_ON, role="viewer")
    original = native_database.summarize_graph_bindings

    async def revoke(*args, **kwargs):
        result = await original(*args, **kwargs)
        await grant.delete()
        return result

    monkeypatch.setattr(native_database, "summarize_graph_bindings", revoke)
    with pytest.raises(AuthorizationUnavailableError):
        await count_user_accessible_entries(reader.id, track.id)
    assert await count_user_accessible_entries(reader.id, track.id) == 0


async def test_bounded_exception_probe_overflow_retains_canonical_stream(
    native_database, monkeypatch
):
    from app.services import entry_access_projection

    _, workspace, _, track, _ = await graph()
    reader = await exception_reader(workspace, track)
    original = native_database.find
    observed = []
    iterator = entry_access_projection.iter_records

    async def overflowing(collection, query, *args, **kwargs):
        if (
            collection == "edge"
            and query.get("source") == reader.id
            and kwargs.get("limit") == 1001
        ):
            return [
                {
                    "id": f"overflow-{index}",
                    "entity": "EXCLUDED_FROM",
                    "source": reader.id,
                    "target": f"missing-{index}",
                }
                for index in range(1001)
            ]
        return await original(collection, query, *args, **kwargs)

    async def streamed(*args, **kwargs):
        observed.append(True)
        async for rows in iterator(*args, **kwargs):
            yield rows

    monkeypatch.setattr(native_database, "find", overflowing)
    monkeypatch.setattr(entry_access_projection, "iter_records", streamed)
    assert await count_user_accessible_entries(reader.id, track.id) == 1
    assert observed


@pytest.mark.parametrize("direction", [1, -1])
async def test_native_numeric_pages_keep_numeric_order_and_null_tail(
    native_database, monkeypatch, direction
):
    from app.models.nodes import Entry
    from app.services import entry_access_projection

    _, workspace, _, track, initial = await graph()
    initial.custom_fields = {"amount": 10}
    await initial.save()
    records = [initial]
    for value in (9, 2, 0, None):
        entry = await Entry.create(track_id=track.id, custom_fields={"amount": value})
        await track.connect(entry, edge=CONTAINS)
        records.append(entry)
    missing = await Entry.create(track_id=track.id)
    await track.connect(missing, edge=CONTAINS)
    records.append(missing)
    reader = await exception_reader(workspace, track)
    assert native_database.can_pushdown_numeric_sort(
        {}, [("context.custom_fields.amount", direction)]
    )

    async def forbidden(*args, **kwargs):
        pytest.fail("A homogeneous numeric page must not run the portable heap")

    monkeypatch.setattr(entry_access_projection, "_select_portable_page", forbidden)
    expected = sorted(
        [row for row in records if row.custom_fields.get("amount") is not None],
        key=lambda row: (row.custom_fields["amount"], row.id),
        reverse=direction == -1,
    )
    expected += sorted(
        [row for row in records if row.custom_fields.get("amount") is None],
        key=lambda row: row.id,
        reverse=direction == -1,
    )
    seen, cursor = [], None
    for _ in range(4):
        rows, metadata = await entry_access_projection.select_page(
            reader.id,
            {"context.track_id": track.id},
            track_id=track.id,
            workspace_id=workspace.id,
            sort=[("context.custom_fields.amount", direction), ("id", direction)],
            cursor=cursor,
            include_total=True,
            limit=2,
        )
        assert metadata["total"] == len(records)
        seen.extend(row.id for row in rows)
        cursor = metadata["next_cursor"]
        if not cursor:
            break
    assert seen == [row.id for row in expected] and not cursor


async def test_numeric_type_change_during_selection_refuses_partial_order(
    native_database, monkeypatch
):
    from app.services import entry_access_projection

    _, workspace, _, track, entry = await graph()
    entry.custom_fields = {"amount": 2}
    await entry.save()
    reader = await exception_reader(workspace, track)
    original = native_database.find
    ordering = [("context.custom_fields.amount", 1), ("id", 1)]
    changed = []

    async def change_type(collection, query, *args, **kwargs):
        rows = await original(collection, query, *args, **kwargs)
        if collection == "node" and kwargs.get("sort") == ordering and not changed:
            changed.append(True)
            await native_database.find_one_and_update(
                "node",
                {"id": entry.id},
                {"$set": {"context.custom_fields.amount": "Changed type"}},
            )
        return rows

    monkeypatch.setattr(native_database, "find", change_type)
    with pytest.raises(AuthorizationUnavailableError):
        await entry_access_projection.select_page(
            reader.id,
            {"context.track_id": track.id},
            workspace_id=workspace.id,
            track_id=track.id,
            sort=ordering,
            limit=2,
            include_total=False,
        )
    assert changed


@pytest.mark.parametrize("values", [(False, 0, 2, None), ("Z", "a", "é", "😀")])
async def test_non_numeric_values_keep_the_portable_comparator(
    native_database, monkeypatch, values
):
    from app.models.nodes import Entry
    from app.services import entry_access_projection

    _, workspace, _, track, initial = await graph()
    initial.custom_fields = {"amount": values[0]}
    await initial.save()
    records = [initial]
    for value in values[1:]:
        entry = await Entry.create(track_id=track.id, custom_fields={"amount": value})
        await track.connect(entry, edge=CONTAINS)
        records.append(entry)
    reader = await exception_reader(workspace, track)
    original = entry_access_projection._select_portable_page
    calls = []

    async def observed(*args, **kwargs):
        calls.append(True)
        return await original(*args, **kwargs)

    monkeypatch.setattr(entry_access_projection, "_select_portable_page", observed)
    rows, _ = await entry_access_projection.select_page(
        reader.id,
        {"context.track_id": track.id},
        workspace_id=workspace.id,
        track_id=track.id,
        sort=[("context.custom_fields.amount", 1), ("id", 1)],
        limit=10,
        include_total=False,
    )
    expected = sorted(
        records,
        key=lambda row: (
            row.custom_fields["amount"] is None,
            row.custom_fields["amount"],
            row.id,
        ),
    )
    assert [row.id for row in rows] == [row.id for row in expected]
    assert calls


async def test_entry_revoked_between_total_and_page_cannot_release_old_total(
    native_database, monkeypatch
):
    from app.services import entry_access_projection

    _, workspace, _, track, entry = await graph()
    reader = await exception_reader(workspace, track)
    original = entry_access_projection._select_native_page
    revoked = []

    async def select_after_revoke(*args, **kwargs):
        if not revoked:
            revoked.append(True)
            await reader.connect(entry, edge=EXCLUDED_FROM)
        return await original(*args, **kwargs)

    monkeypatch.setattr(
        entry_access_projection, "_select_native_page", select_after_revoke
    )
    with pytest.raises(AuthorizationUnavailableError):
        await entry_access_projection.select_page(
            reader.id,
            {"context.track_id": track.id},
            track_id=track.id,
            workspace_id=workspace.id,
            limit=20,
            include_total=True,
        )
    assert revoked


@pytest.mark.parametrize("direction", [1, -1])
async def test_native_compound_numeric_cursor_keeps_every_key(
    native_database, direction
):
    from app.models.nodes import Entry
    from app.services.entry_access_projection import select_page

    _, workspace, _, track, initial = await graph()
    pairs = [(10, 2), (9, 9), (9, 2), (2, 10), (None, 2), (None, None), (None, 3)]
    records = []
    for index, (amount, secondary) in enumerate(pairs):
        fields = {"amount": amount, "secondary": secondary}
        if index == 6:
            fields.pop("amount")
        if index == 0:
            initial.custom_fields = fields
            await initial.save()
            entry = initial
        else:
            entry = await Entry.create(track_id=track.id, custom_fields=fields)
            await track.connect(entry, edge=CONTAINS)
        records.append(entry)
    reader = await exception_reader(workspace, track)
    expected = [3, 1, 2, 0, 6, 4, 5] if direction == 1 else [0, 2, 1, 3, 4, 6, 5]
    ordering = [
        ("context.custom_fields.amount", direction),
        ("context.custom_fields.secondary", -direction),
        ("id", 1),
    ]
    seen, cursor = [], None
    for _ in range(5):
        rows, metadata = await select_page(
            reader.id,
            {"context.track_id": track.id},
            workspace_id=workspace.id,
            track_id=track.id,
            sort=ordering,
            limit=2,
            cursor=cursor,
            include_total=False,
        )
        seen.extend(row.id for row in rows)
        cursor = metadata["next_cursor"]
        if not cursor:
            break
    assert seen == [records[index].id for index in expected] and not cursor


async def test_authority_source_failure_after_page_is_unavailable(
    native_database, monkeypatch
):
    from app.services import entry_access_projection

    _, workspace, _, track, _ = await graph()
    reader = await exception_reader(workspace, track)
    selected = []
    original_select = entry_access_projection._select_native_page
    original_find = native_database.find

    async def fail_probe(collection, query, *args, **kwargs):
        if (
            selected
            and collection == "edge"
            and query.get("source") == reader.id
            and kwargs.get("limit") == 1001
        ):
            raise RuntimeError("Injected final authority-source failure")
        return await original_find(collection, query, *args, **kwargs)

    async def page_then_fail(*args, **kwargs):
        result = await original_select(*args, **kwargs)
        selected.append(True)
        return result

    monkeypatch.setattr(native_database, "find", fail_probe)
    monkeypatch.setattr(entry_access_projection, "_select_native_page", page_then_fail)
    with pytest.raises(AuthorizationUnavailableError):
        await entry_access_projection.select_page(
            reader.id,
            {"context.track_id": track.id},
            workspace_id=workspace.id,
            track_id=track.id,
            limit=20,
            include_total=True,
        )
    assert selected
