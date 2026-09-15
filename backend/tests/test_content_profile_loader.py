"""Tests for the YAML-based library profile loader."""

import os
import tempfile
from pathlib import Path

import pytest
import yaml

from app.services.content_profile_loader import (
    LibraryProfileSpec,
    load_library_profiles,
)


def write_profile(root: Path, slug: str, data: dict) -> None:
    pkg_dir = root / slug
    pkg_dir.mkdir(parents=True, exist_ok=True)
    (pkg_dir / "profile.yaml").write_text(yaml.dump(data), encoding="utf-8")


def test_load_empty_directory():
    with tempfile.TemporaryDirectory() as tmp:
        specs = load_library_profiles(Path(tmp))
    assert specs == []


def test_load_single_track_profile():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        write_profile(
            root,
            "bug-tracker",
            {
                "integral_profile_version": 2,
                "scope": "track",
                "package": {
                    "name": "Bug Tracker",
                    "slug": "bug-tracker",
                    "version": "1.0.0",
                    "description": "Track bugs.",
                    "tags": ["engineering"],
                },
                "track": {
                    "entry_types": [{"key": "bug", "name": "Bug", "fields": []}],
                    "views": [],
                },
            },
        )
        specs = load_library_profiles(root)

    assert len(specs) == 1
    s = specs[0]
    assert s.name == "Bug Tracker"
    assert s.slug == "bug-tracker"
    assert s.version == "1.0.0"
    assert s.library_package is True
    assert s.scope == "platform"
    assert s.manifest["scope"] == "track"
    assert "track" in s.manifest
    assert s.manifest["content_profile_schema_version"] == 2


def test_load_skips_malformed_yaml(caplog):
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        bad_dir = root / "bad-profile"
        bad_dir.mkdir()
        (bad_dir / "profile.yaml").write_text(":: invalid yaml ::", encoding="utf-8")
        specs = load_library_profiles(root)

    assert specs == []
    assert any("Failed to load profile" in r.message for r in caplog.records)


def test_package_block_shape_in_manifest():
    """manifest.package uses slug as name; version/description stripped; extra fields kept."""
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        write_profile(
            root,
            "test-pkg",
            {
                "integral_profile_version": 2,
                "scope": "track",
                "package": {
                    "name": "Test Pkg",
                    "slug": "test-pkg",
                    "version": "2.0.0",
                    "description": "Desc.",
                    "tags": ["test"],
                },
                "track": {"entry_types": [], "views": []},
            },
        )
        specs = load_library_profiles(root)

    assert len(specs) == 1
    pkg_in_manifest = specs[0].manifest.get("package", {})
    # slug becomes manifest.package.name (matches original Python dict shape)
    assert pkg_in_manifest.get("name") == "test-pkg"
    # version/description stripped from manifest.package (live on spec directly)
    assert "version" not in pkg_in_manifest
    assert "description" not in pkg_in_manifest
    # extra fields like tags are preserved
    assert pkg_in_manifest.get("tags") == ["test"]


def test_load_production_profiles():
    """Smoke test: all profiles in app/profiles/ load without error."""
    from pathlib import Path as P

    import app

    profiles_root = P(app.__file__).parent / "profiles"
    if not profiles_root.exists():
        pytest.skip("No profiles directory found")
    specs = load_library_profiles(profiles_root)
    assert len(specs) > 0
    for s in specs:
        assert s.name
        assert s.manifest.get("content_profile_schema_version") == 2
