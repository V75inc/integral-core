"""Concurrency and event-loop regression gates for repeated request work."""

import ast
import asyncio
import threading
from pathlib import Path
from types import SimpleNamespace

import pytest

pytestmark = [pytest.mark.unit, pytest.mark.smoke]


@pytest.mark.asyncio
async def test_concurrent_library_reads_share_executor_work_and_survive_cancel(
    monkeypatch,
):
    from app.services import operational_model_library_sync as source

    owner = threading.get_ident()
    started = threading.Event()
    release = threading.Event()
    calls = []

    def snapshot():
        assert threading.get_ident() != owner
        calls.append(True)
        started.set()
        assert release.wait(5)
        return "source", [SimpleNamespace(slug="sample")], []

    monkeypatch.setattr(source, "_load_library_snapshot", snapshot)
    tasks = [
        asyncio.create_task(source.load_library_bundle_index_async()) for _ in range(20)
    ]
    try:
        for _ in range(100):
            if started.is_set():
                break
            await asyncio.sleep(0.01)
        assert started.is_set()
        tasks[0].cancel()
        with pytest.raises(asyncio.CancelledError):
            await tasks[0]
        assert len(calls) == 1
    finally:
        release.set()
    results = await asyncio.gather(*tasks[1:])
    assert all("sample" in index for _, index in results)
    assert len(calls) == 1
    await source.load_library_bundle_index_async()
    assert len(calls) == 2  # Later reads check freshness, not a permanent TTL.


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "method",
    [
        "backfill_view_entry_type_constraints_from_manifest",
        "write_view_entry_type_constraints_from_manifest",
    ],
)
async def test_many_views_read_fallback_once_off_loop_and_write_in_owner_task(
    monkeypatch, method
):
    from app.services import operational_model_runtime as runtime

    owner_thread = threading.get_ident()
    owner_task = asyncio.current_task()
    reads = []
    writes = []
    views = []
    for n in range(40):

        async def save():
            assert asyncio.current_task() is owner_task
            writes.append(True)

        views.append(
            SimpleNamespace(
                config={"_manifest_view_key": f"view-{n}"},
                name="View",
                type="table",
                entry_type_keys=[],
                default_entry_type_key="",
                save=save,
            )
        )

    async def profile(track):
        return None, {}, None

    def seed_index():
        assert threading.get_ident() != owner_thread
        reads.append(True)
        return {
            runtime._slug(f"view-{n}"): {
                "entry_types": ["record"],
                "default_entry_type": "record",
            }
            for n in range(40)
        }

    monkeypatch.setattr(runtime, "resolve_track_runtime_profile", profile)
    monkeypatch.setattr(runtime, "warm_seeded_library_view_index", seed_index)
    await getattr(runtime, method)(SimpleNamespace(), views)
    assert reads == [True]
    assert len(writes) == 40
    assert all(v.entry_type_keys == ["record"] for v in views)


@pytest.mark.asyncio
async def test_extraction_joins_scheduled_work_and_waiter_cancel_does_not_restart(
    monkeypatch,
):
    from app.services.attachment_metadata.service import MetadataExtractionService

    service = MetadataExtractionService(extractors=())
    started, release = asyncio.Event(), asyncio.Event()
    calls = []
    result = SimpleNamespace(id="attachment")

    async def run(attachment_id):
        calls.append(attachment_id)
        started.set()
        await release.wait()
        return result

    monkeypatch.setattr(service, "_run_extraction", run)
    service.schedule_extraction("attachment")
    await started.wait()
    waiter = asyncio.create_task(service.extract_now("attachment"))
    survivor = asyncio.create_task(service.extract_now("attachment"))
    await asyncio.sleep(0)
    waiter.cancel()
    with pytest.raises(asyncio.CancelledError):
        await waiter
    assert calls == ["attachment"]
    release.set()
    assert await survivor is result
    await service.extract_now("attachment")  # Explicit later reprocess is supported.
    assert calls == ["attachment", "attachment"]


@pytest.mark.asyncio
async def test_extraction_limits_simultaneous_blobs_without_moving_graph_task(
    monkeypatch,
):
    from app.services.attachment_metadata.service import MetadataExtractionService

    service = MetadataExtractionService(extractors=())
    release = asyncio.Event()
    owners = {}
    active = 0
    peak = 0

    async def run(attachment_id):
        nonlocal active, peak
        assert asyncio.current_task() is owners[attachment_id]
        active += 1
        peak = max(peak, active)
        await release.wait()
        active -= 1
        return SimpleNamespace(id=attachment_id)

    async def request(attachment_id):
        owners[attachment_id] = asyncio.current_task()
        return await service.extract_now(attachment_id)

    monkeypatch.setattr(service, "_run_extraction", run)
    tasks = [asyncio.create_task(request(str(n))) for n in range(8)]
    await asyncio.sleep(0)
    assert peak == 2
    release.set()
    await asyncio.gather(*tasks)
    assert peak == 2


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "name",
    [
        "TextExtractor",
        "PdfExtractor",
        "DocxExtractor",
        "XlsxExtractor",
        "PptxExtractor",
        "ImageExtractor",
        "AudioExtractor",
    ],
)
async def test_file_parsers_execute_off_loop(monkeypatch, name):
    from app.services.attachment_metadata import extractors
    from app.services.attachment_metadata.base import MetadataResult

    owner = threading.get_ident()
    extractor = getattr(extractors, name)()

    def parse(**kwargs):
        assert threading.get_ident() != owner
        return MetadataResult(extracted_text="Readable document")

    monkeypatch.setattr(extractor, "extract_file", parse)
    result = await extractor.extract(path="test", mime="test", filename="test", size=1)
    assert result.extracted_text == "Readable document"


def test_request_handlers_do_not_call_known_blocking_source_builders_directly():
    """Prevent the same defect from reappearing under a different endpoint."""
    forbidden = {
        "load_library_operational_models",
        "load_library_operational_models_with_issues",
        "load_library_operational_models_with_issues_cached",
        "load_library_bundle_index",
        "compile_canonical_manifest",
        "build_tool_catalogue",
        "list_core_skills",
        "build_core_capability_snapshot",
        "materialize_standard_skill_library",
        "render_output",
        "resolve_signature_places",
        "overlay_signatures",
        "overlay_signature_png",
    }
    violations = []

    class Check(ast.NodeVisitor):
        active = False

        def visit_FunctionDef(self, node):
            old, self.active = self.active, False
            self.generic_visit(node)
            self.active = old

        def visit_AsyncFunctionDef(self, node):
            old, self.active = self.active, True
            self.generic_visit(node)
            self.active = old

        def visit_Call(self, node):
            if (
                self.active
                and isinstance(node.func, ast.Name)
                and node.func.id in forbidden
            ):
                violations.append(
                    f"{path.relative_to(root)}:{node.lineno}: {node.func.id}"
                )
            self.generic_visit(node)

    root = Path(__file__).parents[1] / "app"
    for path in root.rglob("*.py"):
        Check().visit(ast.parse(path.read_text()))
    assert not violations, "Blocking work in async request paths: " + ", ".join(
        violations
    )


def test_compiled_manifest_cache_cannot_be_modified_by_a_caller():
    from app.services.operational_model_compile import (
        compile_canonical_manifest,
        invalidate_manifest_cache,
    )

    invalidate_manifest_cache()
    manifest = {
        "operational_model_schema_version": 2,
        "scope": "track",
        "track": {"entry_types": []},
    }
    first = compile_canonical_manifest(manifest=manifest)
    first["track"]["entry_types"].append({"key": "injected"})
    second = compile_canonical_manifest(manifest=manifest)
    assert second["track"]["entry_types"] == []
    second["track"]["entry_types"].append({"key": "another"})
    assert compile_canonical_manifest(manifest=manifest)["track"]["entry_types"] == []


@pytest.mark.asyncio
async def test_oversized_metadata_skips_without_reading_the_blob(monkeypatch):
    from unittest.mock import AsyncMock

    from app.config import settings
    from app.services.attachment_metadata import service as module

    node = SimpleNamespace(
        source_type="file",
        storage_key="original",
        size=settings.ATTACHMENT_METADATA_MAX_BYTES + 1,
        mime_type="text/plain",
        metadata_status="",
        metadata_error="",
        save=AsyncMock(),
    )
    monkeypatch.setattr(module.Attachment, "get", AsyncMock(return_value=node))

    def no_storage():
        raise AssertionError("Oversized metadata must not allocate the blob")

    monkeypatch.setattr(module, "get_attachment_storage_service", no_storage)
    result = await module.MetadataExtractionService().extract_now("attachment")
    assert result.metadata_status == "skipped"
    assert "original remains available" in result.metadata_error


@pytest.mark.asyncio
async def test_text_extractor_reads_only_its_projection_budget(tmp_path, monkeypatch):
    from app.config import settings
    from app.services.attachment_metadata import extractors

    monkeypatch.setattr(settings, "ATTACHMENT_EXTRACTED_TEXT_MAX_BYTES", 64)
    monkeypatch.setattr(extractors, "_safe_import", lambda name: None)
    path = tmp_path / "large.txt"
    path.write_text("Searchable words. " * 1000)
    result = await extractors.TextExtractor().extract(
        path=str(path),
        mime="text/plain",
        filename="large.txt",
        size=path.stat().st_size,
    )
    assert result.partial
    assert len(result.extracted_text.encode()) <= 64


@pytest.mark.asyncio
async def test_failed_extraction_does_not_leave_a_processing_indicator(monkeypatch):
    from unittest.mock import AsyncMock

    from app.services.attachment_metadata import service as module

    owner = asyncio.current_task()

    async def save():
        assert asyncio.current_task() is owner

    node = SimpleNamespace(metadata_status="processing", metadata_error="", save=save)
    monkeypatch.setattr(module.Attachment, "get", AsyncMock(return_value=node))
    service = module.MetadataExtractionService(extractors=())

    async def fail(attachment_id):
        raise RuntimeError("Storage unavailable")

    monkeypatch.setattr(service, "_run_extraction", fail)
    with pytest.raises(RuntimeError, match="Storage unavailable"):
        await service.extract_now("attachment")
    assert node.metadata_status == "failed"
    assert "Reprocess" in node.metadata_error
    assert "attachment" not in service._in_flight
