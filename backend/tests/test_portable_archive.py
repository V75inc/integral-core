"""Portable archive framing must reject unsafe or ambiguous uploads."""

import io
import json
from zipfile import ZIP_DEFLATED, ZipFile

import pytest

from app.api.errors import BadRequestError
from app.services import portable_archive

pytestmark = [pytest.mark.unit, pytest.mark.smoke]


def test_archive_round_trips_descriptor_and_binary():
    data = portable_archive.encode_archive({"kind": "app"}, {"files/1": b"\x00\xff"})
    manifest, files = portable_archive.decode_archive(data)
    assert manifest == {"kind": "app", "format": "integral-portable", "version": 1}
    assert files == {"files/1": b"\x00\xff"}


@pytest.mark.parametrize(
    "name",
    [
        "../outside",
        "/absolute",
        "files/../../escape",
        "files\\escape",
        "files/./item",
        "files//item",
    ],
)
def test_archive_rejects_unsafe_members(name):
    stream = io.BytesIO()
    with ZipFile(stream, "w") as archive:
        archive.writestr(
            "integral.json", json.dumps({"format": "integral-portable", "version": 1})
        )
        archive.writestr(name, b"x")
    with pytest.raises(BadRequestError):
        portable_archive.decode_archive(stream.getvalue())


def test_archive_rejects_expanded_size_limit(monkeypatch):
    stream = io.BytesIO()
    with ZipFile(stream, "w", compression=ZIP_DEFLATED) as archive:
        archive.writestr(
            "integral.json", json.dumps({"format": "integral-portable", "version": 1})
        )
        archive.writestr("files/1", b"x" * 500)
    monkeypatch.setattr(
        portable_archive, "MAX_ARCHIVE_BYTES", len(stream.getvalue()) + 1
    )
    with pytest.raises(BadRequestError):
        portable_archive.decode_archive(stream.getvalue())


@pytest.mark.parametrize(
    "descriptor",
    [
        b'{"format":"integral-portable","version":1,"version":2}',
        b'{"format":"integral-portable","version":1,"amount":NaN}',
    ],
)
def test_archive_rejects_ambiguous_json(descriptor):
    stream = io.BytesIO()
    with ZipFile(stream, "w") as archive:
        archive.writestr("integral.json", descriptor)
    with pytest.raises(BadRequestError):
        portable_archive.decode_archive(stream.getvalue())


@pytest.mark.asyncio
async def test_untrusted_runtime_declarations_cannot_be_installed_from_a_zip():
    from app.services.portable_transfer import read_archive

    manifest = {
        "kind": "track",
        "mode": "configuration",
        "tracks": [
            {
                "ref": "track",
                "title": "Unsafe",
                "manifest": {
                    "scope": "track",
                    "track": {
                        "tools": [{"key": "arbitrary", "handler": "untrusted.run"}]
                    },
                },
                "entry_types": [{"ref": "type", "name": "Record"}],
            }
        ],
    }
    with pytest.raises(BadRequestError, match="runtime"):
        await read_archive(portable_archive.encode_archive(manifest, {}))


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "binding", [{"handler_ref": "untrusted:run"}, {"tool": "custom"}]
)
async def test_custom_query_handlers_require_installed_runtime(binding):
    from app.services.portable_transfer import read_archive

    descriptor = {
        "kind": "app",
        "mode": "configuration",
        "apps": [
            {
                "ref": "app",
                "name": "Unsafe",
                "manifest": {"app": {"queries": [{"key": "custom", **binding}]}},
            }
        ],
    }
    with pytest.raises(BadRequestError, match="runtime"):
        await read_archive(portable_archive.encode_archive(descriptor, {}))


@pytest.mark.asyncio
async def test_configuration_cannot_reference_a_source_workspace_track():
    from app.services.portable_transfer import read_archive

    manifest = {
        "kind": "track",
        "mode": "configuration",
        "tracks": [
            {
                "ref": "track",
                "title": "Copy",
                "entry_types": [{"ref": "type", "name": "Record"}],
                "views": [
                    {
                        "ref": "view",
                        "name": "Table",
                        "type": "table",
                        "config": {"track_id": "n.Track.outside"},
                    }
                ],
            }
        ],
    }
    with pytest.raises(BadRequestError, match="outside"):
        await read_archive(portable_archive.encode_archive(manifest, {}))


@pytest.mark.asyncio
async def test_tag_hierarchy_cycles_are_rejected_before_restore():
    from app.services.portable_transfer import read_archive

    manifest = {
        "kind": "track",
        "mode": "configuration",
        "tracks": [
            {
                "ref": "track",
                "title": "Copy",
                "entry_types": [{"ref": "type", "name": "Record"}],
                "tags": [
                    {"ref": "a", "name": "A", "parent_ref": "b"},
                    {"ref": "b", "name": "B", "parent_ref": "a"},
                ],
            }
        ],
    }
    with pytest.raises(BadRequestError, match="hierarchy"):
        await read_archive(portable_archive.encode_archive(manifest, {}))


def test_structural_remapping_preserves_scope_and_free_text():
    from app.services.portable_transfer import _remap

    mapping = {"track": "n.Track.new", "n.Track.old": "n.Track.copy"}
    assert _remap(
        {"scope": "track", "track_id": "track", "title": "n.Track.old"}, mapping
    ) == {"scope": "track", "track_id": "n.Track.new", "title": "n.Track.old"}


def test_composite_file_fields_receive_new_attachment_ids():
    from app.schemas.portable_transfer import PortableEntry, PortableType
    from app.services.portable_transfer import _field_values

    entry = PortableEntry(
        ref="record", type_ref="type", custom_fields={"document": "old-file"}
    )
    et = PortableType(
        ref="type",
        name="Record",
        form_schema={"fields": [{"key": "document", "type": "document-file"}]},
    )
    manifest = {"field_types": [{"key": "document-file", "base": "file"}]}
    assert _field_values(entry, et, {"old-file": "new-file"}, manifest) == {
        "document": "new-file"
    }
