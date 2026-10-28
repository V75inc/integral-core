"""Safe encoding and decoding of Integral portable archives.

This module handles bytes only. Authorization and restore are the caller's
responsibility; decoding an archive does not create or modify any resources.
"""

from __future__ import annotations

import io
import json
import stat
import zlib
from pathlib import PurePosixPath
from typing import Any
from zipfile import ZIP_DEFLATED, BadZipFile, ZipFile

from app.api.errors import BadRequestError

FORMAT = "integral-portable"
VERSION = 1
MAX_ARCHIVE_BYTES = 50 * 1024 * 1024
MAX_FILES = 4096


def _safe_name(name: str) -> bool:
    path = PurePosixPath(name)
    return (
        bool(name)
        and not path.is_absolute()
        and "\\" not in name
        and all(part not in ("", ".", "..") for part in name.split("/"))
    )


def encode_archive(manifest: dict[str, Any], files: dict[str, bytes]) -> bytes:
    """Encode an archive with a versioned descriptor and optional binary files."""
    if any(not _safe_name(name) or not name.startswith("files/") for name in files):
        raise BadRequestError(message="Invalid portable archive file path")
    descriptor = {**manifest, "format": FORMAT, "version": VERSION}
    try:
        raw = json.dumps(descriptor, ensure_ascii=False, allow_nan=False).encode(
            "utf-8"
        )
    except (TypeError, ValueError) as exc:
        raise BadRequestError(message="Invalid portable archive descriptor") from exc
    if (
        len(files) + 1 > MAX_FILES
        or len(raw) + sum(map(len, files.values())) > MAX_ARCHIVE_BYTES
    ):
        raise BadRequestError(message="Portable archive exceeds size or file limits")
    buffer = io.BytesIO()
    with ZipFile(buffer, "w", compression=ZIP_DEFLATED) as archive:
        archive.writestr("integral.json", raw)
        for name, data in sorted(files.items()):
            archive.writestr(name, data)
    result = buffer.getvalue()
    if len(result) > MAX_ARCHIVE_BYTES:
        raise BadRequestError(message="Portable archive exceeds size limit")
    return result


def decode_archive(data: bytes) -> tuple[dict[str, Any], dict[str, bytes]]:
    """Reject unsafe, duplicate or oversized members before reading content."""
    if len(data) > MAX_ARCHIVE_BYTES:
        raise BadRequestError(message="Portable archive exceeds size limit")
    try:
        with ZipFile(io.BytesIO(data)) as archive:
            members = archive.infolist()
            if (
                len(members) > MAX_FILES
                or sum(m.file_size for m in members) > MAX_ARCHIVE_BYTES
            ):
                raise BadRequestError(
                    message="Portable archive exceeds expanded size or file limits"
                )
            names = [m.filename for m in members]
            if len(set(names)) != len(names) or "integral.json" not in names:
                raise BadRequestError(
                    message="Portable archive has missing or duplicate members"
                )
            if any(
                m.is_dir()
                or stat.S_ISLNK(m.external_attr >> 16)
                or bool(m.flag_bits & 1)
                or not _safe_name(m.filename)
                or (
                    m.filename != "integral.json"
                    and not m.filename.startswith("files/")
                )
                for m in members
            ):
                raise BadRequestError(message="Invalid portable archive member")

            def unique_object(pairs):
                result = {}
                for key, value in pairs:
                    if key in result:
                        raise ValueError("duplicate JSON key")
                    result[key] = value
                return result

            def reject_constant(value):
                raise ValueError(f"invalid JSON constant: {value}")

            descriptor = json.loads(
                archive.read("integral.json"),
                object_pairs_hook=unique_object,
                parse_constant=reject_constant,
            )
            from app.services.operational_model_import import validate_manifest_budget

            validate_manifest_budget(descriptor)
            if (
                not isinstance(descriptor, dict)
                or descriptor.get("format") != FORMAT
                or type(descriptor.get("version")) is not int
                or descriptor.get("version") != VERSION
            ):
                raise BadRequestError(message="Unsupported portable archive format")
            return descriptor, {
                name: archive.read(name) for name in names if name != "integral.json"
            }
    except (
        BadZipFile,
        ValueError,
        RuntimeError,
        zlib.error,
    ) as exc:
        raise BadRequestError(message="Invalid portable archive") from exc
