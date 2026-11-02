"""Exact QuerySpec filter semantics stay explicit and complete."""

from types import SimpleNamespace

import pytest

from app.api.errors import BadRequestError, QueryUnavailableError
from app.schemas.governed_query import FilterExpr, QuerySpec
from app.services.governed_query.engine import (
    _filter_matches,
    _iter_track_entries,
    _matches_core_filters,
    _run_core_open,
    _serialize_core_node,
    _sort_core_nodes,
)
from app.services.query_filters import normalize_filter_expressions


@pytest.mark.parametrize(
    ("value", "op", "expected", "matches"),
    [
        ("Available", "eq", "Available", True),
        ("Available", "neq", "Maintenance", True),
        ("Available", "in", ["Maintenance", "Available"], True),
        (["urgent", "rental"], "contains", "rental", True),
        (None, "exists", False, True),
        (3, "gte", 2, True),
        (3, "lte", 2, False),
    ],
)
def test_filter_matches_declared_queryspec_semantics(value, op, expected, matches):
    assert _filter_matches(value, op=op, expected=expected) is matches


def test_legacy_dashboard_filter_map_has_explicit_queryspec_equivalent():
    filters = normalize_filter_expressions(
        {"custom_fields.status": ["Available", "Maintenance"]}
    )

    assert [(item.field, item.op, item.value) for item in filters] == [
        ("custom_fields.status", "in", ["Available", "Maintenance"])
    ]


def test_ordered_filter_type_mismatch_is_explicit_not_no_results():
    with pytest.raises(BadRequestError, match="cannot compare"):
        _filter_matches("not-a-number", op="gte", expected=2)


def test_core_track_query_uses_declared_filters_projection_and_stable_sort():
    rows = [
        SimpleNamespace(id="t2", title="Zulu", workspace_id="ws", kind=""),
        SimpleNamespace(
            id="t1", title="Alpha", workspace_id="ws", kind="agent_scratch"
        ),
    ]
    filtered = [
        row
        for row in rows
        if _matches_core_filters(
            "track", row, [FilterExpr(field="kind", op="eq", value="")]
        )
    ]

    ordered = _sort_core_nodes("track", filtered, "title")

    assert [_serialize_core_node("track", row, ["id", "title"]) for row in ordered] == [
        {"kind": "track", "id": "t2", "title": "Zulu"}
    ]


def test_core_app_query_uses_name_and_descending_sort():
    rows = [
        SimpleNamespace(
            id="a1",
            name="Alpha",
            workspace_id="ws",
            lifecycle_state="active",
            installed_package_slug=None,
        ),
        SimpleNamespace(
            id="a2",
            name="Zulu",
            workspace_id="ws",
            lifecycle_state="paused",
            installed_package_slug="asset-register",
        ),
    ]
    filtered = [
        row
        for row in rows
        if _matches_core_filters(
            "app", row, [FilterExpr(field="lifecycle_state", op="neq", value="paused")]
        )
    ]

    assert [row.name for row in _sort_core_nodes("app", filtered, "-name")] == ["Alpha"]


def test_core_query_rejects_unknown_resource_field_instead_of_ignoring_it():
    row = SimpleNamespace(id="a1", name="Alpha")

    with pytest.raises(BadRequestError, match="unsupported app filter field"):
        _matches_core_filters(
            "app", row, [FilterExpr(field="custom_fields.status", op="eq", value="x")]
        )


@pytest.mark.asyncio
async def test_core_entry_query_walks_all_track_pages_without_hidden_cap(monkeypatch):
    from app.models.nodes import Entry
    from app.services import permissions

    observed = []

    async def batches(*args, **kwargs):
        observed.append(kwargs)
        yield [Entry(id="e1"), Entry(id="e2")]
        yield [Entry(id="e3")]

    monkeypatch.setattr(permissions, "iter_user_accessible_entry_batches", batches)
    entries = [
        entry
        async for entry in _iter_track_entries(
            SimpleNamespace(id="track"), user_id="user", workspace_id="workspace"
        )
    ]
    assert observed == [
        {"candidate_query": {}, "workspace_id": "workspace", "batch_size": 200}
    ]
    assert [entry.id for entry in entries] == ["e1", "e2", "e3"]


@pytest.mark.asyncio
async def test_core_open_query_reports_source_failure_instead_of_empty_result(
    monkeypatch,
):
    """A failed track discovery is a canonical query failure, never no rows."""
    from app.models.nodes import Track

    async def unavailable(*_args, **_kwargs):
        raise RuntimeError("database unavailable")

    monkeypatch.setattr(Track, "find", unavailable)

    with pytest.raises(QueryUnavailableError):
        await _run_core_open(
            user_id="u-query-failure",
            workspace_id="ws-query-failure",
            spec=QuerySpec(mode="core_open", resource="entry"),
        )


@pytest.mark.asyncio
async def test_governed_exact_filter_uses_persisted_candidates(
    authenticated_client, monkeypatch
):
    from app.models.nodes import Track

    created = await authenticated_client.post(
        "/api/tracks",
        json={"title": "Governed candidate fixture", "visibility": "private"},
    )
    assert created.status_code == 200, created.text
    track = await Track.get(created.json()["track"]["id"])
    expected = None
    for title in ("Candidate alpha", "Candidate beta", "Candidate gamma"):
        response = await authenticated_client.post(
            "/api/entries",
            json={
                "track_id": track.id,
                "title": title,
                "body": title,
                "custom_fields": {},
            },
        )
        assert response.status_code == 200, response.text
        if title == "Candidate beta":
            expected = response.json()["entry"]["id"]
    observed = []
    from app.services import permissions

    original = permissions.iter_user_accessible_entry_batches

    async def capture(*args, **kwargs):
        observed.append(kwargs)
        async for batch in original(*args, **kwargs):
            yield batch

    monkeypatch.setattr(permissions, "iter_user_accessible_entry_batches", capture)

    async def target_gate(_kind, _identity):
        return True

    result = await _run_core_open(
        user_id=track.owner_id,
        workspace_id=track.workspace_id,
        target_gate=target_gate,
        spec=QuerySpec(
            mode="core_open",
            resource="entry",
            filters=[FilterExpr(field="title", value="Candidate beta")],
            limit=1,
        ),
    )
    assert result.total_estimate == 1
    assert [row["id"] for row in result.rows] == [expected]
    assert observed and all(
        call["candidate_query"].get("context.title") == "Candidate beta"
        for call in observed
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["semantic", "hybrid"])
async def test_structured_query_rejects_silently_ignored_retrieval_mode(
    monkeypatch, mode
):
    from unittest.mock import AsyncMock

    from app.services.governed_query import engine

    monkeypatch.setattr(engine, "can_access_workspace", AsyncMock(return_value="owner"))
    monkeypatch.setattr(engine, "require_generation", lambda *_args: None)
    read = AsyncMock()
    monkeypatch.setattr(engine, "_run_core_open", read)
    with pytest.raises(BadRequestError) as caught:
        await engine.execute_query(
            user_id="u",
            workspace_id="ws",
            spec=QuerySpec(mode="core_open", resource="entry", retrieval_mode=mode),
        )
    assert caught.value.details["error_code"] == "unsupported_retrieval_mode"
    read.assert_not_awaited()


@pytest.mark.asyncio
async def test_streamed_governed_page_preserves_count_sort_gate_and_links(
    authenticated_client,
):
    from app.models.nodes import Track
    from app.services.governed_query.engine import _decode_cursor
    from app.services.notification_paths import entry_path

    response = await authenticated_client.post(
        "/api/tracks", json={"title": "Paged contract fixture", "visibility": "private"}
    )
    assert response.status_code == 200, response.text
    track = await Track.get(response.json()["track"]["id"])
    ids = {}
    for title in ("Alpha", "Gamma", "Beta"):
        response = await authenticated_client.post(
            "/api/entries",
            json={
                "track_id": track.id,
                "title": title,
                "body": title,
                "custom_fields": {},
            },
        )
        assert response.status_code == 200, response.text
        ids[title] = response.json()["entry"]["id"]

    async def gate(kind, node_id):
        return node_id != ids["Beta"]

    spec = QuerySpec(
        mode="core_open",
        resource="entry",
        sort="-title",
        limit=1,
        filters=[FilterExpr(field="track_id", value=track.id)],
    )
    first = await _run_core_open(
        user_id=track.owner_id,
        workspace_id=track.workspace_id,
        spec=spec,
        target_gate=gate,
    )
    assert first.total_estimate == 2
    assert [row["id"] for row in first.rows] == [ids["Gamma"]]
    assert first.object_refs[0].url_path == entry_path(ids["Gamma"], track.id)
    assert _decode_cursor(first.cursor) == 1
    second = await _run_core_open(
        user_id=track.owner_id,
        workspace_id=track.workspace_id,
        spec=spec.model_copy(update={"cursor": first.cursor}),
        target_gate=gate,
    )
    assert second.total_estimate == 2
    assert [row["id"] for row in second.rows] == [ids["Alpha"]]
    assert second.cursor is None


@pytest.mark.parametrize(
    "payload", [{}, {"o": -1}, {"o": True}, {"o": "1"}, {"o": 1, "v": 9}, []]
)
def test_governed_cursor_rejects_malformed_offsets(payload):
    import base64
    import json

    from app.services.governed_query.engine import _decode_cursor

    with pytest.raises(BadRequestError):
        _decode_cursor(base64.urlsafe_b64encode(json.dumps(payload).encode()).decode())


def test_governed_cursor_binds_new_pages_and_accepts_legacy_offsets():
    from app.services.governed_query.engine import _decode_cursor, _encode_cursor

    assert _decode_cursor(_encode_cursor(3), "new-scope") == 3
    assert _decode_cursor(_encode_cursor(3, "same-scope"), "same-scope") == 3
    with pytest.raises(BadRequestError):
        _decode_cursor(_encode_cursor(3, "another-scope"), "same-scope")
