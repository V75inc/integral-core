"""Uploaded packages cannot expand into unbounded work or arbitrary files."""

import io
import zipfile

import pytest

from app.exceptions import BadRequestError
from app.services.operational_model_import import (
    parse_archive_manifests,
    parse_manifest_bytes,
)

pytestmark = [pytest.mark.unit, pytest.mark.smoke]


def archive(files):
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED) as zipped:
        for name, data in files:
            zipped.writestr(name, data)
    return output.getvalue()


def test_archive_reads_manifests_without_extracting_assets(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    data = archive(
        [
            ("example/operational-model.yaml", "scope: app\napp: {}"),
            ("example/tools/script.py", "untrusted"),
        ]
    )
    assert parse_archive_manifests(data) == [
        ("example", {"scope": "app", "app": {}}, None)
    ]
    assert list(tmp_path.iterdir()) == []


def test_archive_limits_expanded_size_before_reading(monkeypatch):
    import app.services.operational_model_import as parser

    data = archive([("example/operational-model.yaml", "x" * 2000)])
    monkeypatch.setattr(parser, "MAX_UPLOAD_BYTES", 500)
    with pytest.raises(BadRequestError, match="expanded size"):
        parse_archive_manifests(data)


def test_archive_rejects_duplicate_manifests():
    with pytest.warns(UserWarning):
        data = archive(
            [
                ("example/operational-model.yaml", "a: 1"),
                ("example/operational-model.yaml", "a: 2"),
            ]
        )
    with pytest.raises(BadRequestError, match="Duplicate"):
        parse_archive_manifests(data)


def test_yaml_cycles_are_bounded():
    with pytest.raises(BadRequestError, match="deeply nested"):
        parse_manifest_bytes(b"cycle: &cycle\n  again: *cycle")


def test_invalid_package_keeps_preview_error_separate():
    data = archive(
        [
            ("bad/operational-model.yaml", "["),
            ("good/operational-model.yaml", "scope: app"),
        ]
    )
    packages = parse_archive_manifests(data)
    assert packages[0][0] == "bad" and packages[0][1] is None and packages[0][2]
    assert packages[1] == ("good", {"scope": "app"}, None)
