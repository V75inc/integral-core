"""Pack and admit package trees for portable App archives (v2)."""

from __future__ import annotations

import asyncio
import hashlib
from pathlib import Path
from typing import Any

from app.api.errors import BadRequestError
from app.schemas.portable_transfer import PackagePayload

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
    for name in sorted(files, key=lambda name: Path(name).parts):
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
        if path.is_symlink():
            raise BadRequestError(
                message="Package export requires a self-contained artifact without links"
            )
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


async def resolve_admitted_package(
    payload: PackagePayload,
    files: dict[str, bytes],
    *,
    user_id: str,
    workspace_id: str,
) -> Any:
    """Resolve an exact, host-admitted artifact without publishing archive bytes.

    Workspace creation rights never authorize global package publication. Package
    admission is an operator workflow; restore only consumes its durable catalog
    provenance and existing source tree. In particular, dev-mode signature success
    on bytes supplied by a caller is not an admission decision.
    """
    from app.models.nodes import OperationalModel
    from app.services.library_package_visibility import visible_library_model
    from app.services.operational_model_loader import compute_bundle_fingerprint
    from app.services.package_paths import resolve_bundle_dir_for_slug
    from app.services.package_trust import assert_library_artifact_trusted

    validate_package_members(payload, files)
    candidates = await OperationalModel.find(
        {"context.metadata.slug": payload.slug, "context.library_package": True}
    )
    admitted = []
    for candidate in candidates:
        current = await visible_library_model(
            candidate.id, workspace_id, user_id=user_id
        )
        if current is not None:
            admitted.append(current)
    if len(admitted) != 1:
        raise BadRequestError(
            message="Import requires one matching package admitted by the destination host"
        )
    library = admitted[0]
    metadata = dict(library.metadata or {})
    await asyncio.to_thread(assert_library_artifact_trusted, library)
    recorded = metadata.get("bundle_dir_path") or metadata.get("bundle_dir") or ""
    root = Path(recorded) if recorded else None
    # Require the same artifact to be discoverable after a supported cold boot.
    discovered = await asyncio.to_thread(resolve_bundle_dir_for_slug, payload.slug)
    if (
        root is None
        or not root.is_dir()
        or not discovered
        or Path(discovered).resolve() != root.resolve()
        or metadata.get("bundle_fingerprint") != payload.fingerprint
        or await asyncio.to_thread(compute_bundle_fingerprint, root)
        != payload.fingerprint
    ):
        raise BadRequestError(
            message="Import requires the exact admitted package on the host's configured package path"
        )
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
