"""Tests for the declarative migration runner (Pillar 2).

Phase 5 Plan 05-02 — adds tests for the 3 new declarative ops
(``add_field_with_default``, ``rename_entry_type``, ``change_view_type``)
alongside the existing 6-op regressions. Existing tests below the
``Phase 5 additions`` marker MUST continue to pass UNCHANGED.
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.exceptions import BadRequestError
from app.services.content_profile_migrations import (
    _OP_HANDLERS,
    _add_field_with_default,
    _change_view_type,
    _coerce,
    _rename_entry_type,
    _rename_relation_edge_field_key,
    run_publish_migrations,
)


@pytest.mark.asyncio
async def test_relation_edge_metadata_follows_a_renamed_field():
    edge = MagicMock(field_key="assigned_vehicle")
    edge.save = AsyncMock()
    target = MagicMock(id="n.Entry.vehicle")
    context = MagicMock()
    context.find_edges_between = AsyncMock(return_value=[edge])
    entry = MagicMock(id="n.Entry.rental")
    entry.get_context = AsyncMock(return_value=context)
    entry.nodes = AsyncMock(return_value=[target])

    await _rename_relation_edge_field_key(
        entry, source_key="assigned_vehicle", target_key="vehicle"
    )

    assert edge.field_key == "vehicle"
    edge.save.assert_awaited_once()


def test_coerce_text_from_number():
    assert _coerce(42, "text") == "42"


def test_coerce_number_from_string():
    assert _coerce("3.14", "number") == 3.14
    assert _coerce("42", "number") == 42


def test_coerce_boolean_from_string():
    assert _coerce("true", "boolean") is True
    assert _coerce("false", "boolean") is False


def test_coerce_unsupported_target_raises():
    with pytest.raises(ValueError):
        _coerce(1, "totally_unknown")


def test_op_handlers_registered():
    expected_ops = {
        "rename_field",
        "default_fill",
        "delete_field",
        "prune_enum_option",
        "coerce_type",
        "move_field",
    }
    assert expected_ops.issubset(set(_OP_HANDLERS.keys()))


@pytest.mark.asyncio
async def test_run_publish_migrations_empty_returns_zero_executed():
    """No migrations declared: runner short-circuits with empty record."""

    class _StubCP:
        id = "cp-stub"
        scope = "track"

    result = await run_publish_migrations(
        published_cp=_StubCP(),
        candidate_manifest={"migrations": []},
        abort_on_failure=True,
    )
    assert result["executed"] is True
    assert result["ops"] == []
    assert result["mutated_entry_count"] == 0


@pytest.mark.asyncio
async def test_unknown_op_aborts_when_strict():
    class _StubCP:
        id = "cp-stub"
        scope = "track"

    candidate = {
        "migrations": [
            {
                "from_version": "v1",
                "to_version": "v1.1",
                "ops": [{"op": "totally_unknown_op"}],
            }
        ]
    }
    with pytest.raises(BadRequestError):
        await run_publish_migrations(
            published_cp=_StubCP(),
            candidate_manifest=candidate,
            abort_on_failure=True,
        )


@pytest.mark.asyncio
async def test_unknown_op_continues_when_not_strict():
    class _StubCP:
        id = "cp-stub"
        scope = "track"

    candidate = {
        "migrations": [
            {
                "from_version": "v1",
                "to_version": "v1.1",
                "ops": [{"op": "totally_unknown_op"}],
            }
        ]
    }
    result = await run_publish_migrations(
        published_cp=_StubCP(),
        candidate_manifest=candidate,
        abort_on_failure=False,
    )
    assert result["executed"] is True
    assert any(
        e.get("reason", "").startswith("Unknown migration op") for e in result["errors"]
    )


# ---------------------------------------------------------------------------
# Phase 5 Plan 05-02 additions — 3 new declarative ops.
# ---------------------------------------------------------------------------


def test_phase5_op_handlers_registered():
    """``_OP_HANDLERS`` carries all 9 ops after Phase 5 — 6 existing + 3 new."""
    expected_phase5_ops = {
        "add_field_with_default",
        "rename_entry_type",
        "change_view_type",
    }
    assert expected_phase5_ops.issubset(set(_OP_HANDLERS.keys()))
    assert len(_OP_HANDLERS) == 9


def _make_stub_entry(entry_id: str, type_id: str = "et-1", custom_fields=None):
    """Build a stub Entry-like object usable by the (track, op, log) handler API."""
    e = MagicMock()
    e.id = entry_id
    e.type_id = type_id
    e.custom_fields = dict(custom_fields or {})
    e.save = AsyncMock()
    return e


def _make_stub_track(entries, entry_types=None, views=None):
    """Build a stub Track-like object whose .nodes() returns the entries/views/types."""
    track = MagicMock()
    track.id = "track-1"

    async def _nodes(edge=None, direction=None, node=None):
        labels = node or []
        if "Entry" in labels:
            return entries
        if "View" in labels:
            return views or []
        if "EntryType" in labels:
            return entry_types or []
        return []

    track.nodes = _nodes
    return track


def _patch_entry_type_get(et_by_id):
    """Helper — patches EntryType.get to look up from the given dict."""

    async def _get(_id):
        return et_by_id.get(_id)

    return patch(
        "app.services.content_profile_migrations.EntryType.get",
        side_effect=_get,
    )


@pytest.mark.asyncio
async def test_add_field_with_default_round_trip():
    """add_field_with_default populates the default into every Entry that lacks it."""
    et_id = "et-task"
    et_node = MagicMock()
    et_node.id = et_id
    et_node.name = "Task"
    entries = [
        _make_stub_entry("e1", type_id=et_id, custom_fields={"a": 1}),
        _make_stub_entry("e2", type_id=et_id, custom_fields={"a": 2}),
    ]
    track = _make_stub_track(entries)
    log = {"mutated_entries": [], "errors": [], "pending_manual_review": []}
    op = {
        "op": "add_field_with_default",
        "entry_type": "task",
        "field": "cycle",
        "type": "text",
        "default": "sprint",
    }
    with _patch_entry_type_get({et_id: et_node}):
        await _add_field_with_default(track=track, op=op, log=log)
    assert entries[0].custom_fields == {"a": 1, "cycle": "sprint"}
    assert entries[1].custom_fields == {"a": 2, "cycle": "sprint"}
    assert set(log["mutated_entries"]) == {"e1", "e2"}


@pytest.mark.asyncio
async def test_add_field_with_default_idempotent_preserves_existing():
    """Re-running on entries that already have the field is a no-op (existing value wins)."""
    et_id = "et-task"
    et_node = MagicMock()
    et_node.id = et_id
    et_node.name = "Task"
    entries = [
        _make_stub_entry("e1", type_id=et_id, custom_fields={"cycle": "Q3"}),
        _make_stub_entry("e2", type_id=et_id, custom_fields={"cycle": "Q4"}),
    ]
    track = _make_stub_track(entries)
    log = {"mutated_entries": [], "errors": [], "pending_manual_review": []}
    op = {
        "op": "add_field_with_default",
        "entry_type": "task",
        "field": "cycle",
        "default": "sprint",
    }
    with _patch_entry_type_get({et_id: et_node}):
        await _add_field_with_default(track=track, op=op, log=log)
    # Existing values preserved; no save() calls; no log mutations.
    assert entries[0].custom_fields == {"cycle": "Q3"}
    assert entries[1].custom_fields == {"cycle": "Q4"}
    entries[0].save.assert_not_awaited()
    entries[1].save.assert_not_awaited()
    assert log["mutated_entries"] == []


@pytest.mark.asyncio
async def test_add_field_with_default_validates_inputs():
    """Missing entry_type or field raises BadRequestError (parity with existing ops)."""
    log = {"mutated_entries": [], "errors": [], "pending_manual_review": []}
    track = _make_stub_track([])
    with pytest.raises(BadRequestError):
        await _add_field_with_default(
            track=track, op={"op": "add_field_with_default"}, log=log
        )


@pytest.mark.asyncio
async def test_rename_entry_type_rewrites_type_id():
    """rename_entry_type rewrites Entry.type_id from old → new EntryType.id."""
    old_id = "et-task"
    new_id = "et-ticket"
    old_et = MagicMock()
    old_et.id = old_id
    old_et.name = "Task"
    new_et = MagicMock()
    new_et.id = new_id
    new_et.name = "Ticket"
    entries = [
        _make_stub_entry("e1", type_id=old_id),
        _make_stub_entry("e2", type_id=old_id),
    ]
    # Track surfaces the new EntryType via .nodes(EntryType) so the handler
    # can resolve the rename target even when no entries point at it yet.
    track = _make_stub_track(entries, entry_types=[new_et])
    log = {"mutated_entries": [], "errors": [], "pending_manual_review": []}
    op = {"op": "rename_entry_type", "from": "task", "to": "ticket"}
    with _patch_entry_type_get({old_id: old_et, new_id: new_et}):
        await _rename_entry_type(track=track, op=op, log=log)
    assert entries[0].type_id == new_id
    assert entries[1].type_id == new_id
    assert set(log["mutated_entries"]) == {"e1", "e2"}


@pytest.mark.asyncio
async def test_rename_entry_type_idempotent_when_already_target():
    """Re-running when entries already point at the new EntryType is a no-op."""
    new_id = "et-ticket"
    new_et = MagicMock()
    new_et.id = new_id
    new_et.name = "Ticket"
    entries = [
        _make_stub_entry("e1", type_id=new_id),
    ]
    track = _make_stub_track(entries)
    log = {"mutated_entries": [], "errors": [], "pending_manual_review": []}
    op = {"op": "rename_entry_type", "from": "task", "to": "ticket"}
    with _patch_entry_type_get({new_id: new_et}):
        await _rename_entry_type(track=track, op=op, log=log)
    entries[0].save.assert_not_awaited()
    assert log["mutated_entries"] == []


@pytest.mark.asyncio
async def test_rename_entry_type_does_not_touch_other_types():
    """Entries belonging to a different EntryType in the same track are untouched."""
    old_id = "et-task"
    new_id = "et-ticket"
    other_id = "et-note"
    old_et = MagicMock()
    old_et.id = old_id
    old_et.name = "Task"
    new_et = MagicMock()
    new_et.id = new_id
    new_et.name = "Ticket"
    other_et = MagicMock()
    other_et.id = other_id
    other_et.name = "Note"
    entries = [
        _make_stub_entry("e-task", type_id=old_id),
        _make_stub_entry("e-note", type_id=other_id),
    ]
    track = _make_stub_track(entries, entry_types=[new_et])
    log = {"mutated_entries": [], "errors": [], "pending_manual_review": []}
    op = {"op": "rename_entry_type", "from": "task", "to": "ticket"}
    with _patch_entry_type_get({old_id: old_et, new_id: new_et, other_id: other_et}):
        await _rename_entry_type(track=track, op=op, log=log)
    assert entries[0].type_id == new_id
    assert entries[1].type_id == other_id  # untouched
    assert log["mutated_entries"] == ["e-task"]


@pytest.mark.asyncio
async def test_rename_entry_type_validates_inputs():
    log = {"mutated_entries": [], "errors": [], "pending_manual_review": []}
    track = _make_stub_track([])
    with pytest.raises(BadRequestError):
        await _rename_entry_type(
            track=track, op={"op": "rename_entry_type", "from": ""}, log=log
        )


@pytest.mark.asyncio
async def test_change_view_type_table_to_kanban_with_defaults():
    """change_view_type updates type + merges declared defaults into config."""
    view = MagicMock()
    view.id = "v1"
    view.key = "my_view"
    view.type = "table"
    view.config = {"columns": ["a", "b"]}
    view.save = AsyncMock()
    track = _make_stub_track(entries=[], views=[view])
    log = {"mutated_entries": [], "errors": [], "pending_manual_review": []}
    op = {
        "op": "change_view_type",
        "view_key": "my_view",
        "from": "table",
        "to": "kanban",
        "group_by": "priority",
    }
    await _change_view_type(track=track, op=op, log=log)
    assert view.type == "kanban"
    # Existing config preserved; defaults merged in.
    assert view.config["columns"] == ["a", "b"]
    assert view.config["group_by"] == "priority"
    assert log["mutated_entries"] == ["v1"]


@pytest.mark.asyncio
async def test_change_view_type_idempotent():
    view = MagicMock()
    view.id = "v1"
    view.key = "my_view"
    view.type = "kanban"
    view.config = {"group_by": "priority"}
    view.save = AsyncMock()
    track = _make_stub_track(entries=[], views=[view])
    log = {"mutated_entries": [], "errors": [], "pending_manual_review": []}
    op = {
        "op": "change_view_type",
        "view_key": "my_view",
        "to": "kanban",
        "group_by": "status",
    }
    await _change_view_type(track=track, op=op, log=log)
    view.save.assert_not_awaited()
    assert log["mutated_entries"] == []


@pytest.mark.asyncio
async def test_change_view_type_validates_inputs():
    log = {"mutated_entries": [], "errors": [], "pending_manual_review": []}
    track = _make_stub_track([])
    with pytest.raises(BadRequestError):
        await _change_view_type(track=track, op={"op": "change_view_type"}, log=log)


@pytest.mark.asyncio
async def test_run_publish_migrations_dispatches_new_ops():
    """run_publish_migrations dispatches to the 3 new handlers via _OP_HANDLERS."""

    class _StubCP:
        id = "cp-stub"
        scope = "track"

    called = []

    async def _fake_handler(*, track, op, log):
        called.append(op["op"])

    candidate = {
        "migrations": [
            {
                "from_version": "v1",
                "to_version": "v1.1",
                "ops": [
                    {"op": "add_field_with_default", "entry_type": "t", "field": "f"},
                    {"op": "rename_entry_type", "from": "a", "to": "b"},
                    {"op": "change_view_type", "view_key": "v", "to": "kanban"},
                ],
            }
        ]
    }
    with (
        patch.dict(
            "app.services.content_profile_migrations._OP_HANDLERS",
            {
                "add_field_with_default": _fake_handler,
                "rename_entry_type": _fake_handler,
                "change_view_type": _fake_handler,
            },
            clear=False,
        ),
        patch(
            "app.services.content_profile_migrations._affected_tracks",
            new=AsyncMock(return_value=[MagicMock()]),
        ),
    ):
        result = await run_publish_migrations(
            published_cp=_StubCP(),
            candidate_manifest=candidate,
            abort_on_failure=True,
        )
    assert result["executed"] is True
    assert called == ["add_field_with_default", "rename_entry_type", "change_view_type"]
