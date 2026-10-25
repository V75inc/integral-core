"""Canonical, bounded file evidence for the ordinary Entry passage index.

The HAS_ATTACHMENT edge is authority, not Entry.attachment_ids. No bytes, URLs,
EXIF location data or arbitrary metadata dictionaries enter the index. Parsing
and model analysis happen separately; search never downloads or analyzes files.
"""

from __future__ import annotations

from typing import Any

from app.models.edges import HAS_ATTACHMENT
from app.models.nodes import Attachment
from app.services.attachment_metadata.observations import image_description_text

MAX_ATTACHMENT_TEXT_BYTES = 16 * 1024 * 1024


class AttachmentIndexCapacityError(ValueError):
    """A source exceeds the supported bounded projection; never silently truncate."""


async def _file_pages(entry):
    """Bound hydrated file rows too: each may contain a large native text field."""
    cursor, count = None, 0
    while True:
        page, cursor = await entry.nodes_page(
            edge=[HAS_ATTACHMENT],
            node=["Attachment"],
            direction="out",
            cursor=cursor,
            limit=2,
        )
        for attachment in page:
            count += 1
            if count > 64:
                raise AttachmentIndexCapacityError(
                    "Entry indexing supports at most 64 files"
                )
            yield attachment
        if cursor is None:
            return


def searchable_metadata(attachment: Attachment) -> str:
    """Project a small allowlist of descriptive, type-appropriate facts."""
    metadata = attachment.metadata or {}
    parts = [
        f"File: {attachment.filename[:512]}",
        f"Type: {attachment.mime_type}",
    ]
    for label, value in (
        ("Pages", attachment.page_count),
        ("Width", attachment.width),
        ("Height", attachment.height),
    ):
        if value is not None:
            parts.append(f"{label}: {value}")
    for section in ("common", "type_specific"):
        values = metadata.get(section)
        if not isinstance(values, dict):
            continue
        for key in (
            "title",
            "subject",
            "author",
            "keywords",
            "headings",
            "sheet_names",
            "slide_titles",
            "description",
            "detected_language",
            "duration_seconds",
            "artist",
            "album",
            "genre",
        ):
            candidate = values.get(key)
            if isinstance(candidate, list):
                candidate = "; ".join(
                    item[:256] for item in candidate[:32] if isinstance(item, str)
                )
            if isinstance(candidate, (str, int, float)) and not isinstance(
                candidate, bool
            ):
                parts.append(f"{key.replace('_', ' ')}: {str(candidate)[:2048]}")
    return "\n".join(parts)


async def attachment_sources(entry: Any, selection: str) -> tuple[dict, dict]:
    """Return current text and evidence descriptors under one canonical Entry.

    Refuse unsupported fanout instead of silently claiming complete indexing.
    A blocked file, avatar, unfiled chat upload or ambiguous Entry parent cannot
    contribute content. Current edge/scan/content checks run again on retrieval.
    """
    if selection == "none":
        return {}, {}
    texts, sources = {}, {}
    total_bytes = 0

    def add(path, text, source):
        nonlocal total_bytes
        total_bytes += len(text.encode("utf-8"))
        if total_bytes > MAX_ATTACHMENT_TEXT_BYTES:
            raise AttachmentIndexCapacityError(
                "Entry attachment text exceeds the index budget"
            )
        texts[path], sources[path] = text, source

    async for attachment in _file_pages(entry):
        if (
            not isinstance(attachment, Attachment)
            or attachment.owner_kind != "entry"
            or attachment.scan_status == "blocked"
            or attachment.source_type not in {"file", "url"}
        ):
            continue
        parents = await attachment.nodes(
            edge=[HAS_ATTACHMENT], node=["Entry"], direction="in", limit=2
        )
        if len(parents) != 1 or parents[0].id != entry.id:
            continue
        evidence = {
            "attachment_id": attachment.id,
            "filename": attachment.filename[:512],
            "mime_type": attachment.mime_type,
            "content_hash": attachment.content_hash,
            "extraction_status": attachment.metadata_status,
            "extractor_version": attachment.metadata_extractor_version,
        }
        prefix = f"attachments.{attachment.id}"
        add(
            prefix + ".metadata",
            searchable_metadata(attachment),
            {**evidence, "origin": "file_metadata"},
        )
        if selection == "content" and attachment.metadata_status in {
            "complete",
            "partial",
        }:
            # Image content comes exclusively from the configured multimodal
            # model. Do not reuse legacy image text from a different extractor.
            text = (
                ""
                if attachment.mime_type.startswith("image/")
                else attachment.extracted_text or ""
            )
            if text.strip():
                typed = (attachment.metadata or {}).get("type_specific", {})
                locations = (
                    typed.get("text_locations", []) if isinstance(typed, dict) else []
                )
                add(
                    prefix + ".text",
                    text,
                    {
                        **evidence,
                        "origin": "extracted_text",
                        "locations": (
                            locations[:200] if isinstance(locations, list) else []
                        ),
                    },
                )
            text = image_description_text(attachment)
            if text:
                add(
                    prefix + ".description",
                    text,
                    {
                        **evidence,
                        "origin": "generated_description",
                    },
                )
    return texts, sources


def passage_source(document: dict, field: str, start: int, end: int) -> dict:
    """Describe a current matched file passage with its original section bounds."""
    source = document.get("passage_sources", {}).get(field, {})
    if not source:
        return {}
    result = {
        key: source[key] for key in ("attachment_id", "filename", "extraction_status")
    }
    result["content_origin"] = source["origin"]
    locations = [
        location
        for location in source.get("locations", [])
        if isinstance(location, dict)
        and isinstance(location.get("start"), int)
        and isinstance(location.get("end"), int)
        and location["start"] < end
        and start < location["end"]
    ]
    locator = {}
    for kind in ("page", "slide"):
        values = [
            row[kind]
            for row in locations
            if type(row.get(kind)) is int and row[kind] > 0
        ]
        if values:
            locator.update({kind + "_start": min(values), kind + "_end": max(values)})
    sheets = {
        row["sheet"][:128] for row in locations if isinstance(row.get("sheet"), str)
    }
    if len(sheets) == 1:
        locator["sheet"] = sheets.pop()
    if locator:
        result["source_location"] = locator
    return result
