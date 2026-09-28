"""Track merge preflight and atomic migration contract tests."""

from __future__ import annotations

from contextlib import asynccontextmanager
from types import SimpleNamespace

import pytest
from jvspatial.core import Edge

from app.models.edges import (
    CATALOGS,
    CONTAINS,
    HAS_OPERATIONAL_MODEL,
    IS_OF_TYPE,
    TAGGED_WITH,
)
from app.models.nodes import (
    App,
    Entry,
    EntryType,
    OperationalModel,
    Tag,
    Track,
    View,
    Views,
)
from app.services import bulk_move_entries, track_restructuring


async def _track_with_model(app: App, title: str, key: str, fields=None):
    track = await Track.create(
        title=title,
        owner_id="merge-user",
        workspace_id=app.workspace_id,
        visibility="inherit",
        schema_revision=1,
    )
    await app.connect(track, edge=CONTAINS)
    model = await OperationalModel.create(
        name=f"{title} model",
        scope="track",
        app_id=app.id,
        workspace_id=app.workspace_id,
        manifest={},
    )
    await track.connect(model, edge=HAS_OPERATIONAL_MODEL)
    track.attached_operational_model_id = model.id
    await track.save()
    entry_type = await EntryType.create(
        name=f"{title} record",
        form_schema={"_manifest_entry_type_key": key, "fields": fields or []},
        track_id=track.id,
    )
    await model.connect(entry_type, edge=CONTAINS)
    return track, model, entry_type


def _enable_in_memory_graph_transactions(monkeypatch):
    @asynccontextmanager
    async def transaction():
        yield None

    monkeypatch.setattr(
        track_restructuring, "graph_transaction_available", lambda: True
    )
    monkeypatch.setattr(bulk_move_entries, "graph_transaction_available", lambda: True)
    monkeypatch.setattr(bulk_move_entries, "postgres_graph_transaction", transaction)


@pytest.mark.asyncio
async def test_merge_tracks_moves_entries_tags_and_views_atomically(monkeypatch):
    _enable_in_memory_graph_transactions(monkeypatch)

    async def allowed(**_kwargs):
        return SimpleNamespace(allowed=True)

    async def writable(_track):
        return None

    async def runtime(_track):
        return None, {}, None

    async def materialize(**kwargs):
        return dict(kwargs["custom_fields"]), []

    async def no_op(*_args, **_kwargs):
        return None

    async def emit(**_kwargs):
        return None

    monkeypatch.setattr(track_restructuring, "policy_evaluate", allowed)
    monkeypatch.setattr(track_restructuring, "assert_track_schema_writable", writable)
    monkeypatch.setattr(bulk_move_entries, "policy_evaluate", allowed)
    monkeypatch.setattr(
        "app.services.migration_write_guard.assert_track_schema_writable", writable
    )
    monkeypatch.setattr(bulk_move_entries, "resolve_track_runtime_profile", runtime)
    monkeypatch.setattr(
        bulk_move_entries, "validate_and_materialize_entry_custom_fields", materialize
    )
    monkeypatch.setattr(bulk_move_entries, "resolve_entry_type_spec", lambda *_args: {})
    monkeypatch.setattr(bulk_move_entries, "validate_taxonomy_constraints", no_op)
    monkeypatch.setattr(bulk_move_entries, "validate_tags_apply_to_entry_type", no_op)
    monkeypatch.setattr(bulk_move_entries, "emit_change_event", emit)
    monkeypatch.setattr(track_restructuring, "emit_change_event", emit)
    monkeypatch.setattr(
        "app.services.operational_model_runtime.sync_attached_manifest", no_op
    )

    app = await App.create(
        name="Merge test app",
        workspace_id="merge-workspace",
        owner_user_id="merge-user",
    )
    source, source_model, source_type = await _track_with_model(
        app, "Source", "source_record", [{"key": "status", "type": "text"}]
    )
    target, target_model, target_type = await _track_with_model(
        app, "Target", "target_record", [{"key": "stage", "type": "text"}]
    )
    source_tag = await Tag.create(
        name="Urgent",
        name_fold="urgent",
        track_id=source.id,
        group_key="priority",
        applies_to_entry_types=[],
    )
    target_tag = await Tag.create(
        name="High",
        name_fold="high",
        track_id=target.id,
        group_key="priority",
        applies_to_entry_types=[],
    )
    await source_model.connect(source_tag, edge=CONTAINS)
    await target_model.connect(target_tag, edge=CONTAINS)
    source_entry = await Entry.create(
        title="Move this record",
        track_id=source.id,
        type_id=source_type.id,
        tags=[source_tag.id],
        custom_fields={"status": "Open"},
        record_revision=1,
    )
    await source.connect(source_entry, edge=CONTAINS)
    await source_entry.connect(source_type, edge=IS_OF_TYPE)
    await source_entry.connect(
        source_tag,
        edge=TAGGED_WITH,
        tagged_at="2026-09-01T10:00:00Z",
        tagged_by="author-user",
    )
    source_registry = await Views.create(
        track_id=source.id, operational_model_id=source_model.id
    )
    await source_model.connect(source_registry)
    source_view = await View.create(
        name="Source board",
        name_fold="source board",
        type="feed",
        config={"group_by": "status"},
        track_id=source.id,
        operational_model_id=source_model.id,
        entry_type_keys=["source_record"],
        default_entry_type_key="source_record",
        is_default=True,
    )
    await source_registry.connect(source_view, edge=CATALOGS)

    args = {
        "source_track_id": source.id,
        "target_track_id": target.id,
        "entry_type_mapping": {"source_record": "target_record"},
        "field_mapping": {"source_record": {"status": "stage"}},
        "tag_mapping": {source_tag.id: target_tag.id},
        "workspace_id": "merge-workspace",
    }
    preview = await track_restructuring.prepare_track_merge(
        user_id="merge-user", **args
    )
    assert "error" not in preview, preview
    assert preview["affected_count"] == 1

    result = await track_restructuring.merge_tracks(
        user_id="merge-user",
        payload={
            **{key: value for key, value in args.items() if key != "workspace_id"},
            "view_mapping": preview["view_mapping"],
            "preview_fingerprint": preview["preview_fingerprint"],
        },
        workspace_id="merge-workspace",
    )
    assert result.get("merged") is True, result
    assert result["moved_entries"] == 1
    assert await Track.get(source.id) is None
    assert await OperationalModel.get(source_model.id) is None
    assert await EntryType.get(source_type.id) is None
    assert await Tag.get(source_tag.id) is None

    moved = await Entry.get(source_entry.id)
    assert moved.track_id == target.id
    assert moved.type_id == target_type.id
    assert moved.custom_fields == {"stage": "Open"}
    assert moved.tags == [target_tag.id]
    ctx = await moved.get_context()
    target_edges = await ctx.find_edges_between(
        moved.id, target_tag.id, edge_class=TAGGED_WITH
    )
    assert len(target_edges) == 1
    assert target_edges[0].tagged_at == "2026-09-01T10:00:00Z"
    assert target_edges[0].tagged_by == "author-user"

    target_registry = await target_model.nodes(edge=[Edge], node=["Views"])
    assert target_registry
    transferred_views = await target_registry[0].nodes(edge=[CATALOGS], node=["View"])
    assert [view.name for view in transferred_views] == ["Source board"]
    assert transferred_views[0].track_id == target.id
    assert transferred_views[0].entry_type_keys == ["target_record"]
    assert transferred_views[0].default_entry_type_key == "target_record"
    assert transferred_views[0].config == {"group_by": "stage"}
    assert await View.get(source_view.id) is None


@pytest.mark.postgres
@pytest.mark.asyncio
async def test_merge_track_retirement_failure_rolls_back_moves_and_view_copy(
    monkeypatch,
):
    if not track_restructuring.graph_transaction_available():
        pytest.skip("atomic Track merge rollback requires the PostgreSQL graph store")

    async def allowed(**_kwargs):
        return SimpleNamespace(allowed=True)

    async def writable(_track):
        return None

    async def runtime(_track):
        return None, {}, None

    async def materialize(**kwargs):
        return dict(kwargs["custom_fields"]), []

    async def no_op(*_args, **_kwargs):
        return None

    monkeypatch.setattr(track_restructuring, "policy_evaluate", allowed)
    monkeypatch.setattr(track_restructuring, "assert_track_schema_writable", writable)
    monkeypatch.setattr(bulk_move_entries, "policy_evaluate", allowed)
    monkeypatch.setattr(
        "app.services.migration_write_guard.assert_track_schema_writable", writable
    )
    monkeypatch.setattr(bulk_move_entries, "resolve_track_runtime_profile", runtime)
    monkeypatch.setattr(
        bulk_move_entries, "validate_and_materialize_entry_custom_fields", materialize
    )
    monkeypatch.setattr(bulk_move_entries, "resolve_entry_type_spec", lambda *_args: {})
    monkeypatch.setattr(bulk_move_entries, "validate_taxonomy_constraints", no_op)
    monkeypatch.setattr(bulk_move_entries, "validate_tags_apply_to_entry_type", no_op)
    monkeypatch.setattr(bulk_move_entries, "emit_change_event", no_op)
    monkeypatch.setattr(track_restructuring, "emit_change_event", no_op)
    monkeypatch.setattr(
        "app.services.operational_model_runtime.sync_attached_manifest", no_op
    )

    app = await App.create(
        name="Merge rollback app",
        workspace_id="merge-rollback-workspace",
        owner_user_id="merge-user",
    )
    source, source_model, source_type = await _track_with_model(
        app, "Rollback source", "rollback_source"
    )
    target, _target_model, target_type = await _track_with_model(
        app, "Rollback target", "rollback_target"
    )
    entry = await Entry.create(
        title="Must remain at source",
        track_id=source.id,
        type_id=source_type.id,
        custom_fields={},
        record_revision=1,
    )
    await source.connect(entry, edge=CONTAINS)
    await entry.connect(source_type, edge=IS_OF_TYPE)
    source_registry = await Views.create(
        track_id=source.id, operational_model_id=source_model.id
    )
    await source_model.connect(source_registry)
    source_view = await View.create(
        name="Rollback board",
        type="feed",
        track_id=source.id,
        operational_model_id=source_model.id,
        entry_type_keys=["rollback_source"],
        default_entry_type_key="rollback_source",
    )
    await source_registry.connect(source_view, edge=CATALOGS)

    async def fail_source_delete(self, cascade=True):
        if self.id == source.id:
            raise RuntimeError("injected source-retirement failure")
        await original_track_delete(self, cascade=cascade)

    original_track_delete = Track.delete
    monkeypatch.setattr(Track, "delete", fail_source_delete)
    preview = await track_restructuring.prepare_track_merge(
        user_id="merge-user",
        source_track_id=source.id,
        target_track_id=target.id,
        entry_type_mapping={"rollback_source": "rollback_target"},
        field_mapping={"rollback_source": {}},
        tag_mapping={},
        workspace_id="merge-rollback-workspace",
    )
    assert "error" not in preview, preview
    result = await track_restructuring.merge_tracks(
        user_id="merge-user",
        payload={
            "source_track_id": source.id,
            "target_track_id": target.id,
            "entry_type_mapping": {"rollback_source": "rollback_target"},
            "field_mapping": {"rollback_source": {}},
            "tag_mapping": {},
            "view_mapping": {},
            "preview_fingerprint": preview["preview_fingerprint"],
        },
        workspace_id="merge-rollback-workspace",
    )
    assert result["error_code"] == "bulk_move_rolled_back", result
    assert await Track.get(source.id) is not None
    assert await OperationalModel.get(source_model.id) is not None
    assert await EntryType.get(source_type.id) is not None
    restored_entry = await Entry.get(entry.id)
    assert restored_entry.track_id == source.id
    assert restored_entry.type_id == source_type.id
    assert await Track.get(target.id) is not None
    assert await EntryType.get(target_type.id) is not None
    assert await View.get(source_view.id) is not None
