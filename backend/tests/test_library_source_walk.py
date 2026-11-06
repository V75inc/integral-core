"""The faster library source walk must list exactly what the pathlib walk did.

``_library_source_key`` decides when validated, signature-checked library
sources may be reused, so the faster listing has to give the same entries in
the same order, including symlinks, dotfiles and awkward names.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from app.services import operational_model_library_sync as sync

pytestmark = pytest.mark.smoke


def _pathlib_walk(root: Path) -> list:
    """The walk as it was written before (reference)."""
    out = []
    for path in sorted(root.glob("*/*")):
        paths = [path, *sorted(path.rglob("*"))] if path.is_dir() else [path]
        out.extend(str(p) for p in paths)
    return out


def _fast_walk(root: Path) -> list:
    out = []
    for path in sync._sorted_paths(sync._package_entries(root)):
        paths = (
            [path, *sync._sorted_paths(sync._tree_entries(path))]
            if os.path.isdir(path)
            else [path]
        )
        out.extend(paths)
    return out


@pytest.fixture
def tree(tmp_path):
    root = tmp_path / "packages"
    a = root / "payroll-guyana"
    (a / "tools" / "_shared").mkdir(parents=True)
    (a / "tools" / "_shared" / "filing_batch.py").write_text("x")
    (a / "tools" / "__pycache__").mkdir()
    (a / "tools" / "__pycache__" / "x.cpython-312.pyc").write_bytes(b"x")
    (a / "tools" / "a-b.py").write_text("x")
    (a / "tools" / "a.b.py").write_text("x")
    (a / "tools" / "a").mkdir()
    (a / "tools" / "a" / "z.py").write_text("x")
    (a / ".hidden").write_text("x")
    (a / "skills" / "empty").mkdir(parents=True)
    (a / "operational-model.yaml").write_text("x")
    (a / "naïve-ünïcode.md").write_text("x")
    b = root / "payroll"
    b.mkdir()
    (b / "operational-model.yaml").write_text("x")
    # A file directly in the root is not a package entry.
    (root / "README.md").write_text("x")
    # Symlinks: a linked file, a linked directory inside a package (listed but
    # not entered), and a linked package directory at the top (entered).
    outside = tmp_path / "outside"
    (outside / "deep").mkdir(parents=True)
    (outside / "deep" / "f.py").write_text("x")
    (a / "tools" / "linked.py").symlink_to(a / "tools" / "a-b.py")
    (a / "tools" / "linked_dir").symlink_to(outside)
    (root / "linked-package").symlink_to(outside)
    return root


def test_fast_walk_lists_the_same_entries_in_the_same_order(tree):
    expected = _pathlib_walk(tree)
    assert expected  # the fixture really has content
    assert _fast_walk(tree) == expected


def test_fast_walk_on_missing_root_is_empty(tmp_path):
    missing = tmp_path / "nope"
    assert _fast_walk(missing) == _pathlib_walk(missing) == []


def test_source_key_still_changes_when_a_file_changes(tree, monkeypatch):
    from app.services import operational_model_loader as loader

    monkeypatch.setattr(loader, "_PROFILES_ROOT", tree)
    before = sync._library_source_key()
    assert sync._library_source_key() == before
    target = tree / "payroll-guyana" / "tools" / "__pycache__" / "x.cpython-312.pyc"
    target.write_bytes(b"changed bytes")
    assert sync._library_source_key() != before
