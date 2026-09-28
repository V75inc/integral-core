"""Revision-bound, all-or-nothing bulk Entry move contract."""

from __future__ import annotations

from contextlib import asynccontextmanager
from types import SimpleNamespace

import pytest

from app.models.edges import CONTAINS, IS_OF_TYPE, REFERENCES
from app.models.nodes import App, Entry, EntryType, Track
from app.services import bulk_move_entries as moves


async def _graph_fixture():
    app = await App.create(
        name="Move fixture", workspace_id="ws-move", owner_user_id="u-move"
    )
    source = await Track.create(
        title="Source", owner_id="u-move", workspace_id="ws-move", schema_revision=1
    )
    target = await Track.create(
        title="Target", owner_id="u-move", workspace_id="ws-move", schema_revision=1
    )
    await app.connect(source, edge=CONTAINS)
    await app.connect(target, edge=CONTAINS)
    source_type = await EntryType.create(
        name="Source record",
        icon="file",
        form_schema={"_manifest_entry_type_key": "source_record", "fields": []},
        track_id=source.id,
        is_template=False,
    )
    target_type = await EntryType.create(
        name="Target record",
        icon="file",
        form_schema={"_manifest_entry_type_key": "target_record", "fields": []},
        track_id=target.id,
        is_template=False,
    )
    await source.connect(source_type, edge=CONTAINS)
    await target.connect(target_type, edge=CONTAINS)
    linked = await Entry.create(
        title="Linked",
        author_id="u-move",
        track_id=source.id,
        type_id=source_type.id,
        custom_fields={},
    )
    moved = await Entry.create(
        title="Move me",
        author_id="u-move",
        track_id=source.id,
        type_id=source_type.id,
        custom_fields={"related": linked.id},
        record_revision=1,
    )
    inbound = await Entry.create(
        title="Inbound",
        author_id="u-move",
        track_id=source.id,
        type_id=source_type.id,
        custom_fields={},
    )
    for entry, entry_type in (
        (linked, source_type),
        (moved, source_type),
        (inbound, source_type),
    ):
        await source.connect(entry, edge=CONTAINS)
        await entry.connect(entry_type, edge=IS_OF_TYPE)
    await moved.connect(
        linked,
        edge=REFERENCES,
        field_key="related",
        relation_type="operational_model",
        cross_track=False,
    )
    await inbound.connect(
        moved,
        edge=REFERENCES,
        field_key="related",
        relation_type="operational_model",
        cross_track=False,
    )
    return app, source, target, source_type, target_type, linked, moved, inbound


def _stub_validation(monkeypatch):
    async def allowed(**_kwargs):
        return SimpleNamespace(allowed=True)

    async def writable(_track):
        return None

    async def runtime(_track):
        return None, {}, None

    async def materialize(**kwargs):
        return dict(kwargs["custom_fields"]), []

    async def no_op(**_kwargs):
        return None

    monkeypatch.setattr(moves, "policy_evaluate", allowed)
    monkeypatch.setattr(
        "app.services.migration_write_guard.assert_track_schema_writable", writable
    )
    monkeypatch.setattr(moves, "resolve_track_runtime_profile", runtime)
    monkeypatch.setattr(
        moves, "validate_and_materialize_entry_custom_fields", materialize
    )
    monkeypatch.setattr(moves, "resolve_entry_type_spec", lambda *_args: {})
    monkeypatch.setattr(moves, "validate_taxonomy_constraints", no_op)
    monkeypatch.setattr(moves, "validate_tags_apply_to_entry_type", no_op)


def _provide_transaction_for_unit_store(monkeypatch):
    if moves.graph_transaction_available():
        return True

    @asynccontextmanager
    async def transaction():
        yield None

    monkeypatch.setattr(moves, "graph_transaction_available", lambda: True)
    monkeypatch.setattr(moves, "postgres_graph_transaction", transaction)
    return False


@pytest.mark.asyncio
async def test_bulk_move_preserves_and_rekeys_outbound_and_inbound_relations(
    monkeypatch,
):
    _stub_validation(monkeypatch)
    _provide_transaction_for_unit_store(monkeypatch)
    monkeypatch.setattr(moves, "emit_change_event", lambda **_kwargs: _async_none())
    _, source, target, _, target_type, linked, moved, inbound = await _graph_fixture()
    args = {
        "entry_ids": [moved.id],
        "target_track_id": target.id,
        "entry_type_mapping": {"source_record": "target_record"},
        "field_mapping": {"source_record": {"related": "related_to"}},
    }
    preview = await moves.prepare_bulk_move(
        user_id="u-move", workspace_id="ws-move", **args
    )
    assert preview["entries"][0]["status"] == "ready"
    assert "custom_fields" not in preview["entries"][0]

    result = await moves.move_entries(
        user_id="u-move",
        workspace_id="ws-move",
        payload={
            **args,
            "preview_fingerprint": preview["preview_fingerprint"],
            "record_revisions": preview["record_revisions"],
            "target_schema_revision": preview["target_schema_revision"],
        },
    )
    assert result.get("moved_count") == 1, result
    updated = await Entry.get(moved.id)
    assert updated.track_id == target.id
    assert updated.type_id == target_type.id
    assert updated.custom_fields == {"related_to": linked.id}
    assert [
        item.id
        for item in await source.nodes(edge=[CONTAINS], direction="out", node=["Entry"])
        if item.id == moved.id
    ] == []
    assert [
        item.id
        for item in await target.nodes(edge=[CONTAINS], direction="out", node=["Entry"])
        if item.id == moved.id
    ] == [moved.id]

    ctx = await updated.get_context()
    outgoing = await ctx.find_edges_between(
        updated.id, linked.id, edge_class=REFERENCES
    )
    inbound_edges = await ctx.find_edges_between(
        inbound.id, updated.id, edge_class=REFERENCES
    )
    assert len(outgoing) == len(inbound_edges) == 1
    assert outgoing[0].field_key == "related_to"
    assert outgoing[0].cross_track is True
    assert inbound_edges[0].field_key == "related"
    assert inbound_edges[0].cross_track is True


async def _async_none():
    return None


@pytest.mark.asyncio
async def test_bulk_move_rolls_back_every_entry_if_a_later_write_fails(monkeypatch):
    _stub_validation(monkeypatch)
    if not moves.graph_transaction_available():
        pytest.skip(
            "atomic rollback requires the PostgreSQL graph transaction test store"
        )
    _, source, target, _, _, _, first, second = await _graph_fixture()
    args = {
        "entry_ids": [first.id, second.id],
        "target_track_id": target.id,
        "entry_type_mapping": {"source_record": "target_record"},
        "field_mapping": {"source_record": {"related": "related"}},
    }
    preview = await moves.prepare_bulk_move(
        user_id="u-move", workspace_id="ws-move", **args
    )
    original_save = Entry.save
    calls = 0

    async def fail_second_save(self):
        nonlocal calls
        calls += 1
        if calls == 2:
            raise RuntimeError("injected second-entry failure")
        return await original_save(self)

    monkeypatch.setattr(Entry, "save", fail_second_save)
    result = await moves.move_entries(
        user_id="u-move",
        workspace_id="ws-move",
        payload={
            **args,
            "preview_fingerprint": preview["preview_fingerprint"],
            "record_revisions": preview["record_revisions"],
            "target_schema_revision": preview["target_schema_revision"],
        },
    )
    assert result["error_code"] == "bulk_move_rolled_back"
    assert (await Entry.get(first.id)).track_id == source.id
    assert (await Entry.get(second.id)).track_id == source.id
    target_ids = {
        entry.id
        for entry in await target.nodes(
            edge=[CONTAINS], direction="out", node=["Entry"]
        )
    }
    assert first.id not in target_ids
    assert second.id not in target_ids
