"""Bounded, file-only package import parsing; no graph operations."""

from __future__ import annotations

import io
import json
from pathlib import PurePosixPath
from typing import Any
from zipfile import BadZipFile, ZipFile

import yaml

from app.exceptions import BadRequestError

MAX_UPLOAD_BYTES = 50 * 1024 * 1024
MAX_MANIFEST_BYTES = 10 * 1024 * 1024
MAX_ARCHIVE_FILES = 4096
MAX_ARCHIVE_PACKAGES = 128


def validate_manifest_budget(manifest: Any) -> None:
    """Bound expanded YAML aliases/depth before compiler hashing/normalization."""
    pending = [(manifest, 0)]
    visited = 0
    while pending:
        value, depth = pending.pop()
        visited += 1
        if visited > 100_000 or depth > 64:
            raise BadRequestError(message="Manifest is too large or deeply nested")
        if isinstance(value, dict):
            if len(value) + len(pending) > 100_000:
                raise BadRequestError(message="Manifest is too large")
            pending.extend((child, depth + 1) for child in value.values())
        elif isinstance(value, list):
            if len(value) + len(pending) > 100_000:
                raise BadRequestError(message="Manifest is too large")
            pending.extend((child, depth + 1) for child in value)


def parse_manifest_bytes(data: bytes, *, is_yaml: bool = True) -> dict:
    """Parse one size/depth-bounded UTF-8 YAML or JSON manifest."""
    if len(data) > MAX_MANIFEST_BYTES:
        raise BadRequestError(message="Manifest exceeds the 10 MB limit")
    try:
        text = data.decode("utf-8")
        loaded = yaml.safe_load(text) if is_yaml else json.loads(text)
    except (UnicodeError, yaml.YAMLError, json.JSONDecodeError) as exc:
        raise BadRequestError(message=f"Invalid manifest: {exc}") from exc
    if loaded is None:
        loaded = {}
    if not isinstance(loaded, dict):
        raise BadRequestError(message="Manifest must deserialize to an object")
    validate_manifest_budget(loaded)
    return loaded


def parse_archive_manifests(data: bytes) -> list[tuple[str, dict | None, str | None]]:
    """Read only declared one-level manifests; never unpack arbitrary ZIP assets."""
    if len(data) > MAX_UPLOAD_BYTES:
        raise BadRequestError(message="ZIP archive exceeds the 50 MB limit")
    try:
        with ZipFile(io.BytesIO(data)) as archive:
            entries = archive.infolist()
            if (
                len(entries) > MAX_ARCHIVE_FILES
                or sum(e.file_size for e in entries) > MAX_UPLOAD_BYTES
            ):
                raise BadRequestError(
                    message="ZIP archive exceeds expanded size or file limits"
                )
            manifests = []
            names = set()
            for entry in entries:
                path = PurePosixPath(entry.filename)
                if (
                    entry.is_dir()
                    or len(path.parts) != 2
                    or path.parts[-1] != "operational-model.yaml"
                ):
                    continue
                if path.is_absolute() or ".." in path.parts or "\\" in entry.filename:
                    raise BadRequestError(message="Invalid package path in ZIP archive")
                if entry.filename in names:
                    raise BadRequestError(message="Duplicate manifest in ZIP archive")
                names.add(entry.filename)
                manifests.append(entry)
            if not manifests:
                raise BadRequestError(
                    message="No operational-model.yaml files found in archive. Expected structure: <package-name>/operational-model.yaml"
                )
            if len(manifests) > MAX_ARCHIVE_PACKAGES:
                raise BadRequestError(message="ZIP archive contains too many packages")
            result = []
            for entry in sorted(manifests, key=lambda item: item.filename):
                name = PurePosixPath(entry.filename).parts[0]
                if entry.file_size > MAX_MANIFEST_BYTES:
                    raise BadRequestError(message="Manifest exceeds the 10 MB limit")
                try:
                    raw = parse_manifest_bytes(archive.read(entry))
                    result.append((name, raw, None))
                except Exception as exc:
                    result.append((name, None, str(exc)))
            return result
    except BadZipFile as exc:
        raise BadRequestError(message=f"Invalid ZIP archive: {exc}") from exc
