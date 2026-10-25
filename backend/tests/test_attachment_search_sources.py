"""Attachment evidence retains canonical scope, freshness and file provenance."""

import asyncio
from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from tokenizers import Tokenizer, models, pre_tokenizers

from app.models.edges import CONTAINS, HAS_ATTACHMENT, HAS_OPERATIONAL_MODEL
from app.models.nodes import Attachment, EntryType, OperationalModel
from app.services.retrieval.attachment_sources import (
    AttachmentIndexCapacityError,
    attachment_sources,
    searchable_metadata,
)
from app.services.retrieval.index_definition import (
    digest,
    embedding_profile,
    split_passages,
)
from app.services.retrieval.indexing import index_document
from tests.test_resource_location import graph

pytestmark = [pytest.mark.smoke, pytest.mark.asyncio]


async def file(entry, **values):
    attachment = await Attachment.create(
        filename="guide.txt",
        mime_type="text/plain",
        scan_status="clean",
        metadata_status="complete",
        extracted_text="Meet by the western gate.",
        content_hash="original",
        **values,
    )
    await entry.connect(attachment, edge=HAS_ATTACHMENT)
    return attachment


async def test_file_text_is_indexed_with_source_identity_without_scalar_cache():
    _, _, _, _, entry = await graph()
    attachment = await file(entry)
    assert not entry.attachment_ids
    document = await index_document(entry)
    path = f"attachments.{attachment.id}.text"
    assert document["texts"][path] == attachment.extracted_text
    assert document["passage_sources"][path]["attachment_id"] == attachment.id
    assert document["passage_sources"][path]["origin"] == "extracted_text"
    assert document["passage_sources"][path]["filename"] == "guide.txt"


async def test_file_adapter_preserves_existing_attachment_free_fingerprints():
    _, _, _, _, entry = await graph()
    document = await index_document(entry)
    legacy = {
        key: value
        for key, value in document.items()
        if key not in {"source_hash", "source_revision", "type_id", "passage_sources"}
    }
    legacy["definition"] = {
        key: value
        for key, value in document["definition"].items()
        if key != "attachments"
    }
    assert document["source_hash"] == digest(legacy)


async def test_scalar_attachment_id_cannot_replace_the_structural_edge():
    _, _, _, _, entry = await graph()
    attachment = await file(entry)
    await entry.disconnect(attachment, edge_type=HAS_ATTACHMENT)
    entry.attachment_ids = [attachment.id]
    await entry.save()
    texts, sources = await attachment_sources(entry, "content")
    assert texts == sources == {}


@pytest.mark.parametrize("selection", ["none", "metadata", "content"])
async def test_track_selection_controls_file_content(selection):
    _, _, _, track, entry = await graph()
    await file(entry)
    track.search_index = {"attachments": selection}
    await track.save()
    document = await index_document(entry)
    paths = [path for path in document["texts"] if path.startswith("attachments.")]
    if selection == "none":
        assert paths == []
    elif selection == "metadata":
        assert paths and all(path.endswith(".metadata") for path in paths)
    else:
        assert any(path.endswith(".text") for path in paths)


async def test_entry_type_can_narrow_but_not_widen_file_selection():
    _, _, _, track, entry = await graph()
    await file(entry)
    track.search_index = {"attachments": "metadata"}
    await track.save()
    model = await OperationalModel.create(name="Test")
    await track.connect(model, edge=HAS_OPERATIONAL_MODEL)
    entry_type = await EntryType.create(
        name="Test", form_schema={"search_index": {"attachments": "content"}}
    )
    await model.connect(entry_type, edge=CONTAINS)
    entry.type_id = entry_type.id
    await entry.save()
    document = await index_document(entry)
    assert document["definition"]["attachments"] == "metadata"
    assert not any(path.endswith(".text") for path in document["passage_sources"])


async def test_blocked_and_detached_files_change_hash_without_an_entry_edit():
    _, _, _, _, entry = await graph()
    attachment = await file(entry)
    before = await index_document(entry)
    revision = entry.record_revision
    attachment.scan_status = "blocked"
    await attachment.save()
    after = await index_document(entry)
    assert before["source_hash"] != after["source_hash"]
    assert after["passage_sources"] == {}
    assert entry.record_revision == revision
    attachment.scan_status = "clean"
    await attachment.save()
    await entry.disconnect(attachment, edge_type=HAS_ATTACHMENT)
    assert (await index_document(entry))["source_hash"] == after["source_hash"]


async def test_text_edits_and_reprocessed_metadata_invalidate_old_passages():
    _, _, _, _, entry = await graph()
    attachment = await file(entry)
    before = await index_document(entry)
    attachment.extracted_text = "Meet by the eastern courtyard."
    await attachment.save()
    assert before["source_hash"] != (await index_document(entry))["source_hash"]


async def test_generated_observations_are_labeled_and_bound_to_file_hash():
    _, _, _, _, entry = await graph()
    attachment = await file(entry)
    attachment.mime_type = "image/png"
    attachment.metadata = {
        "semantic": {
            "source_hash": attachment.content_hash,
            "description": "An illustrated character wearing blue bows.",
            "visible_text": "ARIANA RUN",
            "keywords": ["character", "blue bows"],
        }
    }
    await attachment.save()
    document = await index_document(entry)
    path = f"attachments.{attachment.id}.description"
    assert "Generated image description" in document["texts"][path]
    assert "Model-read visible text: ARIANA RUN" in document["texts"][path]
    assert f"attachments.{attachment.id}.text" not in document["texts"]
    assert attachment.extracted_text not in document["texts"][path]
    assert document["passage_sources"][path]["origin"] == "generated_description"
    attachment.content_hash = "replacement"
    await attachment.save()
    assert path not in (await index_document(entry))["texts"]


async def test_image_without_multimodal_analysis_indexes_only_file_properties():
    _, _, _, _, entry = await graph()
    attachment = await file(entry)
    attachment.mime_type = "image/png"
    await attachment.save()
    document = await index_document(entry)
    assert list(document["passage_sources"]) == [
        f"attachments.{attachment.id}.metadata"
    ]
    assert attachment.extracted_text not in "\n".join(document["texts"].values())


async def test_direct_agent_image_reads_use_the_same_source_bound_observations():
    from app.services.attachment_agent import (
        get_attachment_text,
        list_attachments_for_entry,
    )

    owner, _, _, _, entry = await graph()
    attachment = await file(entry)
    attachment.mime_type = "image/png"
    attachment.metadata = {
        "semantic": {
            "source_hash": attachment.content_hash,
            "description": "An illustrated character with blue bows.",
            "visible_text": "ARIANA RUN",
        }
    }
    await attachment.save()
    result = await get_attachment_text(owner.id, attachment.id)
    assert "ARIANA RUN" in result["text"]
    assert "Generated image description" in result["text"]
    assert result["content_origin"] == "generated_description"
    assert result["content_untrusted"]
    summary = await list_attachments_for_entry(owner.id, entry.id)
    assert summary["attachments"][0]["has_text"]
    assert "semantic" not in summary["attachments"][0]["metadata"]
    attachment.content_hash = "replacement"
    await attachment.save()
    result = await get_attachment_text(owner.id, attachment.id)
    assert not result["text"] and "semantic" not in result["metadata"]


async def test_failed_extraction_does_not_release_old_text():
    _, _, _, _, entry = await graph()
    attachment = await file(entry)
    attachment.metadata_status = "failed"
    await attachment.save()
    document = await index_document(entry)
    assert all(path.endswith(".metadata") for path in document["passage_sources"])


async def test_file_metadata_omits_urls_location_and_arbitrary_properties():
    attachment = Attachment(
        filename="guide.pdf",
        mime_type="application/pdf",
        metadata={
            "common": {
                "title": "Safety guide",
                "url": "secret-url",
                "GPS": "private-place",
            },
            "type_specific": {
                "sheet_names": ["Overview"],
                "exif": {"GPSInfo": "private-place"},
                "arbitrary": "private-value",
            },
        },
    )
    text = searchable_metadata(attachment)
    assert "Safety guide" in text and "Overview" in text
    assert "secret" not in text and "private" not in text


async def test_distinct_file_citations_survive_equal_text():
    instance = Tokenizer(models.WordLevel({"[UNK]": 0}, unk_token="[UNK]"))
    instance.pre_tokenizer = pre_tokenizers.Whitespace()
    text = "same document words"
    passages = split_passages(
        {"body": text, "attachments.one.text": text, "attachments.two.text": text},
        replace(embedding_profile(), chunk_tokens=32),
        instance,
    )
    assert {passage["field"] for passage in passages} == {
        "body",
        "attachments.one.text",
        "attachments.two.text",
    }


async def test_file_rows_are_paged_without_losing_citations(monkeypatch):
    from app.models.nodes import Entry

    _, _, _, _, entry = await graph()
    files = [await file(entry) for _ in range(5)]
    original = Entry.nodes_page
    calls = []

    async def page(self, **kwargs):
        calls.append(kwargs["limit"])
        return await original(self, **kwargs)

    monkeypatch.setattr(Entry, "nodes_page", page)
    _, sources = await attachment_sources(entry, "content")
    assert {row["attachment_id"] for row in sources.values()} == {
        attachment.id for attachment in files
    }
    assert calls == [2, 2, 2]


async def test_oversized_projection_is_not_silently_truncated(monkeypatch):
    from app.services.retrieval import attachment_sources as module

    _, _, _, _, entry = await graph()
    attachment = await file(entry)
    metadata_bytes = len(searchable_metadata(attachment).encode("utf-8"))
    monkeypatch.setattr(module, "MAX_ATTACHMENT_TEXT_BYTES", metadata_bytes)
    with pytest.raises(AttachmentIndexCapacityError):
        await attachment_sources(entry, "content")
    texts, _ = await attachment_sources(entry, "metadata")
    assert list(texts) == [f"attachments.{attachment.id}.metadata"]


@pytest.mark.parametrize("late_change", ["delete", "replace", "block"])
async def test_late_native_parser_cannot_resurrect_or_overwrite_a_changed_file(
    monkeypatch, late_change
):
    from app.services.attachment_metadata.base import (
        AttachmentMetadataExtractor,
        MetadataResult,
    )
    from app.services.attachment_metadata.service import MetadataExtractionService

    _, _, _, _, entry = await graph()
    attachment = await file(entry, storage_key="original-key")
    started, release = asyncio.Event(), asyncio.Event()

    class SlowParser(AttachmentMetadataExtractor):
        mime_prefixes = ("text/",)

        async def extract(self, **kwargs):
            started.set()
            await release.wait()
            return MetadataResult(extracted_text="Late result must not be published.")

    monkeypatch.setattr(
        "app.services.attachment_metadata.service.get_attachment_storage_service",
        lambda: SimpleNamespace(read_attachment=AsyncMock(return_value=b"file")),
    )
    task = asyncio.create_task(
        MetadataExtractionService((SlowParser(),)).extract_now(attachment.id)
    )
    await started.wait()
    if late_change == "delete":
        await attachment.delete()
    else:
        current = await Attachment.get(attachment.id)
        if late_change == "replace":
            current.content_hash, current.storage_key = "replacement", "new-key"
            current.metadata_status = "pending"
        else:
            current.scan_status = "blocked"
        current.extracted_text = ""
        await current.save()
    release.set()
    with pytest.raises(ValueError):
        await task
    current = await Attachment.find_one({"id": attachment.id})
    if late_change == "delete":
        assert current is None
    else:
        assert not current.extracted_text
        assert current.content_hash == (
            "replacement" if late_change == "replace" else "original"
        )


async def test_native_image_properties_do_not_decode_oversized_images(monkeypatch):
    from app.services.attachment_metadata import extractors

    class LargeImage:
        size, format, mode = (5000, 5000), "PNG", "RGB"

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return None

        def getexif(self):
            raise AssertionError("Do not decode image pixels for oversized properties")

    monkeypatch.setattr(
        extractors,
        "_safe_import",
        lambda name: SimpleNamespace(open=lambda path: LargeImage()),
    )
    result = extractors.ImageExtractor().extract_file(
        path="fixture", mime="image/png", filename="large.png", size=100
    )
    assert result.width == result.height == 5000 and result.partial
    assert not result.extracted_text
