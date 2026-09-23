"""Tests for Plan 03 — Phase 4 (OperationalModel file/files field types).

Covers the backend pieces:
    - VALID_FIELD_TYPES accepts 'file' / 'files'.
    - _normalize_field_spec normalises config.accept / config.max_count /
      config.expose_metadata into the canonical shape.
    - validate_and_materialize_entry_custom_fields validates references
      and enforces max_count / accept MIME / per-entry binding.
    - resolve_derived_fields_for_entry projects attachment metadata
      into derived fields per the manifest mapping.
"""

from __future__ import annotations

from datetime import datetime

import pytest

from app.models.nodes import Attachment, Entry, EntryType, Track
from app.services.operational_model_compile import (
    DEFAULT_FILES_MAX_COUNT,
    VALID_FIELD_TYPES,
    _normalize_field_spec,
)
from app.services.operational_model_derived_fields import (
    resolve_derived_fields_for_entry,
)
from app.services.operational_model_entry_fields import (
    _mime_accepts,
    validate_and_materialize_entry_custom_fields,
)

# ---------------------------------------------------------------------------
# Manifest validation
# ---------------------------------------------------------------------------


def test_valid_field_types_includes_file_and_files():
    assert "file" in VALID_FIELD_TYPES
    assert "files" in VALID_FIELD_TYPES


def test_normalize_field_spec_file_default_config():
    spec = _normalize_field_spec({"key": "contract", "type": "file"})
    cfg = spec["config"]
    assert cfg["accept"] == []
    assert cfg["max_count"] == 1
    assert cfg["expose_metadata"] == []


def test_normalize_field_spec_files_default_max_count():
    spec = _normalize_field_spec({"key": "supporting", "type": "files"})
    assert spec["config"]["max_count"] == DEFAULT_FILES_MAX_COUNT


def test_normalize_field_spec_file_normalizes_accept_to_lowercase():
    spec = _normalize_field_spec(
        {
            "key": "deck",
            "type": "file",
            "config": {
                "accept": ["Application/PDF", "  IMAGE/PNG "],
            },
        }
    )
    assert spec["config"]["accept"] == ["application/pdf", "image/png"]


def test_normalize_field_spec_file_collapses_singular_max_count():
    spec = _normalize_field_spec(
        {
            "key": "deck",
            "type": "file",
            "config": {"max_count": 5},  # ignored for 'file'
        }
    )
    assert spec["config"]["max_count"] == 1


def test_normalize_field_spec_expose_metadata_normalises_rows():
    spec = _normalize_field_spec(
        {
            "key": "deck",
            "type": "file",
            "config": {
                "expose_metadata": [
                    {"from": "type_specific.author", "as": "contract_author"},
                    {"from": "common.page_count"},  # alias defaults to last segment
                    {"as": "ignored"},  # missing from -> dropped
                    "garbage",  # non-dict -> dropped
                ],
            },
        }
    )
    rows = spec["config"]["expose_metadata"]
    assert rows == [
        {"from": "type_specific.author", "as": "contract_author"},
        {"from": "common.page_count", "as": "page_count"},
    ]


# ---------------------------------------------------------------------------
# MIME accept-list helper
# ---------------------------------------------------------------------------


def test_mime_accepts_empty_allows_anything():
    assert _mime_accepts("application/zip", [])


def test_mime_accepts_exact():
    assert _mime_accepts("application/pdf", ["application/pdf"])
    assert not _mime_accepts("application/zip", ["application/pdf"])


def test_mime_accepts_family_wildcard():
    assert _mime_accepts("image/png", ["image/*"])
    assert _mime_accepts("image/jpeg", ["image/*"])
    assert not _mime_accepts("video/mp4", ["image/*"])


# ---------------------------------------------------------------------------
# Reference validation
# ---------------------------------------------------------------------------


async def _make_track_entry_type_with_file_field(
    *,
    field_key: str = "contract",
    field_type: str = "file",
    accept: list[str] | None = None,
    max_count: int | None = None,
    expose: list[dict] | None = None,
):
    """Stage a minimal Track + EntryType with a single file/files field
    so validation paths can be exercised without spinning up a full
    operational model."""
    track = await Track.create(
        title="T",
        title_fold="t",
        visibility="private",
        created_at=datetime.now().isoformat(),
    )
    cfg: dict = {}
    if accept is not None:
        cfg["accept"] = accept
    if max_count is not None:
        cfg["max_count"] = max_count
    if expose is not None:
        cfg["expose_metadata"] = expose
    entry_type = await EntryType.create(
        name="DocType",
        track_id=track.id,
        form_schema={
            "fields": [
                {"key": field_key, "name": field_key, "type": field_type, "config": cfg}
            ]
        },
        created_at=datetime.now().isoformat(),
    )
    return track, entry_type


@pytest.mark.asyncio
async def test_validate_file_field_accepts_known_attachment():
    track, entry_type = await _make_track_entry_type_with_file_field()
    entry = await Entry.create(
        title="e",
        track_id=track.id,
        type_id=entry_type.id,
        attachment_ids=[],
        created_at=datetime.now().isoformat(),
    )
    att = await Attachment.create(
        filename="x.pdf",
        mime_type="application/pdf",
        size=1,
        storage_key="k",
        uploaded_by="u",
        created_at=datetime.now().isoformat(),
    )
    entry.attachment_ids = [att.id]
    await entry.save()

    out, _ = await validate_and_materialize_entry_custom_fields(
        track=track,
        entry_type=entry_type,
        custom_fields={"contract": att.id},
        runtime_tier={},
        entry=entry,
    )
    assert out["contract"] == att.id


@pytest.mark.asyncio
async def test_validate_file_field_rejects_unknown_attachment():
    track, entry_type = await _make_track_entry_type_with_file_field()
    entry = await Entry.create(
        title="e",
        track_id=track.id,
        type_id=entry_type.id,
        attachment_ids=[],
        created_at=datetime.now().isoformat(),
    )
    from app.exceptions import BadRequestError

    with pytest.raises(BadRequestError):
        await validate_and_materialize_entry_custom_fields(
            track=track,
            entry_type=entry_type,
            custom_fields={"contract": "does-not-exist"},
            runtime_tier={},
            entry=entry,
        )


@pytest.mark.asyncio
async def test_validate_file_field_rejects_cross_entry_attachment():
    """Attachment exists but isn't bound to this entry — must reject."""
    track, entry_type = await _make_track_entry_type_with_file_field()
    entry = await Entry.create(
        title="e",
        track_id=track.id,
        type_id=entry_type.id,
        attachment_ids=[],
        created_at=datetime.now().isoformat(),
    )
    other_att = await Attachment.create(
        filename="o.pdf",
        mime_type="application/pdf",
        size=1,
        storage_key="k",
        uploaded_by="u",
        created_at=datetime.now().isoformat(),
    )

    from app.exceptions import BadRequestError

    with pytest.raises(BadRequestError):
        await validate_and_materialize_entry_custom_fields(
            track=track,
            entry_type=entry_type,
            custom_fields={"contract": other_att.id},
            runtime_tier={},
            entry=entry,
        )


@pytest.mark.asyncio
async def test_validate_file_field_rejects_mime_outside_accept_list():
    track, entry_type = await _make_track_entry_type_with_file_field(
        accept=["application/pdf"]
    )
    entry = await Entry.create(
        title="e",
        track_id=track.id,
        type_id=entry_type.id,
        attachment_ids=[],
        created_at=datetime.now().isoformat(),
    )
    att = await Attachment.create(
        filename="x.png",
        mime_type="image/png",
        size=1,
        storage_key="k",
        uploaded_by="u",
        created_at=datetime.now().isoformat(),
    )
    entry.attachment_ids = [att.id]
    await entry.save()

    from app.exceptions import BadRequestError

    with pytest.raises(BadRequestError):
        await validate_and_materialize_entry_custom_fields(
            track=track,
            entry_type=entry_type,
            custom_fields={"contract": att.id},
            runtime_tier={},
            entry=entry,
        )


@pytest.mark.asyncio
async def test_validate_files_field_enforces_max_count():
    track, entry_type = await _make_track_entry_type_with_file_field(
        field_type="files", max_count=2
    )
    entry = await Entry.create(
        title="e",
        track_id=track.id,
        type_id=entry_type.id,
        attachment_ids=[],
        created_at=datetime.now().isoformat(),
    )
    ids: list[str] = []
    for i in range(3):
        att = await Attachment.create(
            filename=f"{i}.pdf",
            mime_type="application/pdf",
            size=1,
            storage_key="k",
            uploaded_by="u",
            created_at=datetime.now().isoformat(),
        )
        ids.append(att.id)
    entry.attachment_ids = ids
    await entry.save()

    from app.exceptions import BadRequestError

    with pytest.raises(BadRequestError):
        await validate_and_materialize_entry_custom_fields(
            track=track,
            entry_type=entry_type,
            custom_fields={"contract": ids},
            runtime_tier={},
            entry=entry,
        )


# ---------------------------------------------------------------------------
# Derived fields resolution
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_resolve_derived_fields_projects_metadata_for_file_field():
    track, entry_type = await _make_track_entry_type_with_file_field(
        expose=[
            {"from": "type_specific.author", "as": "contract_author"},
            {"from": "common.page_count", "as": "contract_pages"},
        ]
    )
    att = await Attachment.create(
        filename="c.pdf",
        mime_type="application/pdf",
        size=1,
        storage_key="k",
        uploaded_by="u",
        metadata={
            "common": {"page_count": 42},
            "type_specific": {"author": "Jane Doe"},
        },
        created_at=datetime.now().isoformat(),
    )
    entry = await Entry.create(
        title="e",
        track_id=track.id,
        type_id=entry_type.id,
        attachment_ids=[att.id],
        custom_fields={"contract": att.id},
        created_at=datetime.now().isoformat(),
    )

    derived = await resolve_derived_fields_for_entry(entry, entry_type=entry_type)
    assert derived["contract_author"] == "Jane Doe"
    assert derived["contract_pages"] == 42


@pytest.mark.asyncio
async def test_resolve_derived_fields_handles_missing_metadata():
    track, entry_type = await _make_track_entry_type_with_file_field(
        expose=[{"from": "type_specific.author", "as": "contract_author"}]
    )
    att = await Attachment.create(
        filename="c.pdf",
        mime_type="application/pdf",
        size=1,
        storage_key="k",
        uploaded_by="u",
        metadata={},  # nothing extracted yet
        created_at=datetime.now().isoformat(),
    )
    entry = await Entry.create(
        title="e",
        track_id=track.id,
        type_id=entry_type.id,
        attachment_ids=[att.id],
        custom_fields={"contract": att.id},
        created_at=datetime.now().isoformat(),
    )

    derived = await resolve_derived_fields_for_entry(entry, entry_type=entry_type)
    # No keys when nothing can be projected — better than emitting nulls.
    assert derived == {}


@pytest.mark.asyncio
async def test_resolve_derived_fields_empty_when_no_file_fields():
    """Entry types without file fields should return an empty dict."""
    track = await Track.create(
        title="T",
        title_fold="t",
        visibility="private",
        created_at=datetime.now().isoformat(),
    )
    entry_type = await EntryType.create(
        name="Plain",
        track_id=track.id,
        form_schema={"fields": [{"key": "title", "type": "text"}]},
        created_at=datetime.now().isoformat(),
    )
    entry = await Entry.create(
        title="e",
        track_id=track.id,
        type_id=entry_type.id,
        attachment_ids=[],
        created_at=datetime.now().isoformat(),
    )
    assert await resolve_derived_fields_for_entry(entry, entry_type=entry_type) == {}
