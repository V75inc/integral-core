"""Normal record lifecycle under a deny-only App restriction."""

import pytest

from app.agentive.tooling.invoke import invoke_route_in_process
from app.api.entries import get_entry
from app.api.errors import BadRequestError
from app.models.edges import CONTAINS, HAS_APPLICATION_DEFINITION, OWNS
from app.models.nodes import App, ApplicationDefinition, Entry
from app.services.app_graph import catalog_app
from app.services.application_definitions import definition_fingerprint
from app.services.attachment_entry_write import (
    AttachmentEntryReadonlyError,
    attachment_entry_write,
)
from app.services.entry_create import create_entry_in_track
from app.services.entry_update import update_entry_in_track
from app.services.entry_write_check import check_entry_write
from app.services.permissions import get_user_node
from tests.test_entry_create_type_id import _bootstrap_track_with_type

pytestmark = [pytest.mark.asyncio, pytest.mark.smoke]


async def _installed_policy(rule):
    user, track, entry_type = await _bootstrap_track_with_type(
        "record-readonly@example.com"
    )
    entry_type.form_schema = {
        "fields": [{"key": "phase", "name": "Phase", "type": "text"}]
    }
    await entry_type.save()
    app = await App.create(name="Record Review", workspace_id=track.workspace_id)
    await catalog_app(app)
    owner = await get_user_node(user)
    await owner.connect(app, edge=OWNS)
    await app.connect(track, edge=CONTAINS)
    manifest = {"app": {"protected_state": {"post": {"readonly_when": rule}}}}
    definition = await ApplicationDefinition.create(
        app_id=app.id,
        workspace_id=track.workspace_id,
        revision=1,
        status="active",
        canonical_manifest=manifest,
        manifest_fingerprint=definition_fingerprint(manifest),
    )
    await app.connect(definition, edge=HAS_APPLICATION_DEFINITION)
    app.active_definition_id = definition.id
    app.active_definition_revision = 1
    await app.save()

    return user, track, entry_type


async def test_app_record_standard_fields_lock_after_state_change():
    user, track, entry_type = await _installed_policy(
        {"field": "phase", "equals": "closed"}
    )
    entry = await create_entry_in_track(
        track=track,
        user_id=user,
        type_id=entry_type.id,
        title="Original title",
        custom_fields={"phase": "open"},
    )
    await update_entry_in_track(entry_id=entry.id, user_id=user, body="Reviewed note")
    async with attachment_entry_write(entry, user) as current:
        assert current.body == "Reviewed note"
    await update_entry_in_track(
        entry_id=entry.id, user_id=user, custom_fields={"phase": "closed"}
    )

    result = await invoke_route_in_process(
        get_entry,
        principal_id=user,
        scope=track.workspace_id,
        entry_id=entry.id,
    )
    assert result["entry"]["app_record_readonly"] is True
    assert not (
        await check_entry_write(user_id=user, entry_id=entry.id, title="New title")
    )["ok"]
    with pytest.raises(BadRequestError) as refused:
        await update_entry_in_track(entry_id=entry.id, user_id=user, body="New note")
    assert refused.value.details["error_code"] == "app_record_readonly"
    assert (await Entry.find_one({"id": entry.id})).body == "Reviewed note"
    with pytest.raises(AttachmentEntryReadonlyError):
        async with attachment_entry_write(entry, user):
            pytest.fail("A read-only rule must refuse before the attachment effect")


async def test_related_record_closes_during_upload(monkeypatch):
    """A concurrent phase change completes during storage and prevents publication."""
    import asyncio
    from unittest.mock import AsyncMock

    from app.api import attachments
    from app.models.nodes import Attachment, EntryType
    from app.services.app_graph import ensure_track_attached_operational_model
    from app.services.workspace_storage_usage import get_usage

    user, track, entry_type = await _installed_policy(
        {
            "relation": "parent",
            "related_entry_type": "review",
            "field": "phase",
            "equals": "closed",
        }
    )
    model = await ensure_track_attached_operational_model(track)
    review_type = await EntryType.create(
        name="Review", track_id=track.id, form_schema=entry_type.form_schema
    )
    await model.connect(review_type, edge=CONTAINS)
    review = await create_entry_in_track(
        track=track,
        user_id=user,
        type_id=review_type.id,
        title="Review",
        custom_fields={"phase": "open"},
    )
    entry = await Entry.create(
        title="Summary",
        track_id=track.id,
        type_id=entry_type.id,
        custom_fields={"parent": review.id},
    )
    await track.connect(entry, edge=CONTAINS)
    started = asyncio.Event()
    resume = asyncio.Event()
    storage = AsyncMock()

    async def store(**kwargs):
        started.set()
        await resume.wait()
        return {"path": "prepared/original"}

    storage.save_attachment.side_effect = store
    monkeypatch.setattr(attachments, "get_attachment_storage_service", lambda: storage)

    async def thumbnail(attachment, **kwargs):
        attachment.thumb_storage_key = "prepared/thumbnail"
        return attachment.thumb_storage_key

    monkeypatch.setattr(attachments, "ensure_thumbnail", thumbnail)
    publish = AsyncMock()
    monkeypatch.setattr(attachments, "_publish_entry_attachment", publish)
    usage = await get_usage(track.workspace_id)
    upload = asyncio.create_task(
        attachments._persist_assembled_content(
            entry=entry,
            user_id=user,
            filename="fixture.txt",
            content=b"test fixture",
            declared_mime="text/plain",
            sha256_hex="fixture",
        )
    )
    try:
        await asyncio.wait_for(started.wait(), 8)
        await asyncio.wait_for(
            update_entry_in_track(
                entry_id=review.id, user_id=user, custom_fields={"phase": "closed"}
            ),
            8,
        )
    finally:
        resume.set()
        with pytest.raises(AttachmentEntryReadonlyError):
            await upload
    assert {call.args[0] for call in storage.delete_attachment.await_args_list} == {
        "prepared/original",
        "prepared/thumbnail",
    }
    assert not (await Entry.find_one({"id": entry.id})).attachment_ids
    assert (
        await Attachment.find_one(
            {"id": storage.save_attachment.await_args.kwargs["attachment_id"]}
        )
        is None
    )
    assert (await get_usage(track.workspace_id)).bytes_used == usage.bytes_used
    publish.assert_not_awaited()
    # The related record is independently editable. Reopening it restores ordinary writes.
    await update_entry_in_track(
        entry_id=review.id, user_id=user, custom_fields={"phase": "open"}
    )
    async with attachment_entry_write(entry, user) as current:
        assert current.id == entry.id


async def test_attachment_graph_failure_reclaims_bytes_and_charge(monkeypatch):
    from unittest.mock import AsyncMock

    from app.api import attachments
    from app.models.nodes import Attachment
    from app.services.workspace_storage_usage import get_usage

    user, track, entry_type = await _installed_policy(
        {"field": "phase", "equals": "closed"}
    )
    entry = await create_entry_in_track(
        track=track,
        user_id=user,
        type_id=entry_type.id,
        title="File target",
        custom_fields={"phase": "open"},
    )
    storage = AsyncMock()
    storage.save_attachment.return_value = {"path": "prepared/file"}
    monkeypatch.setattr(attachments, "get_attachment_storage_service", lambda: storage)
    original = Entry.connect

    async def fail_link(self, target, **kwargs):
        if isinstance(target, Attachment):
            raise RuntimeError("simulated graph failure")
        return await original(self, target, **kwargs)

    monkeypatch.setattr(Entry, "connect", fail_link)
    usage = await get_usage(track.workspace_id)
    with pytest.raises(RuntimeError, match="simulated graph failure"):
        await attachments._persist_assembled_content(
            entry=entry,
            user_id=user,
            filename="draft.txt",
            content=b"draft",
            declared_mime="text/plain",
            sha256_hex="draft",
        )
    storage.delete_attachment.assert_awaited_once_with("prepared/file")
    assert not (await Entry.find_one({"id": entry.id})).attachment_ids
    assert (
        await Attachment.find_one(
            {"id": storage.save_attachment.await_args.kwargs["attachment_id"]}
        )
        is None
    )
    assert (await get_usage(track.workspace_id)).bytes_used == usage.bytes_used


@pytest.mark.parametrize("use_yaml", [False, True], ids=["mapping", "yaml"])
async def test_compile_readonly_policy(use_yaml):
    import yaml

    from app.exceptions import OperationalModelValidationError
    from app.services.operational_model_compile import compile_canonical_manifest
    from tests.test_app_lifecycle import _minimal_app_manifest

    manifest = _minimal_app_manifest(package_name="policy-fixture")
    track = manifest["app"]["tracks"][0]
    review = {
        "key": "review",
        "name": "Review",
        "fields": [{"key": "phase", "name": "Phase", "type": "text"}],
    }
    track["entry_types"].append(review)
    relation = {"target": "entry", "target_entry_types": ["review"]}
    track["entry_types"][0]["fields"].append(
        {"key": "parent", "type": "relation", "relation": relation}
    )
    rule = {
        "relation": "parent",
        "related_entry_type": "review",
        "field": "phase",
        "equals": "closed",
    }
    manifest["app"]["protected_state"] = {"note": {"readonly_when": rule}}

    def compile_fixture():
        if use_yaml:
            return compile_canonical_manifest(manifest_yaml=yaml.safe_dump(manifest))
        return compile_canonical_manifest(manifest=manifest)

    with pytest.raises(OperationalModelValidationError, match="requires a trusted App"):
        compile_fixture()
    manifest["package"]["trust_tier"] = "trusted"
    assert compile_fixture()["app"]["protected_state"]["note"]["readonly_when"] == rule
    review["fields"][0]["type"] = "number"
    with pytest.raises(OperationalModelValidationError, match="text or single-select"):
        compile_fixture()
    review["fields"][0]["type"] = "text"
    relation["target"] = "track"
    with pytest.raises(
        OperationalModelValidationError, match="single, non-chained relation"
    ):
        compile_fixture()


async def test_reprocess_rechecks_policy_after_parser_returns(monkeypatch):
    from unittest.mock import AsyncMock

    from app.api import attachments
    from app.models.edges import HAS_ATTACHMENT
    from app.models.nodes import Attachment
    from app.services.attachment_metadata import service as metadata

    user, track, entry_type = await _installed_policy(
        {"field": "phase", "equals": "closed"}
    )
    entry = await create_entry_in_track(
        track=track,
        user_id=user,
        type_id=entry_type.id,
        title="File target",
        custom_fields={"phase": "open"},
    )
    source = await Attachment.create(
        filename="fixture.txt",
        mime_type="text/plain",
        size=4,
        storage_key="prepared/file",
        scan_status="clean",
        uploaded_by=user,
    )
    await entry.connect(source, edge=HAS_ATTACHMENT)

    class ClosingStore:
        async def read_attachment(self, key):
            from app.services.app_operations.transaction_scope import (
                require_outside_graph_transaction,
            )

            require_outside_graph_transaction()
            await update_entry_in_track(
                entry_id=entry.id, user_id=user, custom_fields={"phase": "closed"}
            )
            return b"test"

    monkeypatch.setattr(
        metadata, "get_attachment_storage_service", lambda: ClosingStore()
    )
    enqueue = AsyncMock()
    monkeypatch.setattr(
        "app.services.attachment_metadata.analysis.enqueue_analysis", enqueue
    )
    with pytest.raises(AttachmentEntryReadonlyError):
        await invoke_route_in_process(
            attachments.reprocess_attachment_metadata,
            principal_id=user,
            scope=track.workspace_id,
            attachment_id=source.id,
        )
    after = await Attachment.find_one({"id": source.id})
    assert after.metadata_status == "failed"
    assert not after.extracted_text
    enqueue.assert_not_awaited()
