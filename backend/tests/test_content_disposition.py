"""Tests for latin-1-safe Content-Disposition header construction."""

from starlette.responses import StreamingResponse

from app.api.attachments import _content_disposition


def test_content_disposition_ascii_filename() -> None:
    header = _content_disposition("attachment", "photo.jpg")
    assert header == 'attachment; filename="photo.jpg"'


def test_content_disposition_latin1_filename() -> None:
    header = _content_disposition("inline", "café.pdf")
    assert header == 'inline; filename="café.pdf"'


def test_content_disposition_unicode_filename_is_latin1_safe() -> None:
    filename = "report" + "\u202f" + "2024.pdf"
    header = _content_disposition("attachment", filename)
    assert "filename*=" in header
    assert "UTF-8''" in header
    assert "%E2%80%AF" in header
    header.encode("latin-1")

    # Starlette rejects non-latin-1 header values at response init time.
    StreamingResponse(
        iter([b"x"]),
        headers={"Content-Disposition": header},
    )


def test_content_disposition_escapes_quotes() -> None:
    header = _content_disposition("attachment", 'my "file".txt')
    assert header == 'attachment; filename="my \\"file\\".txt"'
