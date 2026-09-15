"""Concrete per-MIME extractors.

Each extractor is **soft-imported**: when its third-party library isn't
installed in the running environment the extractor logs a debug message
and yields an empty ``MetadataResult`` (``partial=True``). This keeps
the pipeline functional in minimal CI containers while still being
useful in production where the libraries are present.

Adding a new type:

    1. Subclass ``AttachmentMetadataExtractor``.
    2. Set ``mime_prefixes`` / ``exact_mimes`` and a priority.
    3. Append the class to ``BUILTIN_EXTRACTORS`` at the bottom.

No central registration step — the dispatcher iterates
``BUILTIN_EXTRACTORS`` and asks each whether it supports the MIME.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, Optional

from app.services.attachment_metadata.base import (
    AttachmentMetadataExtractor,
    MetadataResult,
)

logger = logging.getLogger(__name__)


def _safe_import(module: str):
    try:
        return __import__(module, fromlist=["*"])
    except Exception as e:  # noqa: BLE001
        logger.debug("metadata extractor: %s unavailable (%s)", module, e)
        return None


# ---------------------------------------------------------------------------
# Plain text / markdown / json / csv
# ---------------------------------------------------------------------------


class TextExtractor(AttachmentMetadataExtractor):
    name = "text"
    mime_prefixes = ("text/",)
    exact_mimes = frozenset({"application/json"})
    priority = 5

    async def extract(
        self, *, path: str, mime: str, filename: str, size: int
    ) -> MetadataResult:
        try:
            with open(path, "rb") as f:
                raw = f.read()
        except OSError as e:
            return MetadataResult(error=f"read failed: {e}")
        # Decode with replacement so weird encodings don't blow up.
        text = raw.decode("utf-8", errors="replace")
        # Truncate to extracted-text cap (the service does this too but
        # an early cut here keeps the per-line counters honest).
        from app.config import settings

        cap = settings.ATTACHMENT_EXTRACTED_TEXT_MAX_BYTES
        if len(text.encode("utf-8")) > cap:
            text = text.encode("utf-8")[:cap].decode("utf-8", errors="replace")

        lines = text.splitlines()
        words = sum(len(line.split()) for line in lines)
        type_specific: Dict[str, Any] = {
            "line_count": len(lines),
            "word_count": words,
            "char_count": len(text),
        }

        # Markdown heading outline for markdown files.
        if mime in ("text/markdown", "text/x-markdown") or filename.lower().endswith(
            (".md", ".markdown")
        ):
            headings = [
                line.lstrip("# ").strip() for line in lines if line.startswith("#")
            ][:32]
            type_specific["headings"] = headings

        # Cheap language detection — soft-imported.
        langdetect = _safe_import("langdetect")
        if langdetect is not None and text.strip():
            try:
                type_specific["detected_language"] = langdetect.detect(text[:4096])
            except Exception:  # noqa: BLE001
                pass

        return MetadataResult(
            common={},
            type_specific=type_specific,
            extracted_text=text,
        )


# ---------------------------------------------------------------------------
# PDF
# ---------------------------------------------------------------------------


class PdfExtractor(AttachmentMetadataExtractor):
    name = "pdf"
    exact_mimes = frozenset({"application/pdf"})
    priority = 20

    async def extract(
        self, *, path: str, mime: str, filename: str, size: int
    ) -> MetadataResult:
        # Prefer pdfplumber (better text extraction); fall back to pypdf
        # for metadata only when pdfplumber is missing.
        pdfplumber = _safe_import("pdfplumber")
        pypdf = _safe_import("pypdf")
        if pdfplumber is None and pypdf is None:
            return MetadataResult(
                partial=True,
                error="pdfplumber/pypdf unavailable",
            )

        type_specific: Dict[str, Any] = {}
        text_pieces: list[str] = []
        page_count: Optional[int] = None
        partial = False
        encrypted = False

        # --- Metadata via pypdf (lighter, more reliable for properties) ---
        if pypdf is not None:
            try:
                reader = pypdf.PdfReader(path)
                if reader.is_encrypted:
                    encrypted = True
                    # Try empty-password unlock for PDFs encrypted only
                    # to satisfy the spec (no DRM).
                    try:
                        reader.decrypt("")
                    except Exception:  # noqa: BLE001
                        partial = True
                page_count = len(reader.pages)
                info = reader.metadata or {}
                type_specific.update(
                    {
                        "title": _pdf_str(info.get("/Title")),
                        "author": _pdf_str(info.get("/Author")),
                        "subject": _pdf_str(info.get("/Subject")),
                        "keywords": _pdf_str(info.get("/Keywords")),
                        "creator": _pdf_str(info.get("/Creator")),
                        "producer": _pdf_str(info.get("/Producer")),
                        "encrypted": encrypted,
                    }
                )
            except Exception as e:  # noqa: BLE001
                logger.warning("pypdf failed on %r: %s", filename, e)
                partial = True

        # --- Text via pdfplumber ---
        if pdfplumber is not None:
            try:
                from app.config import settings

                cap = settings.ATTACHMENT_EXTRACTED_TEXT_MAX_BYTES
                running = 0
                with pdfplumber.open(path) as pdf:
                    if page_count is None:
                        page_count = len(pdf.pages)
                    for page in pdf.pages:
                        try:
                            chunk = page.extract_text() or ""
                        except Exception:  # noqa: BLE001
                            chunk = ""
                            partial = True
                        encoded = chunk.encode("utf-8")
                        if running + len(encoded) > cap:
                            text_pieces.append(
                                encoded[: cap - running].decode(
                                    "utf-8", errors="replace"
                                )
                            )
                            partial = True
                            break
                        running += len(encoded)
                        text_pieces.append(chunk)
            except Exception as e:  # noqa: BLE001
                logger.warning("pdfplumber failed on %r: %s", filename, e)
                partial = True

        return MetadataResult(
            common={"page_count": page_count} if page_count is not None else {},
            type_specific=type_specific,
            extracted_text="\n\n".join(p for p in text_pieces if p),
            page_count=page_count,
            partial=partial,
        )


def _pdf_str(value: object) -> Optional[str]:
    if value is None:
        return None
    try:
        s = str(value).strip()
        return s or None
    except Exception:  # noqa: BLE001
        return None


# ---------------------------------------------------------------------------
# DOCX
# ---------------------------------------------------------------------------


class DocxExtractor(AttachmentMetadataExtractor):
    name = "docx"
    exact_mimes = frozenset(
        {
            "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        }
    )
    priority = 20

    async def extract(
        self, *, path: str, mime: str, filename: str, size: int
    ) -> MetadataResult:
        docx = _safe_import("docx")
        if docx is None:
            return MetadataResult(partial=True, error="python-docx unavailable")

        try:
            doc = docx.Document(path)
        except Exception as e:  # noqa: BLE001
            return MetadataResult(partial=True, error=f"open failed: {e}")

        # Core properties (title/author/created/modified)
        cp = doc.core_properties
        type_specific: Dict[str, Any] = {
            "title": cp.title or None,
            "author": cp.author or None,
            "created": cp.created.isoformat() if cp.created else None,
            "modified": cp.modified.isoformat() if cp.modified else None,
            "subject": cp.subject or None,
            "keywords": cp.keywords or None,
            "category": cp.category or None,
            "comments": cp.comments or None,
            "revision": cp.revision,
        }

        # Heading outline
        headings: list[str] = []
        text_pieces: list[str] = []
        word_count = 0
        for para in doc.paragraphs:
            text = (para.text or "").strip()
            if not text:
                continue
            text_pieces.append(text)
            word_count += len(text.split())
            style = (para.style.name or "").lower() if para.style else ""
            if style.startswith("heading") and len(headings) < 64:
                headings.append(text)
        type_specific["headings"] = headings
        type_specific["word_count"] = word_count

        return MetadataResult(
            common={},
            type_specific=type_specific,
            extracted_text="\n".join(text_pieces),
        )


# ---------------------------------------------------------------------------
# XLSX
# ---------------------------------------------------------------------------


class XlsxExtractor(AttachmentMetadataExtractor):
    name = "xlsx"
    exact_mimes = frozenset(
        {
            "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        }
    )
    priority = 20

    async def extract(
        self, *, path: str, mime: str, filename: str, size: int
    ) -> MetadataResult:
        openpyxl = _safe_import("openpyxl")
        if openpyxl is None:
            return MetadataResult(partial=True, error="openpyxl unavailable")

        try:
            wb = openpyxl.load_workbook(path, read_only=True, data_only=True)
        except Exception as e:  # noqa: BLE001
            return MetadataResult(partial=True, error=f"open failed: {e}")

        sheets: list[Dict[str, Any]] = []
        text_pieces: list[str] = []
        formula_count = 0
        for name in wb.sheetnames:
            ws = wb[name]
            sheet_info = {
                "name": name,
                "rows": ws.max_row or 0,
                "cols": ws.max_column or 0,
            }
            sheets.append(sheet_info)
            # Sample a few rows of values for text indexing — full
            # sheet contents quickly blow past the text cap.
            for row in ws.iter_rows(max_row=200, values_only=True):
                for v in row:
                    if v is None:
                        continue
                    sv = str(v).strip()
                    if not sv:
                        continue
                    if sv.startswith("="):
                        formula_count += 1
                    text_pieces.append(sv)
        wb.close()

        type_specific: Dict[str, Any] = {
            "sheets": sheets,
            "sheet_count": len(sheets),
            "formula_count_sampled": formula_count,
        }

        return MetadataResult(
            common={},
            type_specific=type_specific,
            extracted_text="\n".join(text_pieces),
        )


# ---------------------------------------------------------------------------
# PPTX
# ---------------------------------------------------------------------------


class PptxExtractor(AttachmentMetadataExtractor):
    name = "pptx"
    exact_mimes = frozenset(
        {
            "application/vnd.openxmlformats-officedocument.presentationml.presentation",
        }
    )
    priority = 20

    async def extract(
        self, *, path: str, mime: str, filename: str, size: int
    ) -> MetadataResult:
        pptx = _safe_import("pptx")
        if pptx is None:
            return MetadataResult(partial=True, error="python-pptx unavailable")

        try:
            pres = pptx.Presentation(path)
        except Exception as e:  # noqa: BLE001
            return MetadataResult(partial=True, error=f"open failed: {e}")

        slide_titles: list[str] = []
        notes: list[str] = []
        text_pieces: list[str] = []
        for slide in pres.slides:
            title = ""
            for shape in slide.shapes:
                if shape.has_text_frame:
                    chunk = (shape.text_frame.text or "").strip()
                    if chunk:
                        if (
                            not title
                            and shape.placeholder_format
                            and (shape.placeholder_format.idx == 0)
                        ):
                            title = chunk.split("\n", 1)[0]
                        text_pieces.append(chunk)
            slide_titles.append(title)
            if slide.has_notes_slide:
                note_text = (slide.notes_slide.notes_text_frame.text or "").strip()
                if note_text:
                    notes.append(note_text)
                    text_pieces.append(note_text)

        type_specific: Dict[str, Any] = {
            "slide_count": len(pres.slides),
            "slide_titles": slide_titles,
            "speaker_notes": notes,
        }

        return MetadataResult(
            common={"page_count": len(pres.slides)},
            type_specific=type_specific,
            extracted_text="\n\n".join(text_pieces),
            page_count=len(pres.slides),
        )


# ---------------------------------------------------------------------------
# Images
# ---------------------------------------------------------------------------


class ImageExtractor(AttachmentMetadataExtractor):
    name = "image"
    mime_prefixes = ("image/",)
    priority = 10

    async def extract(
        self, *, path: str, mime: str, filename: str, size: int
    ) -> MetadataResult:
        pil_image = _safe_import("PIL.Image")
        if pil_image is None:
            return MetadataResult(partial=True, error="Pillow unavailable")
        try:
            with pil_image.open(path) as im:
                width, height = im.size
                format_ = im.format
                mode = im.mode
                exif = {}
                try:
                    raw_exif = im.getexif() or {}
                    if raw_exif:
                        # Map tag ids to readable names lazily so we don't
                        # require ExifTags up-front.
                        exif_tags = _safe_import("PIL.ExifTags")
                        tag_map = getattr(exif_tags, "TAGS", {}) if exif_tags else {}
                        for tag_id, value in raw_exif.items():
                            name = tag_map.get(tag_id, str(tag_id))
                            try:
                                if isinstance(value, bytes):
                                    value = value.decode("utf-8", errors="replace")
                                exif[name] = value
                            except Exception:  # noqa: BLE001
                                pass
                except Exception:  # noqa: BLE001
                    pass
        except Exception as e:  # noqa: BLE001
            return MetadataResult(partial=True, error=f"open failed: {e}")

        # Strip EXIF values that aren't JSON-serializable (e.g. tuples
        # for rational numbers come through fine, but IFD pointers don't).
        def _normalize(value: Any) -> Any:
            try:
                import json as _json

                _json.dumps(value)
                return value
            except Exception:  # noqa: BLE001
                return str(value)

        clean_exif = {k: _normalize(v) for k, v in exif.items()}

        return MetadataResult(
            common={"width": width, "height": height},
            type_specific={
                "format": format_,
                "mode": mode,
                "exif": clean_exif,
            },
            width=width,
            height=height,
        )


# ---------------------------------------------------------------------------
# Audio
# ---------------------------------------------------------------------------


class AudioExtractor(AttachmentMetadataExtractor):
    name = "audio"
    mime_prefixes = ("audio/",)
    priority = 10

    async def extract(
        self, *, path: str, mime: str, filename: str, size: int
    ) -> MetadataResult:
        mutagen = _safe_import("mutagen")
        if mutagen is None:
            return MetadataResult(partial=True, error="mutagen unavailable")
        try:
            audio = mutagen.File(path, easy=True)
        except Exception as e:  # noqa: BLE001
            return MetadataResult(partial=True, error=f"open failed: {e}")
        if audio is None:
            return MetadataResult(partial=True, error="unsupported audio format")

        info = getattr(audio, "info", None)
        type_specific: Dict[str, Any] = {
            "duration_seconds": getattr(info, "length", None) if info else None,
            "bitrate": getattr(info, "bitrate", None) if info else None,
            "sample_rate": getattr(info, "sample_rate", None) if info else None,
            "channels": getattr(info, "channels", None) if info else None,
        }
        # Tag bag (artist, album, title, etc.)
        tags: Dict[str, Any] = {}
        try:
            for k, v in audio.items():
                if isinstance(v, list) and len(v) == 1:
                    tags[k] = v[0]
                else:
                    tags[k] = list(v) if isinstance(v, list) else v
        except Exception:  # noqa: BLE001
            pass
        type_specific["tags"] = tags

        return MetadataResult(
            common=(
                {"duration_seconds": type_specific["duration_seconds"]}
                if type_specific["duration_seconds"] is not None
                else {}
            ),
            type_specific=type_specific,
        )


# ---------------------------------------------------------------------------
# Video
# ---------------------------------------------------------------------------


class VideoExtractor(AttachmentMetadataExtractor):
    name = "video"
    mime_prefixes = ("video/",)
    priority = 10

    async def extract(
        self, *, path: str, mime: str, filename: str, size: int
    ) -> MetadataResult:
        # ffprobe is the most portable way to inspect arbitrary video
        # containers. We shell out instead of binding through a wrapper
        # to keep the dep surface minimal — ffprobe ships with ffmpeg.
        import asyncio
        import json as _json
        import shutil

        ffprobe = shutil.which("ffprobe")
        if not ffprobe:
            return MetadataResult(partial=True, error="ffprobe unavailable")

        proc = await asyncio.create_subprocess_exec(
            ffprobe,
            "-v",
            "error",
            "-show_format",
            "-show_streams",
            "-of",
            "json",
            path,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await proc.communicate()
        if proc.returncode != 0:
            return MetadataResult(
                partial=True,
                error=f"ffprobe failed: {stderr.decode('utf-8', errors='replace')[:200]}",
            )
        try:
            data = _json.loads(stdout.decode("utf-8", errors="replace"))
        except Exception as e:  # noqa: BLE001
            return MetadataResult(partial=True, error=f"ffprobe parse failed: {e}")

        fmt = data.get("format", {}) or {}
        streams = data.get("streams", []) or []
        video_stream = next(
            (s for s in streams if s.get("codec_type") == "video"), None
        )
        width = video_stream.get("width") if video_stream else None
        height = video_stream.get("height") if video_stream else None
        duration = fmt.get("duration")
        try:
            duration_val: Optional[float] = float(duration) if duration else None
        except (TypeError, ValueError):
            duration_val = None

        type_specific: Dict[str, Any] = {
            "format_name": fmt.get("format_name"),
            "codec_name": video_stream.get("codec_name") if video_stream else None,
            "framerate": video_stream.get("r_frame_rate") if video_stream else None,
            "bitrate": fmt.get("bit_rate"),
            "stream_count": len(streams),
        }

        common: Dict[str, Any] = {}
        if width is not None:
            common["width"] = width
        if height is not None:
            common["height"] = height
        if duration_val is not None:
            common["duration_seconds"] = duration_val

        return MetadataResult(
            common=common,
            type_specific=type_specific,
            width=width,
            height=height,
        )


# Registration order matters only as a stable tiebreaker — the
# dispatcher uses priority first. Specialized types appear before
# generic prefixes so a same-priority tie still routes correctly.
BUILTIN_EXTRACTORS: tuple[AttachmentMetadataExtractor, ...] = (
    PdfExtractor(),
    DocxExtractor(),
    XlsxExtractor(),
    PptxExtractor(),
    ImageExtractor(),
    AudioExtractor(),
    VideoExtractor(),
    TextExtractor(),
)
