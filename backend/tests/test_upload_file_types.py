"""Uploads accept real business document formats, and still refuse executables."""

import pytest

import app.api  # noqa: F401  (import order: the API package must load first)
from app.exceptions import BadRequestError
from app.services.attachment_upload_shared import (
    is_mime_allowed,
    resolve_effective_mime,
)

DOCS = [
    ("minutes.rtf", "application/rtf"),
    ("policy.odt", "application/vnd.oasis.opendocument.text"),
    ("budget.ods", "application/vnd.oasis.opendocument.spreadsheet"),
    ("deck.odp", "application/vnd.oasis.opendocument.presentation"),
    ("book.epub", "application/epub+zip"),
    ("mail.eml", "message/rfc822"),
    ("mail.msg", "application/vnd.ms-outlook"),
    ("plan.mpp", "application/vnd.ms-project"),
    ("flow.vsdx", "application/vnd.ms-visio.drawing"),
    ("macro.xlsm", "application/vnd.ms-excel.sheet.macroenabled.12"),
    ("layout.dwg", "image/vnd.dwg"),
    ("cfg.yaml", "application/yaml"),
    ("data.tsv", "text/tab-separated-values"),
    ("backup.tar", "application/x-tar"),
    ("pack.7z", "application/x-7z-compressed"),
]


@pytest.mark.parametrize("name,mime", DOCS)
def test_document_formats_are_accepted_by_extension(name, mime):
    # A generic claim (what a browser sends for formats it does not know).
    assert (
        resolve_effective_mime(
            head_bytes=b"\x00" * 64,
            content_type_claim="application/octet-stream",
            filename=name,
        )
        == mime
    )
    assert is_mime_allowed(mime)


@pytest.mark.parametrize(
    "name", ["setup.exe", "run.sh", "x.bat", "lib.dll", "page.html", "app.js", "blob.bin"]
)
def test_executables_scripts_and_unknown_types_stay_refused(name):
    with pytest.raises(BadRequestError):
        resolve_effective_mime(
            head_bytes=b"MZ\x90\x00" + b"\x00" * 60,
            content_type_claim="application/octet-stream",
            filename=name,
        )


def test_extra_mime_types_are_configurable(monkeypatch):
    from app.config import settings

    assert not is_mime_allowed("application/x-custom-doc")
    monkeypatch.setattr(
        settings, "ATTACHMENT_EXTRA_ALLOWED_MIME_TYPES", "application/x-custom-doc, text/x-foo"
    )
    assert is_mime_allowed("application/x-custom-doc")
    assert is_mime_allowed("text/x-foo")


def test_storage_layer_accepts_what_the_upload_layer_accepts():
    from app.services.attachment_storage import _storage_allowed_mime_types

    allowed = _storage_allowed_mime_types()
    for mime in (
        "text/rtf",
        "application/vnd.oasis.opendocument.text",
        "application/vnd.ms-project",
        "image/vnd.dwg",
        "application/octet-stream",
        "video/quicktime",
    ):
        assert mime in allowed
    # Executables are not in the list (the validator also blocks them by name).
    assert "application/x-dosexec" not in allowed
    assert "application/x-sh" not in allowed


def test_sniffer_treats_container_and_alias_types_as_compatible():
    from app.services.attachment_content_sniffer import _are_compatible

    assert _are_compatible("application/rtf", "text/rtf")
    assert _are_compatible("application/vnd.oasis.opendocument.text", "application/zip")
    assert _are_compatible("application/vnd.ms-project", "application/cdfv2")
    assert _are_compatible("application/yaml", "text/plain")
    assert _are_compatible("message/rfc822", "text/plain")
    # A spoof is still a mismatch.
    assert not _are_compatible("application/pdf", "application/x-dosexec")
    assert not _are_compatible("application/rtf", "application/x-dosexec")
    assert not _are_compatible("application/yaml", "application/x-dosexec")
