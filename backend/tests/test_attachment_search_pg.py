"""Actual encoder and pgvector evidence for file discovery and fresh citations."""

import pytest

from app.models.edges import HAS_ATTACHMENT
from app.models.nodes import Attachment
from app.services.retrieval import indexing
from tests.test_resource_location import graph
from tests.test_semantic_indexing_pg import search, vector_driver

pytestmark = [pytest.mark.integration, pytest.mark.slow, pytest.mark.asyncio]


async def test_coverage_includes_current_attachment_preparation(vector_driver):
    from app.services.retrieval.index_management import index_status

    user, workspace, app, track, entry = await graph()
    image = await Attachment.create(
        filename="diagram.png",
        mime_type="image/png",
        scan_status="clean",
        metadata_status="complete",
        content_hash="image-v1",
    )
    await entry.connect(image, edge=HAS_ATTACHMENT)
    await indexing.enqueue_entry(entry)
    assert (await indexing.deliver_index_jobs())["indexed"] == 1
    status = await index_status(user.id, track.id, workspace.id)
    assert not status["coverage_complete"]
    assert status["attachment_coverage"]["image_description_unavailable"] == 1

    image.metadata = {
        "semantic": {"source_hash": "image-v1", "description": "A blue diagram"}
    }
    await image.save()
    await indexing.enqueue_entry(entry)
    assert (await indexing.deliver_index_jobs())["indexed"] == 1
    status = await index_status(user.id, track.id, workspace.id)
    assert status["coverage_complete"]
    assert status["attachment_coverage"]["complete"] == 1

    image.metadata_status = "failed"
    await image.save()
    await indexing.enqueue_entry(entry)
    assert (await indexing.deliver_index_jobs())["indexed"] == 1
    status = await index_status(user.id, track.id, workspace.id)
    assert not status["coverage_complete"]
    assert status["attachment_coverage"]["failed"] == 1


async def test_attachment_content_filename_and_visual_descriptions(vector_driver):
    user, workspace, app, track, entry = await graph()
    entry.title, entry.body = "Daily equipment note", "A routine operational update."
    await entry.save()
    attachment = await Attachment.create(
        filename="concept-preview.png",
        mime_type="image/png",
        scan_status="clean",
        metadata_status="complete",
        content_hash="known-file",
        extracted_text="ARIANA RUN — AVATAR CONCEPT V2",
        metadata={
            "semantic": {
                "source_hash": "known-file",
                "description": "An illustrated game character with blue hair bows and a plaid school uniform, shown in front, rear and running poses.",
                "visible_text": "Ariana Run",
                "keywords": ["blue bows", "character concept"],
            }
        },
    )
    await entry.connect(attachment, edge=HAS_ATTACHMENT)
    await indexing.enqueue_entry(entry)
    assert (await indexing.deliver_index_jobs())["indexed"] == 1
    for query, options in [
        ("Ariana Run avatar", {"attachment_kind": "image"}),
        ("character with blue hair bows", {"attachment_kind": "image"}),
        ("", {"attachment_kind": "any"}),
        ("", {"attachment_filename": "CONCEPT-preview"}),
    ]:
        found, *_ = await search(
            user,
            workspace,
            app,
            vector_driver,
            query=query,
            mode="graph" if not query else "hybrid",
            context_tokens=128,
            **options,
        )
        assert found and found[0].entry_id == entry.id
        assert any(
            p.attachment_id == attachment.id and p.filename == attachment.filename
            for p in found[0].passages
        )
        assert all(p.field.startswith("attachments.") for p in found[0].passages)
        assert sum(p.tokens for result in found for p in result.passages) <= 128
    assert not (
        await search(
            user,
            workspace,
            app,
            vector_driver,
            query="",
            mode="graph",
            attachment_kind="document",
        )
    )[0]
    assert not (
        await search(
            user,
            workspace,
            app,
            vector_driver,
            query="",
            mode="graph",
            attachment_filename="unrelated.pdf",
        )
    )[0]
    # Canonical removal withholds stale indexed evidence before a worker runs.
    await entry.disconnect(attachment, edge_type=HAS_ATTACHMENT)
    assert not (
        await search(
            user,
            workspace,
            app,
            vector_driver,
            query="Ariana Run",
            attachment_kind="any",
        )
    )[0]


async def test_file_passages_recheck_parent_permissions_and_selected_content(
    vector_driver,
):
    from app.models.edges import COLLABORATES_ON, EXCLUDED_FROM, IS_MEMBER_OF
    from app.models.nodes import User
    from app.services.app_graph import catalog_user

    owner, workspace, app, track, entry = await graph()
    reader = await User.create(display_name="File reader")
    await catalog_user(reader)
    await reader.connect(workspace, edge=IS_MEMBER_OF, role="member")
    await reader.connect(track, edge=COLLABORATES_ON, role="viewer")
    attachment = await Attachment.create(
        filename="safety.pdf",
        mime_type="application/pdf",
        scan_status="clean",
        metadata_status="partial",
        extracted_text="The emergency assembly is beside the western gate.",
        metadata={
            "type_specific": {"text_locations": [{"start": 0, "end": 55, "page": 3}]}
        },
    )
    await entry.connect(attachment, edge=HAS_ATTACHMENT)
    await indexing.enqueue_entry(entry)
    await indexing.deliver_index_jobs()
    found, *_ = await search(
        reader,
        workspace,
        app,
        vector_driver,
        query="assembly point",
        attachment_kind="document",
    )
    assert found
    passage = next(p for p in found[0].passages if p.content_origin == "extracted_text")
    assert passage.source_location.page_start == 3
    assert passage.extraction_status == "partial"
    await reader.connect(entry, edge=EXCLUDED_FROM)
    assert not (
        await search(
            reader,
            workspace,
            app,
            vector_driver,
            query="assembly point",
            attachment_kind="document",
        )
    )[0]
    track.search_index = {"attachments": "none"}
    await track.save()
    assert not (
        await search(
            owner,
            workspace,
            app,
            vector_driver,
            query="assembly point",
            attachment_kind="document",
        )
    )[0]
