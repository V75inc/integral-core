"""Real resource round trips, not just ZIP framing or mocked node writes."""

import pytest

from app.api.errors import BadRequestError, InsufficientPermissionsError
from app.models.edges import CONTAINS, HAS_ATTACHMENT, HAS_VIEW, REFERENCES
from app.models.nodes import App, Attachment, Entry, EntryType, Track, View
from app.schemas.portable_transfer import ExportSelection
from app.services.app_graph import (
    create_track_view,
    get_track_attached_operational_model,
)
from app.services.app_service import create_app_for_user
from app.services.attachment_storage import get_attachment_storage_service
from app.services.entry_create import create_entry_in_track
from app.services.personal_workspace import ensure_personal_workspace_for_user_id
from app.services.portable_archive import encode_archive
from app.services.portable_transfer import (
    export_resources,
    import_resources,
    read_archive,
)
from app.services.track_service import create_track_in_space


async def fixture(user_id):
    workspace = await ensure_personal_workspace_for_user_id(user_id)
    app = await create_app_for_user(
        user_id, "Archive Source", workspace_id=workspace.id
    )
    track = await create_track_in_space(
        user_id, title="Records", workspace_id=workspace.id, app_id=app.id
    )
    cp = await get_track_attached_operational_model(track)
    types = await cp.nodes(edge=[CONTAINS], node=["EntryType"])
    et = types[0]
    et.form_schema = {
        "fields": [
            {"key": "amount", "name": "Amount", "type": "number", "required": True},
            {"key": "note", "name": "Note", "type": "text"},
            {
                "key": "related",
                "name": "Related",
                "type": "relation",
                "relation": {"target": "entry", "allow_cross_track": True},
            },
            {"key": "document", "name": "Document", "type": "file"},
        ]
    }
    await et.save()
    from app.services.operational_model_runtime import sync_attached_manifest

    await sync_attached_manifest(cp)
    entry = await create_entry_in_track(
        track=track,
        user_id=user_id,
        title="First",
        body="Full body ✓",
        type_id=et.id,
        custom_fields={"amount": 0, "note": "literal original id"},
    )
    second = await create_entry_in_track(
        track=track,
        user_id=user_id,
        title="Second",
        type_id=et.id,
        custom_fields={"amount": 12.5, "related": entry.id, "note": entry.id},
    )
    # A true cycle exercises allocation before validation. It is subsequently
    # validated through the normal update path before export.
    from app.services.entry_update import update_entry_in_track

    await update_entry_in_track(
        entry_id=entry.id, user_id=user_id, custom_fields={"related": second.id}
    )
    entry = await Entry.get(entry.id)
    return workspace.id, app, track, entry, second


@pytest.mark.asyncio
@pytest.mark.smoke
@pytest.mark.parametrize("kind", ["app", "track"])
@pytest.mark.parametrize("mode", ["configuration", "full"])
async def test_round_trip(kind, mode, authenticated_client, test_user, monkeypatch):
    ws, app, track, first, second = await fixture(test_user.id)
    # Avoid a background extraction outliving this test's disposable graph.
    from app.services.attachment_metadata.service import get_metadata_extraction_service

    monkeypatch.setattr(
        get_metadata_extraction_service(), "schedule_extraction", lambda *_: None
    )
    content = b"portable file contents\n"
    attachment = await Attachment.create(
        filename="notes.txt",
        mime_type="text/plain",
        source_type="file",
        scan_status="skipped",
        size=len(content),
        uploaded_by=test_user.id,
    )
    await first.connect(attachment, edge=HAS_ATTACHMENT)
    storage = get_attachment_storage_service()
    stored = await storage.save_attachment(
        entry_id=first.id,
        attachment_id=attachment.id,
        filename="notes.txt",
        content=content,
        mime_type="text/plain",
    )
    attachment.storage_key = stored["path"]
    await attachment.save()
    from app.services.entry_update import update_entry_in_track

    first.attachment_ids = [attachment.id]
    await first.save()
    await update_entry_in_track(
        entry_id=first.id,
        user_id=test_user.id,
        attachment_ids=[attachment.id],
        custom_fields={"document": attachment.id},
    )
    data = await export_resources(
        user_id=test_user.id,
        workspace_id=ws,
        selection=ExportSelection(
            kind=kind, ids=[app.id if kind == "app" else track.id], mode=mode
        ),
    )
    descriptor, files = await read_archive(data)
    assert bool(files) == (mode == "full")
    result = await import_resources(user_id=test_user.id, workspace_id=ws, data=data)
    assert result["entries"] == (2 if mode == "full" else 0)
    assert result["attachments"] == (1 if mode == "full" else 0)
    if kind == "app":
        copied_app = await App.get(result["resource_ids"][0])
        assert copied_app.name != app.name  # collision creates a new copy
        assert copied_app.visibility == "private"
        tracks = await copied_app.nodes(edge=[CONTAINS], node=["Track"])
        copied = tracks[0]
    else:
        copied = await Track.get(result["resource_ids"][0])
    assert copied.id != track.id
    assert copied.visibility == "private"
    cp = await get_track_attached_operational_model(copied)
    types = await cp.nodes(edge=[CONTAINS], node=["EntryType"])
    assert len(types) == 1
    assert types[0].form_schema["fields"][0]["required"] is True
    entries = await copied.nodes(edge=[CONTAINS], node=["Entry"])
    if mode == "configuration":
        assert entries == []
        return
    rows = {entry.title: entry for entry in entries}
    a, b = rows["First"], rows["Second"]
    assert a.body == "Full body ✓"
    assert a.created_at == first.created_at
    assert a.custom_fields["amount"] == 0
    assert b.custom_fields["amount"] == 12.5
    assert a.custom_fields["related"] == b.id
    assert b.custom_fields["related"] == a.id
    assert b.custom_fields["note"] == first.id  # free text isn't an ID binding
    targets = await a.nodes(edge=[REFERENCES], node=["Entry"])
    assert [t.id for t in targets] == [b.id]
    attachments = await a.nodes(edge=[HAS_ATTACHMENT], node=["Attachment"])
    assert len(attachments) == 1
    assert attachments[0].id != attachment.id
    assert a.custom_fields["document"] == attachments[0].id
    assert await storage.read_attachment(attachments[0].storage_key) == content
    # Imported material must itself be exportable, without dangling source refs.
    again = await export_resources(
        user_id=test_user.id,
        workspace_id=ws,
        selection=ExportSelection(kind="track", ids=[copied.id], mode="full"),
    )
    descriptor, files = await read_archive(again)
    assert len(descriptor.tracks[0].entries) == 2
    assert list(files.values()) == [content]


@pytest.mark.asyncio
async def test_reject_external_relation_and_wrong_workspace(
    authenticated_client, test_user
):
    ws, app, track, first, _ = await fixture(test_user.id)
    other = await create_track_in_space(test_user.id, title="External", workspace_id=ws)
    et = (
        await (await get_track_attached_operational_model(other)).nodes(
            edge=[CONTAINS], node=["EntryType"]
        )
    )[0]
    target = await create_entry_in_track(
        track=other, user_id=test_user.id, type_id=et.id, title="Outside"
    )
    from app.services.entry_update import update_entry_in_track

    await update_entry_in_track(
        entry_id=first.id, user_id=test_user.id, custom_fields={"related": target.id}
    )
    with pytest.raises(BadRequestError, match="outside this export"):
        await export_resources(
            user_id=test_user.id,
            workspace_id=ws,
            selection=ExportSelection(kind="app", ids=[app.id], mode="full"),
        )
    with pytest.raises(InsufficientPermissionsError):
        await export_resources(
            user_id=test_user.id,
            workspace_id="wrong",
            selection=ExportSelection(kind="track", ids=[track.id]),
        )


@pytest.mark.asyncio
async def test_bad_field_restore_cleans_up_new_resources(
    authenticated_client, test_user
):
    ws, app, _, _, _ = await fixture(test_user.id)
    data = await export_resources(
        user_id=test_user.id,
        workspace_id=ws,
        selection=ExportSelection(kind="app", ids=[app.id], mode="full"),
    )
    descriptor, files = await read_archive(data)
    descriptor.apps[0].tracks[0].entries[0].custom_fields["unknown"] = "must fail"
    before = {n.id for n in await App.find({"context.workspace_id": ws})}
    with pytest.raises(BadRequestError):
        await import_resources(
            user_id=test_user.id,
            workspace_id=ws,
            data=encode_archive(descriptor.model_dump(), files),
        )
    assert {n.id for n in await App.find({"context.workspace_id": ws})} == before
    assert not await Entry.find({"context.status": "importing"})


@pytest.mark.asyncio
@pytest.mark.smoke
async def test_http_export_preview_import(authenticated_client, test_user):
    ws, app, _, _, _ = await fixture(test_user.id)
    headers = {"X-Integral-Scope": f"ws:{ws}"}
    response = await authenticated_client.post(
        "/api/portable/export",
        headers=headers,
        json={"kind": "app", "ids": [app.id], "mode": "configuration"},
    )
    assert response.status_code == 200, response.text
    assert response.headers["content-type"] == "application/zip"
    assert response.headers["cache-control"] == "no-store"
    headers["Content-Type"] = "application/zip"
    preview = await authenticated_client.post(
        "/api/portable/preview", headers=headers, content=response.content
    )
    assert preview.status_code == 200, preview.text
    result = await authenticated_client.post(
        "/api/portable/import", headers=headers, content=response.content
    )
    assert result.status_code == 200, result.text


@pytest.mark.asyncio
async def test_multiple_tracks_restore_into_existing_app(
    authenticated_client, test_user
):
    ws, app, track, first, _ = await fixture(test_user.id)
    other = await create_track_in_space(test_user.id, title="Related", workspace_id=ws)
    target = await create_entry_in_track(
        track=other, user_id=test_user.id, title="Target"
    )
    from app.services.entry_update import update_entry_in_track

    await update_entry_in_track(
        entry_id=first.id, user_id=test_user.id, custom_fields={"related": target.id}
    )
    data = await export_resources(
        user_id=test_user.id,
        workspace_id=ws,
        selection=ExportSelection(kind="track", ids=[track.id, other.id], mode="full"),
    )
    destination = await create_app_for_user(
        test_user.id, "Destination", workspace_id=ws
    )
    result = await import_resources(
        user_id=test_user.id, workspace_id=ws, app_id=destination.id, data=data
    )
    assert result["tracks"] == 2 and result["entries"] == 3
    copied = await destination.nodes(edge=[CONTAINS], node=["Track"])
    assert set(t.id for t in copied) == set(result["resource_ids"])
    assert all(t.id not in {track.id, other.id} for t in copied)
    records = [
        e for t in copied for e in await t.nodes(edge=[CONTAINS], node=["Entry"])
    ]
    by_title = {e.title: e for e in records}
    assert by_title["First"].custom_fields["related"] == by_title["Target"].id


@pytest.mark.asyncio
async def test_copy_to_another_workspace_with_tags_and_views(
    authenticated_client, test_user
):
    from app.models.edges import IS_MEMBER_OF, TAGGED_WITH
    from app.models.nodes import Tag, User, Workspace
    from app.services.app_graph import catalog_workspace

    ws, app, track, first, _ = await fixture(test_user.id)
    cp = await get_track_attached_operational_model(track)
    parent = await Tag.create(name="Status", track_id=track.id, group_key="progress")
    await cp.connect(parent, edge=CONTAINS)
    child = await Tag.create(
        name="Ready",
        track_id=track.id,
        group_key="progress",
        parent_tag_id=parent.id,
        aliases=["Available"],
        color="#12AB34",
    )
    await cp.connect(child, edge=CONTAINS)
    await first.connect(child, edge=TAGGED_WITH)
    first.tags = [child.id]
    await first.save()
    await create_track_view(
        track,
        name="Money",
        type="table",
        is_default=True,
        config={"columns": ["title", "amount"], "track_id": track.id},
    )
    from app.services.operational_model_runtime import sync_attached_manifest

    await sync_attached_manifest(cp)
    destination = await Workspace.create(
        name="Portable destination", kind="organization"
    )
    await catalog_workspace(destination)
    user = await User.get(test_user.id)
    await user.connect(destination, edge=IS_MEMBER_OF, role="owner")
    data = await export_resources(
        user_id=test_user.id,
        workspace_id=ws,
        selection=ExportSelection(kind="app", ids=[app.id], mode="full"),
    )
    result = await import_resources(
        user_id=test_user.id, workspace_id=destination.id, data=data
    )
    copied_app = await App.get(result["resource_ids"][0])
    assert copied_app.workspace_id == destination.id
    copied = (await copied_app.nodes(edge=[CONTAINS], node=["Track"]))[0]
    assert copied.workspace_id == destination.id
    records = await copied.nodes(edge=[CONTAINS], node=["Entry"])
    copied_first = next(e for e in records if e.title == first.title)
    copied_tag = await Tag.get(copied_first.tags[0])
    assert copied_tag.id != child.id and copied_tag.name == "Ready"
    assert copied_tag.color == "#12AB34" and copied_tag.aliases == ["Available"]
    assert (await Tag.get(copied_tag.parent_tag_id)).name == "Status"
    saved = next(
        v
        for v in await copied.nodes(edge=[HAS_VIEW], node=["View"])
        if v.name == "Money"
    )
    assert saved.config["track_id"] == copied.id
    assert saved.config["columns"] == ["title", "amount"]


@pytest.mark.asyncio
@pytest.mark.smoke
async def test_excluded_child_prevents_partial_backup(
    authenticated_client, test_user, test_user2
):
    from app.services.sharing import add_collaborator, add_exclusion

    ws, app, _, first, _ = await fixture(test_user.id)
    await add_collaborator(test_user.id, "app", app.id, test_user2.id, "viewer")
    await add_exclusion(test_user.id, "entry", first.id, test_user2.id)
    with pytest.raises(InsufficientPermissionsError):
        await export_resources(
            user_id=test_user2.id,
            workspace_id=ws,
            selection=ExportSelection(kind="app", ids=[app.id], mode="full"),
        )


@pytest.mark.asyncio
async def test_duplicate_or_corrupt_archive_does_not_write(
    authenticated_client, test_user
):
    ws, app, _, _, _ = await fixture(test_user.id)
    data = await export_resources(
        user_id=test_user.id,
        workspace_id=ws,
        selection=ExportSelection(kind="app", ids=[app.id], mode="full"),
    )
    descriptor, _ = await read_archive(data)
    descriptor.apps.append(descriptor.apps[0].model_copy(deep=True))
    before = {n.id for n in await App.find({"context.workspace_id": ws})}
    with pytest.raises(BadRequestError):
        await import_resources(
            user_id=test_user.id,
            workspace_id=ws,
            data=encode_archive(descriptor.model_dump(), {}),
        )
    assert {n.id for n in await App.find({"context.workspace_id": ws})} == before


@pytest.mark.asyncio
async def test_failed_restore_removes_schema_sidecars(authenticated_client, test_user):
    from app.models.nodes import ApplicationDefinition, OperationalModel, Views

    ws, app, _, _, _ = await fixture(test_user.id)
    data = await export_resources(
        user_id=test_user.id,
        workspace_id=ws,
        selection=ExportSelection(kind="app", ids=[app.id], mode="full"),
    )
    descriptor, files = await read_archive(data)
    descriptor.apps[0].tracks[0].entries[-1].custom_fields["amount"] = "not a number"
    classes = [
        App,
        Track,
        Entry,
        OperationalModel,
        EntryType,
        View,
        Views,
        ApplicationDefinition,
    ]
    before = {cls.__name__: {node.id for node in await cls.find({})} for cls in classes}
    with pytest.raises(BadRequestError):
        await import_resources(
            user_id=test_user.id,
            workspace_id=ws,
            data=encode_archive(descriptor.model_dump(), files),
        )
    for cls in classes:
        assert {node.id for node in await cls.find({})} == before[
            cls.__name__
        ], cls.__name__


@pytest.mark.asyncio
async def test_image_bytes_and_inline_image_links_round_trip(
    authenticated_client, test_user, monkeypatch
):
    import io

    from PIL import Image

    from app.services.attachment_metadata.service import get_metadata_extraction_service

    monkeypatch.setattr(
        get_metadata_extraction_service(), "schedule_extraction", lambda *_: None
    )
    ws, app, track, first, _ = await fixture(test_user.id)
    buffer = io.BytesIO()
    Image.new("RGB", (8, 8), "blue").save(buffer, format="PNG")
    image = buffer.getvalue()
    attachment = await Attachment.create(
        filename="blue.png",
        mime_type="image/png",
        size=len(image),
        source_type="file",
        scan_status="skipped",
        uploaded_by=test_user.id,
    )
    await first.connect(attachment, edge=HAS_ATTACHMENT)
    storage = get_attachment_storage_service()
    stored = await storage.save_attachment(
        entry_id=first.id,
        attachment_id=attachment.id,
        filename="blue.png",
        content=image,
        mime_type="image/png",
    )
    attachment.storage_key = stored["path"]
    await attachment.save()
    first.attachment_ids = [attachment.id]
    first.body = f"![Blue square](/api/attachments/{attachment.id}/file)"
    await first.save()
    data = await export_resources(
        user_id=test_user.id,
        workspace_id=ws,
        selection=ExportSelection(kind="app", ids=[app.id], mode="full"),
    )
    result = await import_resources(user_id=test_user.id, workspace_id=ws, data=data)
    copied_app = await App.get(result["resource_ids"][0])
    copied_track = (await copied_app.nodes(edge=[CONTAINS], node=["Track"]))[0]
    copied = next(
        e
        for e in await copied_track.nodes(edge=[CONTAINS], node=["Entry"])
        if e.title == "First"
    )
    copied_image = (await copied.nodes(edge=[HAS_ATTACHMENT], node=["Attachment"]))[0]
    assert await storage.read_attachment(copied_image.storage_key) == image
    assert copied_image.mime_type == "image/png"
    assert copied.body == f"![Blue square](/api/attachments/{copied_image.id}/file)"


@pytest.mark.asyncio
async def test_import_checks_total_file_quota_before_creating_copies(
    authenticated_client, test_user, monkeypatch
):
    import hashlib

    from app.config import settings
    from app.models.nodes import Workspace
    from app.schemas.portable_transfer import PortableFile

    ws, app, _, _, _ = await fixture(test_user.id)
    data = await export_resources(
        user_id=test_user.id,
        workspace_id=ws,
        selection=ExportSelection(kind="app", ids=[app.id], mode="full"),
    )
    archive, _ = await read_archive(data)
    content = b"hi"
    archive.apps[0].tracks[0].entries[0].attachments = [
        PortableFile(
            ref=f"file-{i}",
            filename=f"{i}.txt",
            mime_type="text/plain",
            source_type="file",
            member="files/shared",
            sha256=hashlib.sha256(content).hexdigest(),
        )
        for i in range(2)
    ]
    workspace = await Workspace.get(ws)
    workspace.storage_quota_bytes = 3
    workspace.storage_bytes_used = 0
    await workspace.save()
    monkeypatch.setattr(settings, "STORAGE_QUOTA_ENFORCE", True)
    before = {a.id for a in await App.find({"context.workspace_id": ws})}
    with pytest.raises(BadRequestError, match="quota"):
        await import_resources(
            user_id=test_user.id,
            workspace_id=ws,
            data=encode_archive(archive.model_dump(), {"files/shared": content}),
        )
    assert {a.id for a in await App.find({"context.workspace_id": ws})} == before


@pytest.mark.asyncio
async def test_home_and_dashboard_sources_rebind_to_copied_tracks(
    authenticated_client, test_user
):
    from app.services.dashboard_service import create_dashboard, list_dashboards

    ws, app, track, _, _ = await fixture(test_user.id)
    widgets = [
        {
            "id": "total",
            "type": "metric_card",
            "grid": {"x": 0, "y": 0, "w": 4, "h": 2},
            "data_source": {"kind": "count", "track_ids": [track.id]},
        }
    ]
    app.home_composition = {"title": "Overview", "widgets": widgets}
    await app.save()
    await create_dashboard(
        user_id=test_user.id, app_id=app.id, name="Overview", widgets=widgets
    )
    data = await export_resources(
        user_id=test_user.id,
        workspace_id=ws,
        selection=ExportSelection(kind="app", ids=[app.id]),
    )
    result = await import_resources(user_id=test_user.id, workspace_id=ws, data=data)
    copied = await App.get(result["resource_ids"][0])
    copied_track = (await copied.nodes(edge=[CONTAINS], node=["Track"]))[0]
    assert copied.home_composition["widgets"][0]["data_source"]["track_ids"] == [
        copied_track.id
    ]
    dashboards = await list_dashboards(user_id=test_user.id, app_id=copied.id)
    assert len(dashboards) == 1
    assert dashboards[0].widgets[0]["data_source"]["track_ids"] == [copied_track.id]


@pytest.mark.asyncio
async def test_declarative_composite_field_round_trip(authenticated_client, test_user):
    from app.services.portable_archive import encode_archive

    ws = (await ensure_personal_workspace_for_user_id(test_user.id)).id
    fields = [{"key": "cost", "name": "Cost", "type": "money"}]
    manifest = {
        "operational_model_schema_version": 2,
        "scope": "track",
        "field_types": [
            {"key": "money", "base": "number", "config": {}, "label": "Money"}
        ],
        "track": {
            "entry_types": [{"key": "record", "name": "Record", "fields": fields}]
        },
    }
    payload = {
        "kind": "track",
        "mode": "full",
        "tracks": [
            {
                "ref": "track",
                "title": "Composite fields",
                "manifest": manifest,
                "entry_types": [
                    {"ref": "type", "name": "Record", "form_schema": {"fields": fields}}
                ],
                "entries": [
                    {
                        "ref": "record",
                        "type_ref": "type",
                        "title": "Cost",
                        "custom_fields": {"cost": 25.5},
                    }
                ],
            }
        ],
    }
    result = await import_resources(
        user_id=test_user.id, workspace_id=ws, data=encode_archive(payload, {})
    )
    copied = await Track.get(result["resource_ids"][0])
    entry = (await copied.nodes(edge=[CONTAINS], node=["Entry"]))[0]
    assert entry.custom_fields["cost"] == 25.5
    data = await export_resources(
        user_id=test_user.id,
        workspace_id=ws,
        selection=ExportSelection(kind="track", ids=[copied.id], mode="full"),
    )
    archive, _ = await read_archive(data)
    assert archive.tracks[0].manifest["field_types"][0]["key"] == "money"


@pytest.mark.asyncio
async def test_failed_import_removes_written_attachment_bytes(
    authenticated_client, test_user, monkeypatch
):
    import hashlib

    from app.schemas.portable_transfer import PortableFile

    ws, app, _, _, _ = await fixture(test_user.id)
    data = await export_resources(
        user_id=test_user.id,
        workspace_id=ws,
        selection=ExportSelection(kind="app", ids=[app.id], mode="full"),
    )
    archive, _ = await read_archive(data)
    content = b"rollback attachment\n"
    archive.apps[0].tracks[0].entries[0].attachments = [
        PortableFile(
            ref="n.Attachment.source",
            filename="cleanup.txt",
            mime_type="text/plain",
            source_type="file",
            member="files/one",
            sha256=hashlib.sha256(content).hexdigest(),
        )
    ]
    archive.apps[0].tracks[0].entries[-1].custom_fields[
        "invalid"
    ] = "reject after writing the file"
    storage = get_attachment_storage_service()
    save = storage.save_attachment
    written = []

    async def capture(**kwargs):
        result = await save(**kwargs)
        written.append(result["path"])
        return result

    monkeypatch.setattr(storage, "save_attachment", capture)
    with pytest.raises(BadRequestError):
        await import_resources(
            user_id=test_user.id,
            workspace_id=ws,
            data=encode_archive(archive.model_dump(), {"files/one": content}),
        )
    assert written
    for key in written:
        assert await storage.read_attachment(key) is None


@pytest.mark.asyncio
async def test_track_exports_include_inherited_app_tags(
    authenticated_client, test_user
):
    from app.models.nodes import Tag
    from app.services.app_graph import get_app_attached_operational_model

    ws, app, track, first, _ = await fixture(test_user.id)
    cp = await get_app_attached_operational_model(app)
    tag = await Tag.create(name="Shared label", app_id=app.id)
    await cp.connect(tag, edge=CONTAINS)
    first.tags = [tag.id]
    await first.save()
    data = await export_resources(
        user_id=test_user.id,
        workspace_id=ws,
        selection=ExportSelection(kind="track", ids=[track.id], mode="full"),
    )
    archive, _ = await read_archive(data)
    assert any(t.name == "Shared label" for t in archive.tracks[0].tags)
    result = await import_resources(user_id=test_user.id, workspace_id=ws, data=data)
    copied = await Track.get(result["resource_ids"][0])
    entry = next(
        e
        for e in await copied.nodes(edge=[CONTAINS], node=["Entry"])
        if e.title == first.title
    )
    copied_tag = await Tag.get(entry.tags[0])
    assert copied_tag.name == "Shared label" and copied_tag.id != tag.id
    assert copied_tag.track_id == copied.id


@pytest.mark.asyncio
async def test_multiple_apps_rebind_cross_app_relations_after_name_collisions(
    authenticated_client, test_user
):
    from app.services.entry_update import update_entry_in_track
    from app.services.operational_model_runtime import sync_attached_manifest

    ws, app, track, first, second = await fixture(test_user.id)
    provider = await create_app_for_user(test_user.id, "Provider", workspace_id=ws)
    provider_track = await create_track_in_space(
        test_user.id, title="Targets", workspace_id=ws, app_id=provider.id
    )
    target = await create_entry_in_track(
        track=provider_track, user_id=test_user.id, title="Target in provider"
    )
    cp = await get_track_attached_operational_model(track)
    et = await EntryType.get(first.type_id)
    related = next(
        field for field in et.form_schema["fields"] if field["key"] == "related"
    )
    related["relation"].update({"allow_cross_app": True, "target_app": "Provider"})
    await et.save()
    await sync_attached_manifest(cp)
    await update_entry_in_track(
        entry_id=first.id, user_id=test_user.id, custom_fields={"related": target.id}
    )
    await update_entry_in_track(
        entry_id=second.id, user_id=test_user.id, custom_fields={"related": target.id}
    )
    data = await export_resources(
        user_id=test_user.id,
        workspace_id=ws,
        selection=ExportSelection(kind="app", ids=[app.id, provider.id], mode="full"),
    )
    result = await import_resources(user_id=test_user.id, workspace_id=ws, data=data)
    copies = [await App.get(identity) for identity in result["resource_ids"]]
    copied_app = next(a for a in copies if a.name.startswith("Archive Source"))
    copied_provider = next(a for a in copies if a.name.startswith("Provider"))
    assert copied_provider.name != provider.name
    copied_track = (await copied_app.nodes(edge=[CONTAINS], node=["Track"]))[0]
    copied_target_track = (
        await copied_provider.nodes(edge=[CONTAINS], node=["Track"])
    )[0]
    copied_target = (await copied_target_track.nodes(edge=[CONTAINS], node=["Entry"]))[
        0
    ]
    copied_first = next(
        e
        for e in await copied_track.nodes(edge=[CONTAINS], node=["Entry"])
        if e.title == first.title
    )
    assert copied_first.custom_fields["related"] == copied_target.id
    refs = await copied_first.nodes(edge=[REFERENCES], node=["Entry"])
    assert [e.id for e in refs] == [copied_target.id]
    form = (await EntryType.get(copied_first.type_id)).form_schema
    relation = next(f["relation"] for f in form["fields"] if f["key"] == "related")
    assert relation["resolution"] == f"instance:{copied_provider.id}"
    assert relation["target_app"] == copied_provider.name
