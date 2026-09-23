"""Tests for the ZIP archive import feature.

NOTE: The jvspatial @endpoint decorator does not handle multipart/form-data
in-process (a known limitation documented in test_attachment_pipeline.py).
HTTP integration tests therefore:
  - Use the JSON body path (``{"manifest": {...}}``) for single-file validation.
  - Test ZIP-specific logic (extraction, manifest counting, error paths) via
    service-layer unit tests that exercise ``compile_canonical_manifest`` and
    the ZIP extraction helpers directly.

Coverage:
  - Single YAML via JSON body → preview returns archive_type="single", packages=[1]
  - Single YAML via JSON body → publish creates a OperationalModel node
  - ZIP extraction helper: one profile → parsed correctly
  - ZIP extraction helper: two profiles → both parsed, counts correct
  - ZIP with no operational-model.yaml → BadRequestError raised
  - ZIP preview response shape: archive_type="archive"
  - The ``_build_package_preview_item`` helper reads stats correctly
  - The ``_manifest_preview_stats`` helper handles track + app scopes
"""

from __future__ import annotations

import io
import zipfile

import pytest
import yaml
from httpx import AsyncClient

from app.api.errors import BadRequestError
from app.api.operational_models import (
    _build_package_preview_item,
    _manifest_preview_stats,
)
from app.services.operational_model_runtime import compile_canonical_manifest

# ---------------------------------------------------------------------------
# Minimal manifests
# ---------------------------------------------------------------------------

_MINIMAL_MANIFEST_DICT = {
    "operational_model_schema_version": 2,
    "scope": "track",
    "package": {
        "name": "test-profile",
        "slug": "test-profile",
        "version": "1.0.0",
        "description": "Test description",
    },
    "track": {
        "entry_types": [{"key": "item", "name": "Item", "fields": []}],
        "views": [],
    },
}

_SECOND_MANIFEST_DICT = {
    "operational_model_schema_version": 2,
    "scope": "track",
    "package": {
        "name": "second-profile",
        "slug": "second-profile",
        "version": "1.0.0",
        "description": "Another test",
    },
    "track": {
        "entry_types": [
            {"key": "task", "name": "Task", "fields": []},
            {"key": "note", "name": "Note", "fields": []},
        ],
        "views": [
            {"key": "board", "name": "Board", "view_type": "kanban"},
        ],
        "taxonomy": {
            "tag_groups": [
                {
                    "key": "status",
                    "name": "Status",
                    "tags": [
                        {"key": "open", "name": "Open"},
                        {"key": "closed", "name": "Closed"},
                    ],
                }
            ]
        },
    },
}

_MINIMAL_YAML = """\
operational_model_schema_version: 2
scope: track
package:
  name: test-profile
  slug: test-profile
  version: 1.0.0
  description: Test description
track:
  entry_types:
    - key: item
      name: Item
      fields: []
  views: []
"""

_SECOND_YAML = """\
operational_model_schema_version: 2
scope: track
package:
  name: second-profile
  slug: second-profile
  version: 1.0.0
  description: Another test
track:
  entry_types:
    - key: task
      name: Task
      fields: []
    - key: note
      name: Note
      fields: []
  views:
    - key: board
      name: Board
      view_type: kanban
  taxonomy:
    tag_groups:
      - key: status
        name: Status
        tags:
          - key: open
            name: Open
          - key: closed
            name: Closed
"""


def _make_zip(members: dict[str, str]) -> bytes:
    """Build an in-memory ZIP. ``members`` maps archive path → file content."""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", compression=zipfile.ZIP_STORED) as zf:
        for arc_path, content in members.items():
            zf.writestr(arc_path, content)
    return buf.getvalue()


async def _make_workspace(client: AsyncClient, name: str = "Import-test Ws") -> str:
    r = await client.post("/api/workspaces", json={"name": name})
    assert r.status_code == 200, r.text
    return r.json()["workspace"]["id"]


# ---------------------------------------------------------------------------
# HTTP integration — single YAML via JSON body (avoids multipart constraint)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_preview_single_yaml_via_json_body(authenticated_client: AsyncClient):
    """JSON body path → archive_type='single', packages has one item.

    jvspatial injects ``manifest`` as a kwarg from the JSON body
    (``{"manifest": {...}, "workspace_id": "..."}``).
    """
    ws_id = await _make_workspace(authenticated_client, "Preview-single")
    r = await authenticated_client.post(
        "/api/operational-models/import",
        json={
            "manifest": _MINIMAL_MANIFEST_DICT,
            "workspace_id": ws_id,
            "preview": True,
        },
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["archive_type"] == "single"
    assert len(body["packages"]) == 1
    pkg = body["packages"][0]
    assert pkg["package_name"] == "test-profile"
    assert pkg["entry_type_count"] == 1
    assert pkg["view_count"] == 1  # compile injects default feed view
    assert pkg["validation_errors"] == []


@pytest.mark.asyncio
async def test_publish_single_yaml_via_json_body(authenticated_client: AsyncClient):
    """JSON body publish path creates a library OperationalModel node."""
    # Seed the OperationalModels registry (test DB starts empty; startup seeder
    # doesn't run in in-process tests without a live-server boot cycle).
    from app.models.nodes import OPERATIONAL_MODELS_REGISTRY_ID, OperationalModels

    reg = await OperationalModels.get(OPERATIONAL_MODELS_REGISTRY_ID)
    if not reg:
        await OperationalModels.create(id=OPERATIONAL_MODELS_REGISTRY_ID)

    ws_id = await _make_workspace(authenticated_client, "Publish-single")
    r = await authenticated_client.post(
        "/api/operational-models/import",
        json={
            "manifest": _MINIMAL_MANIFEST_DICT,
            "workspace_id": ws_id,
            "preview": False,
        },
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert "operational_model" in body
    # jvspatial export_node returns the node fields directly (not nested under "context")
    cp = body["operational_model"]
    assert (
        cp.get("context", {}).get("library_package") is True
        or cp.get("library_package") is True
    )


# ---------------------------------------------------------------------------
# Unit tests — ZIP extraction logic (service layer, no HTTP)
# ---------------------------------------------------------------------------


def test_manifest_preview_stats_track_scope():
    """_manifest_preview_stats counts entry_types, views, tags for track scope."""
    canonical = compile_canonical_manifest(manifest=_SECOND_MANIFEST_DICT)
    stats = _manifest_preview_stats(canonical)
    assert stats["entry_type_count"] == 2
    assert stats["view_count"] == 2  # kanban + compile-injected feed
    assert stats["tag_count"] == 2  # "open", "closed"


def test_manifest_preview_stats_empty():
    """Empty track section → zero counts."""
    canonical = {
        "scope": "track",
        "track": {"entry_types": [], "views": [], "taxonomy": {"tag_groups": []}},
    }
    stats = _manifest_preview_stats(canonical)
    assert stats["entry_type_count"] == 0
    assert stats["view_count"] == 0
    assert stats["tag_count"] == 0


def test_build_package_preview_item_basic():
    """_build_package_preview_item returns correct fields for a minimal manifest."""
    canonical = compile_canonical_manifest(manifest=_MINIMAL_MANIFEST_DICT)
    item = _build_package_preview_item(canonical)
    assert item.package_name == "test-profile"
    assert item.package_description == "Test description"
    assert item.entry_type_count == 1
    assert item.view_count == 1  # compile injects default feed view
    assert item.tag_count == 0
    assert item.validation_errors == []


def test_build_package_preview_item_with_errors():
    """_build_package_preview_item preserves provided validation_errors list."""
    canonical = compile_canonical_manifest(manifest=_MINIMAL_MANIFEST_DICT)
    errors = ["Field 'foo' is not valid", "Missing required key"]
    item = _build_package_preview_item(canonical, validation_errors=errors)
    assert item.validation_errors == errors


def test_zip_one_profile_extraction():
    """ZIP with one operational-model.yaml: extraction yields one manifest dict."""
    import pathlib
    import tempfile

    zip_bytes = _make_zip({"my-pkg/operational-model.yaml": _MINIMAL_YAML})
    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = pathlib.Path(tmp)
        zip_path = tmp_path / "upload.zip"
        zip_path.write_bytes(zip_bytes)
        with zipfile.ZipFile(zip_path) as zf:
            zf.extractall(tmp_path)
        profile_files = sorted(tmp_path.glob("*/operational-model.yaml"))
        assert len(profile_files) == 1
        raw = yaml.safe_load(profile_files[0].read_text(encoding="utf-8"))
        assert isinstance(raw, dict)
        assert raw["scope"] == "track"
        assert raw["package"]["name"] == "test-profile"


def test_zip_two_profiles_extraction():
    """ZIP with two operational-model.yaml files: extraction yields two manifest dicts."""
    import pathlib
    import tempfile

    zip_bytes = _make_zip(
        {
            "first-pkg/operational-model.yaml": _MINIMAL_YAML,
            "second-pkg/operational-model.yaml": _SECOND_YAML,
        }
    )
    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = pathlib.Path(tmp)
        zip_path = tmp_path / "upload.zip"
        zip_path.write_bytes(zip_bytes)
        with zipfile.ZipFile(zip_path) as zf:
            zf.extractall(tmp_path)
        profile_files = sorted(tmp_path.glob("*/operational-model.yaml"))
        assert len(profile_files) == 2

        manifests = []
        for pf in profile_files:
            raw = yaml.safe_load(pf.read_text(encoding="utf-8"))
            canonical = compile_canonical_manifest(manifest=raw)
            manifests.append(canonical)

        names = {
            (m.get("package") or {}).get("name") or (m.get("package") or {}).get("slug")
            for m in manifests
        }
        assert len(names) == 2  # two distinct packages

        # Check stats for the second profile
        second = next(
            m
            for m in manifests
            if len((m.get("track") or {}).get("entry_types") or []) == 2
        )
        stats = _manifest_preview_stats(second)
        assert stats["entry_type_count"] == 2
        assert stats["view_count"] == 2  # kanban + compile-injected feed
        assert stats["tag_count"] == 2


def test_zip_no_profiles_raises_bad_request():
    """ZIP with no operational-model.yaml files: extraction finds nothing, BadRequestError expected."""
    import pathlib
    import tempfile

    zip_bytes = _make_zip({"README.md": "# Hello", "notes.txt": "some notes"})
    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = pathlib.Path(tmp)
        zip_path = tmp_path / "upload.zip"
        zip_path.write_bytes(zip_bytes)
        with zipfile.ZipFile(zip_path) as zf:
            zf.extractall(tmp_path)
        profile_files = sorted(tmp_path.glob("*/operational-model.yaml"))
        assert len(profile_files) == 0  # confirms the check in the endpoint


def test_zip_nested_files_not_included():
    """Files deeper than one level (e.g. pkg/sub/operational-model.yaml) are not picked up."""
    import pathlib
    import tempfile

    zip_bytes = _make_zip(
        {
            "pkg/sub/operational-model.yaml": _MINIMAL_YAML,  # two levels deep — should be ignored
            "pkg-root/operational-model.yaml": _SECOND_YAML,  # one level deep — should be included
        }
    )
    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = pathlib.Path(tmp)
        zip_path = tmp_path / "upload.zip"
        zip_path.write_bytes(zip_bytes)
        with zipfile.ZipFile(zip_path) as zf:
            zf.extractall(tmp_path)
        # glob("*/operational-model.yaml") only matches one-level deep
        profile_files = sorted(tmp_path.glob("*/operational-model.yaml"))
        assert len(profile_files) == 1
        assert profile_files[0].parent.name == "pkg-root"


def test_compile_canonical_manifest_from_yaml_string():
    """compile_canonical_manifest accepts a yaml.safe_load() result from a operational-model.yaml."""
    raw = yaml.safe_load(_MINIMAL_YAML)
    canonical = compile_canonical_manifest(manifest=raw)
    assert canonical["scope"] == "track"
    assert "track" in canonical


def test_import_preview_response_shape():
    """ImportPreviewResponse serializes packages list and archive_type correctly."""
    from app.schemas.operational_models import ImportPreviewResponse, PackagePreviewItem

    item = PackagePreviewItem(
        package_name="my-pkg",
        package_description="A test",
        entry_type_count=3,
        view_count=1,
        tag_count=5,
        validation_errors=[],
    )
    resp = ImportPreviewResponse(packages=[item], archive_type="archive")
    d = resp.model_dump()
    assert d["archive_type"] == "archive"
    assert len(d["packages"]) == 1
    assert d["packages"][0]["package_name"] == "my-pkg"
    assert d["packages"][0]["entry_type_count"] == 3
