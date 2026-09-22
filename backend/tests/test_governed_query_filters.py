"""Exact QuerySpec filter semantics stay explicit and complete."""

from types import SimpleNamespace

import pytest

from app.api.errors import BadRequestError, QueryUnavailableError
from app.schemas.governed_query import FilterExpr, QuerySpec
from app.services.governed_query.engine import (
    _filter_matches,
    _matches_core_filters,
    _run_core_open,
    _serialize_core_node,
    _sort_core_nodes,
    _track_entries,
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
async def test_core_entry_query_walks_all_track_pages_without_hidden_cap():
    from app.models.nodes import Entry

    class _Track:
        def __init__(self) -> None:
            self.cursors: list[str | None] = []

        async def nodes_page(self, *, cursor, limit, **_kwargs):
            self.cursors.append(cursor)
            if cursor is None:
                return [Entry(id="e1"), Entry(id="e2")], "next"
            return [Entry(id="e3")], None

    track = _Track()
    entries = await _track_entries(track)

    assert track.cursors == [None, "next"]
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
