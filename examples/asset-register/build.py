#!/usr/bin/env python3
"""Build the Asset Register App as a deterministic portable archive.

The archive is deliberately a plain directory bundle rather than a Python
wheel. Integral discovers ``<package-root>/<slug>/profile.yaml`` and resolves
declared handlers relative to that bundle directory, so this preserves the
same public installation shape an external App author uses at runtime.
"""

from __future__ import annotations

import argparse
import gzip
import hashlib
import io
import re
import tarfile
from pathlib import Path
from typing import Iterable


PACKAGE_ROOT = Path(__file__).resolve().parent
PROFILE_PATH = PACKAGE_ROOT / "profile.yaml"
_IGNORED_PARTS = frozenset({"__pycache__", ".git", ".pytest_cache"})
_IGNORED_SUFFIXES = frozenset({".pyc", ".pyo"})


def _package_metadata() -> tuple[str, str]:
    """Read the package slug and version without requiring a YAML runtime."""
    content = PROFILE_PATH.read_text(encoding="utf-8")
    slug_match = re.search(r"^  slug:\s*([^\s#]+)", content, flags=re.MULTILINE)
    version_match = re.search(
        r"^  version:\s*([^\s#]+)", content, flags=re.MULTILINE
    )
    if slug_match is None or version_match is None:
        raise ValueError("profile.yaml must declare package.slug and package.version")
    return slug_match.group(1), version_match.group(1)


def _bundle_files() -> Iterable[Path]:
    for path in sorted(PACKAGE_ROOT.rglob("*")):
        if not path.is_file():
            continue
        relative = path.relative_to(PACKAGE_ROOT)
        if any(part in _IGNORED_PARTS for part in relative.parts):
            continue
        if path.suffix in _IGNORED_SUFFIXES:
            continue
        yield path


def build_archive(output_dir: Path) -> tuple[Path, str]:
    """Build the archive and sidecar SHA-256 file, returning both values."""
    slug, version = _package_metadata()
    output_dir.mkdir(parents=True, exist_ok=True)
    archive = output_dir / f"{slug}-{version}.tar.gz"

    # Normalize owner, mode and timestamps so a rebuild of unchanged sources
    # has one digest. That digest becomes the artifact identity recorded by a
    # package publisher or release evidence bundle.
    with archive.open("wb") as raw:
        with gzip.GzipFile(fileobj=raw, mode="wb", mtime=0) as compressed:
            with tarfile.open(fileobj=compressed, mode="w") as bundle:
                for path in _bundle_files():
                    relative = path.relative_to(PACKAGE_ROOT).as_posix()
                    info = tarfile.TarInfo(name=f"{slug}/{relative}")
                    payload = path.read_bytes()
                    info.size = len(payload)
                    info.mode = 0o755 if path.stat().st_mode & 0o111 else 0o644
                    info.mtime = 0
                    info.uid = 0
                    info.gid = 0
                    info.uname = ""
                    info.gname = ""
                    bundle.addfile(info, io.BytesIO(payload))

    digest = hashlib.sha256(archive.read_bytes()).hexdigest()
    archive.with_suffix(archive.suffix + ".sha256").write_text(
        f"{digest}  {archive.name}\n", encoding="utf-8"
    )
    return archive, digest


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out-dir", type=Path, required=True)
    args = parser.parse_args()
    archive, digest = build_archive(args.out_dir)
    print(f"built {archive}")
    print(f"sha256 {digest}")


if __name__ == "__main__":
    main()
