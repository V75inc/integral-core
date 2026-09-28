"""Track merge preflight and atomic migration contract tests."""

from __future__ import annotations

import os
from contextlib import asynccontextmanager
from types import SimpleNamespace

import pytest
from jvspatial.core import Edge

from app.models.edges import (
    CATALOGS,
    COLLABORATES_ON,
    CONTAINS,
    HAS_ATTACHMENT,
    HAS_COMMENT,
    HAS_OPERATIONAL_MODEL,
    IS_OF_TYPE,
    REFERENCES,
    TAGGED_WITH,
)
from app.models.nodes import (
    App,
    Attachment,
    Comment,
    Entry,
    EntryType,
    OperationalModel,
    Tag,
    Track,
    Tracks,
    User,
    View,
    Views,
    Workspace,
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


async def _standalone_track_with_model(
    workspace_id: str, registry: Tracks, title: str, key: str
):
    track = await Track.create(
        title=title,
        owner_id="merge-user",
        workspace_id=workspace_id,
        visibility="inherit",
        schema_revision=1,
    )
    await registry.connect(track, edge=CATALOGS)
    model = await OperationalModel.create(
        name=f"{title} model",
        scope="track",
        app_id="",
        workspace_id=workspace_id,
        manifest={},
    )
    await track.connect(model, edge=HAS_OPERATIONAL_MODEL)
    track.attached_operational_model_id = model.id
    await track.save()
    entry_type = await EntryType.create(
        name=f"{title} record",
        form_schema={"_manifest_entry_type_key": key, "fields": []},
        track_id=track.id,
    )
    await model.connect(entry_type, edge=CONTAINS)
    return track


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
    source_note_type = await EntryType.create(
        name="Source note",
        form_schema={
            "_manifest_entry_type_key": "source_note",
            "fields": [{"key": "summary", "type": "text"}],
        },
        track_id=source.id,
    )
    await source_model.connect(source_note_type, edge=CONTAINS)
    target, target_model, target_type = await _track_with_model(
        app, "Target", "target_record", [{"key": "stage", "type": "text"}]
    )
    target_note_type = await EntryType.create(
        name="Target note",
        form_schema={
            "_manifest_entry_type_key": "target_note",
            "fields": [{"key": "details", "type": "text"}],
        },
        track_id=target.id,
    )
    await target_model.connect(target_note_type, edge=CONTAINS)
    linked_entry = await Entry.create(
        title="Existing target record",
        track_id=target.id,
        type_id=target_type.id,
        custom_fields={"stage": "Open"},
        record_revision=1,
    )
    await target.connect(linked_entry, edge=CONTAINS)
    await linked_entry.connect(target_type, edge=IS_OF_TYPE)
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
    await source_entry.connect(
        linked_entry,
        edge=REFERENCES,
        field_key="related",
        relation_type="operational_model",
        cross_track=True,
    )
    await linked_entry.connect(
        source_entry,
        edge=REFERENCES,
        field_key="backlink",
        relation_type="operational_model",
        cross_track=True,
    )
    comment = await Comment.create(author_id="author-user", text="Keep this note")
    attachment = await Attachment.create(
        filename="evidence.txt",
        storage_key="test/evidence.txt",
        uploaded_by="author-user",
    )
    await source_entry.connect(comment, edge=HAS_COMMENT)
    await source_entry.connect(attachment, edge=HAS_ATTACHMENT)
    mixed_type_entry = await Entry.create(
        title="Move mixed EntryType",
        track_id=source.id,
        type_id=source_note_type.id,
        custom_fields={"summary": "Preserve this note"},
        record_revision=1,
    )
    await source.connect(mixed_type_entry, edge=CONTAINS)
    await mixed_type_entry.connect(source_note_type, edge=IS_OF_TYPE)
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
        "entry_type_mapping": {
            "source_record": "target_record",
            "source_note": "target_note",
        },
        "field_mapping": {
            "source_record": {"status": "stage"},
            "source_note": {"summary": "details"},
        },
        "tag_mapping": {source_tag.id: target_tag.id},
        "workspace_id": "merge-workspace",
    }
    preview = await track_restructuring.prepare_track_merge(
        user_id="merge-user", **args
    )
    assert "error" not in preview, preview
    assert preview["affected_count"] == 2

    async def deny_source_delete(**kwargs):
        if kwargs.get("action") == "track.delete":
            return SimpleNamespace(allowed=False)
        return SimpleNamespace(allowed=True)

    monkeypatch.setattr(track_restructuring, "policy_evaluate", deny_source_delete)
    denied = await track_restructuring.merge_tracks(
        user_id="merge-user",
        payload={
            **{key: value for key, value in args.items() if key != "workspace_id"},
            "view_mapping": preview["view_mapping"],
            "preview_fingerprint": preview["preview_fingerprint"],
        },
        workspace_id="merge-workspace",
    )
    assert denied["error_code"] == "forbidden", denied
    assert (await Entry.get(source_entry.id)).track_id == source.id
    monkeypatch.setattr(track_restructuring, "policy_evaluate", allowed)

    changed_entry = await Entry.get(source_entry.id)
    changed_entry.record_revision += 1
    await changed_entry.save()
    stale = await track_restructuring.merge_tracks(
        user_id="merge-user",
        payload={
            **{key: value for key, value in args.items() if key != "workspace_id"},
            "view_mapping": preview["view_mapping"],
            "preview_fingerprint": preview["preview_fingerprint"],
        },
        workspace_id="merge-workspace",
    )
    assert stale["error_code"] == "stale_preview", stale
    assert (await Entry.get(source_entry.id)).track_id == source.id
    preview = await track_restructuring.prepare_track_merge(
        user_id="merge-user", **args
    )
    assert "error" not in preview, preview

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
    assert result["moved_entries"] == 2
    assert await Track.get(source.id) is None
    assert await OperationalModel.get(source_model.id) is None
    assert await EntryType.get(source_type.id) is None
    assert await EntryType.get(source_note_type.id) is None
    assert await Tag.get(source_tag.id) is None

    moved = await Entry.get(source_entry.id)
    assert moved.track_id == target.id
    assert moved.type_id == target_type.id
    assert moved.custom_fields == {"stage": "Open"}
    assert moved.tags == [target_tag.id]
    moved_mixed_type = await Entry.get(mixed_type_entry.id)
    assert moved_mixed_type.track_id == target.id
    assert moved_mixed_type.type_id == target_note_type.id
    assert moved_mixed_type.custom_fields == {"details": "Preserve this note"}
    ctx = await moved.get_context()
    target_edges = await ctx.find_edges_between(
        moved.id, target_tag.id, edge_class=TAGGED_WITH
    )
    assert len(target_edges) == 1
    assert target_edges[0].tagged_at == "2026-09-01T10:00:00Z"
    assert target_edges[0].tagged_by == "author-user"
    related_edges = await ctx.find_edges_between(
        moved.id, linked_entry.id, edge_class=REFERENCES
    )
    assert len(related_edges) == 1
    assert related_edges[0].field_key == "related"
    assert related_edges[0].cross_track is False
    reverse_edges = await ctx.find_edges_between(
        linked_entry.id, moved.id, edge_class=REFERENCES
    )
    assert len(reverse_edges) == 1
    assert reverse_edges[0].field_key == "backlink"
    assert reverse_edges[0].cross_track is False
    assert await ctx.find_edges_between(moved.id, comment.id, edge_class=HAS_COMMENT)
    assert await ctx.find_edges_between(
        moved.id, attachment.id, edge_class=HAS_ATTACHMENT
    )

    target_registry = await target_model.nodes(edge=[Edge], node=["Views"])
    assert target_registry
    transferred_views = await target_registry[0].nodes(edge=[CATALOGS], node=["View"])
    assert [view.name for view in transferred_views] == ["Source board"]
    assert transferred_views[0].track_id == target.id
    assert transferred_views[0].entry_type_keys == ["target_record"]
    assert transferred_views[0].default_entry_type_key == "target_record"
    assert transferred_views[0].config == {"group_by": "stage"}
    assert await View.get(source_view.id) is None


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("target_workspace", "target_app_name", "expected_error"),
    [
        ("other-workspace", "Source app", "workspace_mismatch"),
        ("merge-workspace", "Other app", "container_mismatch"),
    ],
)
async def test_merge_refuses_cross_workspace_or_cross_app_before_writes(
    monkeypatch, target_workspace, target_app_name, expected_error
):
    async def allowed(**_kwargs):
        return SimpleNamespace(allowed=True)

    async def writable(_track):
        return None

    monkeypatch.setattr(track_restructuring, "policy_evaluate", allowed)
    monkeypatch.setattr(track_restructuring, "assert_track_schema_writable", writable)
    source_app = await App.create(
        name="Source app", workspace_id="merge-workspace", owner_user_id="merge-user"
    )
    target_app = await App.create(
        name=target_app_name,
        workspace_id=target_workspace,
        owner_user_id="merge-user",
    )
    source, _source_model, _source_type = await _track_with_model(
        source_app, "Source", "source_record"
    )
    target, _target_model, _target_type = await _track_with_model(
        target_app, "Target", "target_record"
    )

    result = await track_restructuring.prepare_track_merge(
        user_id="merge-user",
        source_track_id=source.id,
        target_track_id=target.id,
        entry_type_mapping={"source_record": "target_record"},
        field_mapping={"source_record": {}},
        tag_mapping={},
        workspace_id="merge-workspace",
    )

    assert result["error"] == expected_error
    assert await Track.get(source.id) is not None
    assert await Track.get(target.id) is not None


@pytest.mark.asyncio
async def test_merge_refuses_more_than_500_entries_before_bulk_preflight(monkeypatch):
    async def allowed(**_kwargs):
        return SimpleNamespace(allowed=True)

    async def writable(_track):
        return None

    monkeypatch.setattr(track_restructuring, "policy_evaluate", allowed)
    monkeypatch.setattr(track_restructuring, "assert_track_schema_writable", writable)
    app = await App.create(
        name="Oversized merge app",
        workspace_id="merge-workspace",
        owner_user_id="merge-user",
    )
    source, _source_model, _source_type = await _track_with_model(
        app, "Oversized source", "source_record"
    )
    target, _target_model, _target_type = await _track_with_model(
        app, "Oversized target", "target_record"
    )
    original_nodes_page = Track.nodes_page

    async def oversized_page(track, *args, **kwargs):
        if track.id == source.id:
            return [SimpleNamespace(id=f"entry-{index}") for index in range(501)], None
        return await original_nodes_page(track, *args, **kwargs)

    monkeypatch.setattr(Track, "nodes_page", oversized_page)
    result = await track_restructuring.prepare_track_merge(
        user_id="merge-user",
        source_track_id=source.id,
        target_track_id=target.id,
        entry_type_mapping={"source_record": "target_record"},
        field_mapping={"source_record": {}},
        tag_mapping={},
        workspace_id="merge-workspace",
    )

    assert result["error"] == "too_many_entries"
    assert "500" in result["detail"]
    assert await Track.get(source.id) is not None
    assert await Track.get(target.id) is not None


@pytest.mark.asyncio
async def test_empty_standalone_track_merge_previews_without_widening_scope(
    monkeypatch,
):
    async def allowed(**_kwargs):
        return SimpleNamespace(allowed=True)

    async def writable(_track):
        return None

    monkeypatch.setattr(track_restructuring, "policy_evaluate", allowed)
    monkeypatch.setattr(track_restructuring, "assert_track_schema_writable", writable)
    monkeypatch.setattr(bulk_move_entries, "policy_evaluate", allowed)
    monkeypatch.setattr(
        "app.services.migration_write_guard.assert_track_schema_writable", writable
    )
    workspace = await Workspace.create(
        name="Standalone merge workspace", kind="organization"
    )
    registry = await Tracks.create(workspace_id=workspace.id)
    await workspace.connect(registry, edge=CONTAINS)
    source = await _standalone_track_with_model(
        workspace.id, registry, "Empty standalone source", "source_record"
    )
    target = await _standalone_track_with_model(
        workspace.id, registry, "Empty standalone target", "target_record"
    )

    result = await track_restructuring.prepare_track_merge(
        user_id="merge-user",
        source_track_id=source.id,
        target_track_id=target.id,
        entry_type_mapping={"source_record": "target_record"},
        field_mapping={"source_record": {}},
        tag_mapping={},
        workspace_id=workspace.id,
    )

    assert "error" not in result, result
    assert result["affected_count"] == 0
    assert result["snapshot"]["app_id"] == ""
    assert result["snapshot"]["entry_ids"] == []
    assert await Track.get(source.id) is not None
    assert await Track.get(target.id) is not None


@pytest.mark.asyncio
async def test_merge_refuses_source_access_sidecars_before_writes(monkeypatch):
    async def allowed(**_kwargs):
        return SimpleNamespace(allowed=True)

    async def writable(_track):
        return None

    monkeypatch.setattr(track_restructuring, "policy_evaluate", allowed)
    monkeypatch.setattr(track_restructuring, "assert_track_schema_writable", writable)
    app = await App.create(
        name="Sidecar merge app",
        workspace_id="merge-workspace",
        owner_user_id="merge-user",
    )
    source, _source_model, _source_type = await _track_with_model(
        app, "Sidecar source", "source_record"
    )
    target, _target_model, _target_type = await _track_with_model(
        app, "Sidecar target", "target_record"
    )
    collaborator = await User.create(id="sidecar-user", email="sidecar@example.com")
    await collaborator.connect(source, edge=COLLABORATES_ON, role="editor")

    result = await track_restructuring.prepare_track_merge(
        user_id="merge-user",
        source_track_id=source.id,
        target_track_id=target.id,
        entry_type_mapping={"source_record": "target_record"},
        field_mapping={"source_record": {}},
        tag_mapping={},
        workspace_id="merge-workspace",
    )

    assert result["error"] == "unsupported_sidecar"
    assert "collaborators" in result["detail"]
    assert await Track.get(source.id) is not None
    assert await Track.get(target.id) is not None


@pytest.mark.asyncio
async def test_merge_requires_explicit_view_name_mapping_on_collision(monkeypatch):
    async def allowed(**_kwargs):
        return SimpleNamespace(allowed=True)

    async def writable(_track):
        return None

    monkeypatch.setattr(track_restructuring, "policy_evaluate", allowed)
    monkeypatch.setattr(track_restructuring, "assert_track_schema_writable", writable)
    monkeypatch.setattr(bulk_move_entries, "policy_evaluate", allowed)
    monkeypatch.setattr(
        "app.services.migration_write_guard.assert_track_schema_writable", writable
    )
    app = await App.create(
        name="View collision app",
        workspace_id="merge-workspace",
        owner_user_id="merge-user",
    )
    source, source_model, _source_type = await _track_with_model(
        app, "Collision source", "source_record"
    )
    target, target_model, _target_type = await _track_with_model(
        app, "Collision target", "target_record"
    )
    source_view_id = ""
    for track, model, type_key in (
        (source, source_model, "source_record"),
        (target, target_model, "target_record"),
    ):
        registry = await Views.create(track_id=track.id, operational_model_id=model.id)
        await model.connect(registry)
        view = await View.create(
            name="Board",
            name_fold="board",
            type="feed",
            track_id=track.id,
            operational_model_id=model.id,
            entry_type_keys=[type_key],
            default_entry_type_key=type_key,
        )
        await registry.connect(view, edge=CATALOGS)
        if track.id == source.id:
            source_view_id = view.id

    result = await track_restructuring.prepare_track_merge(
        user_id="merge-user",
        source_track_id=source.id,
        target_track_id=target.id,
        entry_type_mapping={"source_record": "target_record"},
        field_mapping={"source_record": {}},
        tag_mapping={},
        workspace_id="merge-workspace",
    )

    assert result["error"] == "view_mapping_required"
    assert await Track.get(source.id) is not None
    assert await Track.get(target.id) is not None
    resolved = await track_restructuring.prepare_track_merge(
        user_id="merge-user",
        source_track_id=source.id,
        target_track_id=target.id,
        entry_type_mapping={"source_record": "target_record"},
        field_mapping={"source_record": {}},
        tag_mapping={},
        view_mapping={source_view_id: "Imported board"},
        workspace_id="merge-workspace",
    )
    assert "error" not in resolved, resolved
    assert resolved["view_mapping"] == {source_view_id: "Imported board"}


@pytest.mark.asyncio
async def test_split_refuses_access_sidecars_before_selecting_entries(monkeypatch):
    async def allowed(**_kwargs):
        return SimpleNamespace(allowed=True)

    async def can_create(*_args, **_kwargs):
        return True

    async def writable(_track):
        return None

    monkeypatch.setattr(track_restructuring, "policy_evaluate", allowed)
    monkeypatch.setattr(
        "app.services.permissions.can_create_track_under_workspace", can_create
    )
    monkeypatch.setattr(track_restructuring, "assert_track_schema_writable", writable)
    app = await App.create(
        name="Sidecar split app",
        workspace_id="merge-workspace",
        owner_user_id="merge-user",
    )
    source, _source_model, _source_type = await _track_with_model(
        app, "Sidecar split source", "source_record"
    )
    collaborator = await User.create(id="split-sidecar-user", email="split@example.com")
    await collaborator.connect(source, edge=COLLABORATES_ON, role="editor")

    result = await track_restructuring.prepare_track_split(
        user_id="merge-user",
        source_track_id=source.id,
        new_track_title="Split destination",
        entry_type_keys=["source_record"],
        workspace_id="merge-workspace",
    )

    assert result["error"] == "unsupported_sidecar"
    assert "collaborators" in result["detail"]
    assert await Track.get(source.id) is not None


@pytest.mark.asyncio
async def test_split_refuses_more_than_500_selected_entries(monkeypatch):
    async def allowed(**_kwargs):
        return SimpleNamespace(allowed=True)

    async def can_create(*_args, **_kwargs):
        return True

    async def writable(_track):
        return None

    monkeypatch.setattr(track_restructuring, "policy_evaluate", allowed)
    monkeypatch.setattr(
        "app.services.permissions.can_create_track_under_workspace", can_create
    )
    monkeypatch.setattr(track_restructuring, "assert_track_schema_writable", writable)
    app = await App.create(
        name="Oversized split app",
        workspace_id="merge-workspace",
        owner_user_id="merge-user",
    )
    source, _source_model, source_type = await _track_with_model(
        app, "Oversized split source", "source_record"
    )
    original_nodes_page = Track.nodes_page

    async def oversized_page(track, *args, **kwargs):
        if track.id == source.id:
            return [
                SimpleNamespace(
                    id=f"split-entry-{index}",
                    type_id=source_type.id,
                    custom_fields={},
                    tags=[],
                    record_revision=1,
                )
                for index in range(501)
            ], None
        return await original_nodes_page(track, *args, **kwargs)

    monkeypatch.setattr(Track, "nodes_page", oversized_page)
    result = await track_restructuring.prepare_track_split(
        user_id="merge-user",
        source_track_id=source.id,
        new_track_title="Oversized split destination",
        entry_type_keys=["source_record"],
        workspace_id="merge-workspace",
    )

    assert result["error"] == "too_many_entries"
    assert "500" in result["detail"]
    assert await Track.get(source.id) is not None


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


@pytest.mark.asyncio
@pytest.mark.parametrize("selector_kind", ["entry_type", "filter"])
@pytest.mark.parametrize("fail_after_move", [False, True])
async def test_split_track_clones_schema_tags_views_and_moves_selected_entries(
    monkeypatch, selector_kind, fail_after_move
):
    postgres_mode = os.environ.get("INTEGRAL_TEST_DB") == "postgres"
    if fail_after_move and not postgres_mode:
        pytest.skip("atomic split rollback requires PostgreSQL transaction mode")
    if not postgres_mode:
        _enable_in_memory_graph_transactions(monkeypatch)

    @asynccontextmanager
    async def transaction():
        yield None

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

    async def can_create(*_args, **_kwargs):
        return True

    async def branch(*_args, **_kwargs):
        return tracks_registry

    async def emit(**_kwargs):
        return None

    monkeypatch.setattr(track_restructuring, "policy_evaluate", allowed)
    monkeypatch.setattr(track_restructuring, "assert_track_schema_writable", writable)
    monkeypatch.setattr(bulk_move_entries, "policy_evaluate", allowed)
    if not postgres_mode:
        monkeypatch.setattr(
            track_restructuring, "postgres_graph_transaction", transaction
        )
        monkeypatch.setattr(
            bulk_move_entries, "postgres_graph_transaction", transaction
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
        "app.services.permissions.can_create_track_under_workspace", can_create
    )
    monkeypatch.setattr("app.services.app_graph._resolve_workspace_branch", branch)
    monkeypatch.setattr(
        "app.services.operational_model_runtime.sync_attached_manifest", no_op
    )

    app = await App.create(
        name="Split test app",
        workspace_id="merge-workspace",
        owner_user_id="merge-user",
    )
    source, source_model, source_type = await _track_with_model(
        app, "Source", "source_record", [{"key": "status", "type": "text"}]
    )
    tag = await Tag.create(
        name="Urgent",
        name_fold="urgent",
        track_id=source.id,
        group_key="priority",
        applies_to_entry_types=["source_record"],
    )
    await source_model.connect(tag, edge=CONTAINS)
    registry = await Views.create(
        track_id=source.id, operational_model_id=source_model.id
    )
    await source_model.connect(registry)
    source_view = await View.create(
        name="Urgent board",
        name_fold="urgent board",
        type="feed",
        config={"filters": [{"field": "tags", "op": "eq", "value": tag.id}]},
        track_id=source.id,
        operational_model_id=source_model.id,
        entry_type_keys=["source_record"],
        default_entry_type_key="source_record",
    )
    await registry.connect(source_view, edge=CATALOGS)
    entry = await Entry.create(
        title="Split this record",
        track_id=source.id,
        type_id=source_type.id,
        tags=[tag.id],
        custom_fields={"status": "Open"},
        record_revision=1,
    )
    await source.connect(entry, edge=CONTAINS)
    await entry.connect(source_type, edge=IS_OF_TYPE)
    await entry.connect(tag, edge=TAGGED_WITH, tagged_by="split-user")
    user = await User.create(id="split-user", email="split@example.com")

    tracks_registry = await Tracks.create(workspace_id=source.workspace_id)
    created_track_ids = []
    original_track_create = Track.create

    async def capture_track_create(**kwargs):
        created = await original_track_create(**kwargs)
        if kwargs.get("title") == "Priority work":
            created_track_ids.append(created.id)
        return created

    monkeypatch.setattr(Track, "create", capture_track_create)
    selector = (
        {"entry_type_keys": ["source_record"]}
        if selector_kind == "entry_type"
        else {
            "filters": [{"field": "custom_fields.status", "op": "eq", "value": "Open"}]
        }
    )
    preview = await track_restructuring.prepare_track_split(
        user_id=user.id,
        source_track_id=source.id,
        new_track_title="Priority work",
        **selector,
        workspace_id=source.workspace_id,
    )
    assert preview["affected_count"] == 1, preview
    if fail_after_move:
        original_move = track_restructuring.move_entries

        async def move_then_fail(**kwargs):
            moved = await original_move(**kwargs)
            assert not moved.get("error"), moved
            return {"error": True, "message": "injected post-move split failure"}

        monkeypatch.setattr(track_restructuring, "move_entries", move_then_fail)
    result = await track_restructuring.split_track(
        user_id=user.id,
        payload={
            "source_track_id": source.id,
            "new_track_title": "Priority work",
            **selector,
            "entry_ids": preview["entry_ids"],
            "record_revisions": preview["record_revisions"],
            "preview_fingerprint": preview["preview_fingerprint"],
        },
        workspace_id=source.workspace_id,
    )
    if fail_after_move:
        assert result["error_code"] == "track_split_rolled_back", result
        assert created_track_ids
        assert await Track.get(created_track_ids[0]) is None
        assert (await Entry.get(entry.id)).track_id == source.id
        assert await Track.get(source.id) is not None
        assert await OperationalModel.get(source_model.id) is not None
        assert await View.get(source_view.id) is not None
        return
    assert result.get("split") is True, result
    destination = await Track.get(result["target_track_id"])
    assert destination is not None
    assert (await Entry.get(entry.id)).track_id == destination.id
    destination_model = await track_restructuring.get_track_attached_operational_model(
        destination
    )
    destination_types = await destination_model.nodes(
        edge=[CONTAINS], node=["EntryType"]
    )
    assert [track_restructuring._type_key(item) for item in destination_types] == [
        "source_record"
    ]
    destination_tags = await destination_model.nodes(edge=[CONTAINS], node=["Tag"])
    assert len(destination_tags) == 1
    moved_entry = await Entry.get(entry.id)
    assert moved_entry.tags == [destination_tags[0].id]
    destination_registry, destination_views = await track_restructuring._track_views(
        destination, destination_model
    )
    assert destination_registry is not None
    assert len(destination_views) == 1
    assert destination_views[0].config["filters"][0]["value"] == destination_tags[0].id
    assert await Track.get(source.id) is not None
