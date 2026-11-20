"""Pack and admit package trees for portable App archives (v2)."""

from __future__ import annotations

import asyncio
import hashlib
import logging
import os
import shutil
from pathlib import Path
from typing import Any

from app.api.errors import BadRequestError
from app.schemas.portable_transfer import PackagePayload
from app.utils.time import utc_now_iso

logger = logging.getLogger(__name__)

_EXCLUDE_DIRS = frozenset(
    {
        "__pycache__",
        ".venv",
        "venv",
        ".git",
        "dist",
        ".pytest_cache",
        "node_modules",
        ".mypy_cache",
        ".ruff_cache",
    }
)
_EXCLUDE_NAMES = frozenset({".DS_Store", "Thumbs.db"})


def member_prefix_for_slug(slug: str) -> str:
    """ZIP member prefix for an admitted package tree: ``package/<slug>/``."""
    return f"package/{slug}/"


def _unsafe_member(name: str) -> bool:
    return "\\" in name or any(part in ("", ".", "..") for part in name.split("/"))


def fingerprint_members(files: dict[str, bytes], prefix: str) -> str:
    """Match ``compute_bundle_fingerprint`` over in-archive package members."""
    entries: list[str] = []
    for name in sorted(files):
        if not name.startswith(prefix) or name.endswith("/"):
            continue
        rel = name[len(prefix) :]
        if not rel or rel == "signature.bin" or "__pycache__" in rel.split("/"):
            continue
        data = files[name]
        entries.append(f"{rel}\x00{hashlib.sha256(data).hexdigest()}\x00{len(data)}")
    return hashlib.sha256("\n".join(entries).encode()).hexdigest()


def pack_bundle_dir(bundle_dir: Path, slug: str) -> tuple[dict[str, bytes], str]:
    """Copy an admitted bundle into archive members under ``package/<slug>/``."""
    root = bundle_dir.resolve()
    if not root.is_dir() or not (root / "operational-model.yaml").is_file():
        raise BadRequestError(
            message="Packaged App export requires its installed package on disk"
        )
    prefix = member_prefix_for_slug(slug)
    files: dict[str, bytes] = {}
    for path in sorted(root.rglob("*")):
        if not path.is_file():
            continue
        rel = path.relative_to(root)
        if any(part in _EXCLUDE_DIRS for part in rel.parts):
            continue
        if rel.name in _EXCLUDE_NAMES:
            continue
        files[prefix + rel.as_posix()] = path.read_bytes()
    if f"{prefix}operational-model.yaml" not in files:
        raise BadRequestError(
            message="Packaged App export requires operational-model.yaml in the bundle"
        )
    return files, fingerprint_members(files, prefix)


def validate_package_members(payload: PackagePayload, files: dict[str, bytes]) -> None:
    """Ensure package members are present, scoped, and fingerprint-stable."""
    prefix = payload.member_prefix
    expected = member_prefix_for_slug(payload.slug)
    if prefix != expected:
        raise BadRequestError(message="Package member prefix does not match slug")
    package_files = {
        name: data for name, data in files.items() if name.startswith(prefix)
    }
    if not package_files:
        raise BadRequestError(message="Packaged archive is missing package files")
    if any(_unsafe_member(name) for name in package_files):
        raise BadRequestError(message="Invalid package member path")
    if f"{prefix}operational-model.yaml" not in package_files:
        raise BadRequestError(
            message="Packaged archive is missing operational-model.yaml"
        )
    actual = fingerprint_members(files, prefix)
    if actual != payload.fingerprint:
        raise BadRequestError(
            message="Package fingerprint does not match archive contents"
        )


def write_package_tree(payload: PackagePayload, files: dict[str, bytes]) -> Path:
    """Materialize package members under ``packages/apps/<slug>/`` (not Core seeds)."""
    from app.services.operational_model_loader import compute_bundle_fingerprint
    from app.services.package_paths import admitted_packages_root

    validate_package_members(payload, files)
    prefix = payload.member_prefix
    packages_root = admitted_packages_root()
    packages_root.mkdir(parents=True, exist_ok=True)
    dest = (packages_root / payload.slug).resolve()
    if not str(dest).startswith(str(packages_root) + os.sep):
        raise BadRequestError(message="Invalid package destination")
    if dest.is_dir() and (dest / "operational-model.yaml").is_file():
        existing = compute_bundle_fingerprint(dest)
        if existing == payload.fingerprint:
            return dest
        shutil.rmtree(dest)
    dest.mkdir(parents=True, exist_ok=True)
    try:
        for name, data in files.items():
            if not name.startswith(prefix):
                continue
            rel = name[len(prefix) :]
            if (
                not rel
                or _unsafe_member(name)
                or any(part in _EXCLUDE_DIRS for part in Path(rel).parts)
            ):
                raise BadRequestError(message="Invalid package member path")
            target = (dest / rel).resolve()
            if not str(target).startswith(str(dest) + os.sep) and target != dest:
                raise BadRequestError(message="Invalid package member path")
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(data)
        actual = compute_bundle_fingerprint(dest)
        if actual != payload.fingerprint:
            raise BadRequestError(
                message="Package fingerprint does not match after extract"
            )
    except Exception:
        if dest.exists():
            shutil.rmtree(dest, ignore_errors=True)
        raise
    return dest


async def admit_package_from_archive(
    payload: PackagePayload, files: dict[str, bytes]
) -> Any:
    """Write package bytes, load the bundle, upsert library OM, return the library row."""
    from app.models.nodes import (
        OPERATIONAL_MODELS_REGISTRY_ID,
        OperationalModel,
        OperationalModels,
    )
    from app.services.app_graph import ensure_catalog_edge
    from app.services.operational_model_library_seed import (
        upsert_seeded_library_packages,
    )
    from app.services.operational_model_loader import (
        load_library_operational_models_with_issues,
    )
    from app.services.package_trust import assert_library_artifact_trusted

    bundle_dir = await asyncio.to_thread(write_package_tree, payload, files)
    specs, issues = await asyncio.to_thread(
        load_library_operational_models_with_issues,
        package_paths=[bundle_dir.parent],
        verify_signatures=True,
        core_only=False,
    )
    matched = [spec for spec in specs if spec.slug == payload.slug]
    if not matched:
        detail = (
            "; ".join(
                f"{issue.slug}:{issue.code}"
                for issue in issues
                if issue.slug == payload.slug
            )
            or "bundle failed library admission"
        )
        raise BadRequestError(
            message=f"Imported package could not be admitted ({detail})"
        )
    spec = matched[0]
    if spec.bundle_fingerprint != payload.fingerprint:
        raise BadRequestError(
            message="Admitted package fingerprint does not match the archive"
        )
    registry = await OperationalModels.get(OPERATIONAL_MODELS_REGISTRY_ID)
    if registry is None:
        raise BadRequestError(message="Library catalog is unavailable")
    await upsert_seeded_library_packages(
        catalog_registry=registry,
        specs=[spec],
        now_iso=utc_now_iso(),
        ensure_catalog_edge=ensure_catalog_edge,
    )
    rows = await OperationalModel.find({"context.metadata.slug": payload.slug})
    library = next(
        (row for row in rows if getattr(row, "library_package", False)), None
    )
    if library is None:
        raise BadRequestError(message="Library package was not registered after import")
    await asyncio.to_thread(assert_library_artifact_trusted, library)
    return library


def package_meta_from_bundle(bundle_dir: Path, slug: str) -> dict[str, str]:
    """Read version hints from an on-disk bundle for export."""
    version = ""
    try:
        import yaml

        raw = (
            yaml.safe_load(
                (bundle_dir / "operational-model.yaml").read_text(encoding="utf-8")
            )
            or {}
        )
        pkg = raw.get("package") or {}
        version = str(pkg.get("version") or "")
        yaml_slug = str(pkg.get("slug") or pkg.get("name") or "").strip()
        if yaml_slug and yaml_slug != slug:
            raise BadRequestError(
                message="Installed package slug does not match the bundle directory"
            )
    except BadRequestError:
        raise
    except Exception as exc:
        raise BadRequestError(
            message="Could not read package metadata for export"
        ) from exc
    return {"slug": slug, "version": version}
