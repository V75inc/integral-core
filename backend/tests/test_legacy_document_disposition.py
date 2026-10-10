"""Unsupported legacy document code is absent; supported rendering stays usable."""

import importlib.util

import pytest

pytestmark = pytest.mark.smoke


@pytest.mark.parametrize(
    "module", ["generate", "template_lifecycle", "document_type_lifecycle"]
)
def test_retired_document_modules_are_unreachable(module):
    assert importlib.util.find_spec(f"app.services.documents.{module}") is None


def test_supported_document_renderer_and_record_schema_remain_available():
    from app.models.nodes import GeneratedDocument
    from app.services.document_render import render_document
    from app.services.documents import list_document_fields

    # Preserve the discriminator and stored snapshot shape for backup/recovery.
    record = GeneratedDocument(
        workspace_id="legacy-workspace",
        resolved_snapshot={"saved": "original"},
        checksum="original",
    )
    assert record._entity_name() == "GeneratedDocument"
    assert record.resolved_snapshot == {"saved": "original"}
    assert callable(list_document_fields)
    rendered = render_document(
        title="Supported",
        body="Current renderer",
        renderer="markdown",
        sections=[],
        rows=[],
    )
    assert b"Current renderer" in rendered
