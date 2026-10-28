"""Automatic visual analysis is owned, revision-bound and source-selected."""

import io
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from PIL import Image

from app.api.errors import InsufficientPermissionsError, ResourceConflictError
from app.models.edges import HAS_ATTACHMENT
from app.models.nodes import Attachment
from app.schemas.attachment_analysis import AttachmentAnalysisWrite
from app.services.attachment_metadata import analysis, configuration
from app.services.attachment_metadata.base import (
    AttachmentMetadataExtractor,
    MetadataResult,
)
from app.services.attachment_metadata.service import MetadataExtractionService
from tests.test_embedding_configuration import fixture

pytestmark = [pytest.mark.smoke, pytest.mark.asyncio]


@pytest.mark.parametrize(
    ("status", "mode", "described", "expected"),
    [
        ("pending", "content", False, "pending"),
        ("processing", "content", False, "pending"),
        ("partial", "content", False, "partial"),
        ("failed", "content", False, "failed"),
        ("skipped", "content", False, "limited"),
        ("complete", "content", False, "image_description_unavailable"),
        ("complete", "content", True, "complete"),
        ("complete", "metadata", False, "complete"),
    ],
)
async def test_attachment_coverage_reports_preparation_limits(
    status, mode, described, expected
):
    from app.services.retrieval.index_management import _attachment_coverage

    source = {
        "attachment_id": "file-1",
        "origin": "file_metadata",
        "mime_type": "image/png",
        "extraction_status": status,
    }
    sources = {"attachments.file-1.metadata": source}
    if described:
        sources["attachments.file-1.description"] = {
            "attachment_id": "file-1",
            "origin": "generated_description",
        }
    counts = dict.fromkeys(
        (
            "examined",
            "complete",
            "pending",
            "partial",
            "failed",
            "limited",
            "image_description_unavailable",
        ),
        0,
    )
    _attachment_coverage(
        {"definition": {"attachments": mode}, "passage_sources": sources}, counts
    )
    assert counts["examined"] == counts[expected] == 1


async def test_provider_raster_does_not_reveal_invisible_rgb_pixels():
    original = io.BytesIO()
    Image.new("RGBA", (32, 32), (255, 0, 0, 0)).save(original, format="PNG")
    with Image.open(io.BytesIO(analysis._vision_input(original.getvalue()))) as raster:
        assert raster.mode == "RGB"
        assert min(raster.getpixel((16, 16))) > 245
        assert not raster.getexif()


async def test_overlapping_manual_reprocesses_schedule_one_analysis(monkeypatch):
    import asyncio

    from tests.test_attachment_search_sources import file
    from tests.test_resource_location import graph

    owner, _, _, _, entry = await graph()
    attachment = await file(entry, storage_key="fixture")
    started, release = asyncio.Event(), asyncio.Event()

    class Parser(AttachmentMetadataExtractor):
        mime_prefixes = ("text/",)

        async def extract(self, **kwargs):
            started.set()
            await release.wait()
            return MetadataResult(extracted_text="Current native text.")

    queue = AsyncMock()
    monkeypatch.setattr(analysis, "enqueue_analysis", queue)
    monkeypatch.setattr(
        "app.services.attachment_metadata.service.get_attachment_storage_service",
        lambda: SimpleNamespace(read_attachment=AsyncMock(return_value=b"source")),
    )
    service = MetadataExtractionService((Parser(),))
    options = {"force_analysis": True, "analysis_principal_id": owner.id}
    first = asyncio.create_task(service.extract_now(attachment.id, **options))
    await started.wait()
    second = asyncio.create_task(service.extract_now(attachment.id, **options))
    await asyncio.sleep(0)
    release.set()
    results = await asyncio.gather(first, second)
    queue.assert_awaited_once_with(results[0], force=True, principal_id=owner.id)


async def test_workspace_owner_can_disable_descriptions_with_a_revision(monkeypatch):
    from app.config import settings

    monkeypatch.setattr(settings, "ATTACHMENT_IMAGE_DESCRIPTIONS_ENABLED", True)
    user, workspace, _, _ = await fixture()
    assert await configuration.enabled(workspace.id)
    with pytest.raises(InsufficientPermissionsError):
        await configuration.save(
            "foreign", workspace.id, AttachmentAnalysisWrite(enabled=False, revision=0)
        )
    result = await configuration.save(
        user.user_id, workspace.id, AttachmentAnalysisWrite(enabled=False, revision=0)
    )
    assert result["revision"] == 1 and not result["configuration"]["enabled"]
    assert not await configuration.enabled(workspace.id)
    with pytest.raises(ResourceConflictError):
        await configuration.save(
            user.user_id,
            workspace.id,
            AttachmentAnalysisWrite(enabled=True, revision=0),
        )
    monkeypatch.setattr(settings, "ATTACHMENT_IMAGE_DESCRIPTIONS_ENABLED", False)
    await configuration.save(
        user.user_id, workspace.id, AttachmentAnalysisWrite(enabled=True, revision=1)
    )
    assert not await configuration.enabled(workspace.id)


async def test_vision_uses_only_the_configured_capable_roles(monkeypatch):
    from app.agentive.harness import model_routing
    from app.services import model_settings

    monkeypatch.setattr(configuration, "enabled", AsyncMock(return_value=True))
    heavy = SimpleNamespace(model="openai/text-only")
    light = SimpleNamespace(model="openai/vision-enabled")
    resolver = AsyncMock(return_value=SimpleNamespace(heavy=heavy, light=light))
    monkeypatch.setattr(model_routing, "resolve_turn_routes", resolver)
    monkeypatch.setattr(
        model_settings,
        "model_capabilities",
        lambda provider, model: {"vision": model == "vision-enabled"},
    )
    assert await analysis._vision_route("workspace-bound") is light
    assert resolver.await_args.kwargs["workspace_id"] == "workspace-bound"
    monkeypatch.setattr(configuration, "enabled", AsyncMock(return_value=False))
    resolver.reset_mock()
    assert await analysis._vision_route("workspace-bound") is None
    resolver.assert_not_awaited()


async def test_queue_contains_identity_only_and_respects_canonical_selection(
    monkeypatch,
):
    from app.agentive.services import work_items
    from tests.test_resource_location import graph

    user, workspace, _, track, entry = await graph()
    monkeypatch.setattr(configuration, "supported", lambda: True)
    attachment = await Attachment.create(
        filename="private-source.txt",
        mime_type="text/plain",
        uploaded_by=user.id,
        content_hash="file-hash",
        extracted_text="Private source wording.",
    )
    await entry.connect(attachment, edge=HAS_ATTACHMENT)
    enqueue = AsyncMock()
    monkeypatch.setattr(work_items, "enqueue_work_item", enqueue)
    await analysis.enqueue_analysis(attachment)
    assert enqueue.await_count == 1
    payload = enqueue.await_args.kwargs
    assert payload["workspace_id"] == workspace.id
    assert payload["kind"] == "attachment_analysis"
    assert payload["retry_policy"].max_attempts == 1
    assert "Private source wording" not in str(payload)
    assert payload["input_payload"]["attachment_id"] == attachment.id
    track.search_index = {"attachments": "none"}
    await track.save()
    enqueue.reset_mock()
    await analysis.enqueue_analysis(attachment)
    enqueue.assert_not_awaited()
