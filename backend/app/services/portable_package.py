"""Pack and admit package trees for portable App archives (v2)."""

from __future__ import annotations

import asyncio
import hashlib
import os
import shutil
from pathlib import Path
from typing import Any, Optional

from app.api.errors import BadRequestError
from app.schemas.portable_transfer import PackagePayload
from app.services.operational_model_loader import (
    BUNDLE_EXCLUDE_DIRS,
    BUNDLE_EXCLUDE_NAMES,
    skip_bundle_rel,
)

# Re-export shared ignores for callers/tests that imported these names.
_EXCLUDE_DIRS = BUNDLE_EXCLUDE_DIRS
_EXCLUDE_NAMES = BUNDLE_EXCLUDE_NAMES


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
        if not rel or skip_bundle_rel(rel, for_fingerprint=True):
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
        if skip_bundle_rel(rel, for_fingerprint=False):
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


def _env_package_roots() -> list[Path]:
    env = os.environ.get("INTEGRAL_PACKAGE_PATHS", "").strip()
    if not env:
        try:
            from app.config import settings

            env = (getattr(settings, "INTEGRAL_PACKAGE_PATHS", None) or "").strip()
        except Exception:  # noqa: BLE001
            env = ""
    roots: list[Path] = []
    for part in env.split(",") if env else []:
        raw = part.strip()
        if not raw:
            continue
        path = Path(raw).expanduser()
        roots.append(
            path.resolve() if path.is_absolute() else (Path.cwd() / path).resolve()
        )
    return roots


def select_import_package_root(slug: str) -> Path:
    """Choose a writable host package root for materializing ``slug``."""
    from app.services.package_paths import resolve_package_paths

    preferred = _env_package_roots()
    ordered: list[Path] = []
    seen: set[str] = set()
    for root in preferred + resolve_package_paths():
        key = str(root.resolve()) if root.exists() else str(root)
        if key in seen:
            continue
        seen.add(key)
        ordered.append(root)

    for root in ordered:
        target = root / slug
        if target.is_dir() and (target / "operational-model.yaml").is_file():
            return root.resolve() if root.exists() else root
        try:
            root.mkdir(parents=True, exist_ok=True)
            probe = root / ".integral-portable-write-probe"
            probe.write_text("", encoding="utf-8")
            probe.unlink(missing_ok=True)
            return root.resolve()
        except OSError:
            continue
    raise BadRequestError(
        message=(
            "Import requires a writable host package path "
            "(configure INTEGRAL_PACKAGE_PATHS or integral up --apps)"
        )
    )


def materialize_package_members(
    payload: PackagePayload, files: dict[str, bytes], dest: Path
) -> None:
    """Write archive ``package/<slug>/`` members into ``dest`` (new directory)."""
    if dest.exists():
        raise BadRequestError(
            message="Import cannot overwrite an existing host package directory"
        )
    prefix = payload.member_prefix
    staged = dest.with_name(dest.name + ".portable-staging")
    if staged.exists():
        shutil.rmtree(staged)
    staged.mkdir(parents=True)
    try:
        wrote = False
        for name, data in files.items():
            if not name.startswith(prefix) or name.endswith("/"):
                continue
            rel = name[len(prefix) :]
            if not rel or _unsafe_member(rel):
                raise BadRequestError(message="Invalid package member path")
            rel_path = Path(rel)
            if skip_bundle_rel(rel_path, for_fingerprint=False):
                continue
            out = staged / rel_path
            out.parent.mkdir(parents=True, exist_ok=True)
            out.write_bytes(data)
            wrote = True
        if not wrote or not (staged / "operational-model.yaml").is_file():
            raise BadRequestError(
                message="Packaged archive is missing operational-model.yaml"
            )
        staged.rename(dest)
    except BaseException:
        if staged.exists():
            shutil.rmtree(staged, ignore_errors=True)
        raise


async def _seed_library_from_bundle(bundle_dir: Path, slug: str) -> Any:
    """Load one on-disk bundle and upsert it into the library catalog.

    Portable archive admission is an explicit restore path: load with
    ``core_only=False`` so host ``INTEGRAL_CORE_ONLY`` ambient filtering does
    not block packages already present in the ZIP. Boot-time library sync is
    unchanged.
    """
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
    from app.utils.time import utc_now_iso

    def _load():
        return load_library_operational_models_with_issues(
            package_paths=[bundle_dir.parent],
            verify_signatures=True,
            core_only=False,
        )

    specs, issues = await asyncio.to_thread(_load)
    matched = [spec for spec in specs if spec.slug == slug]
    if not matched:
        detail = next(
            (issue.message for issue in issues if issue.slug == slug),
            "package could not be loaded from the archive",
        )
        raise BadRequestError(
            message=f"Import could not admit package {slug!r}: {detail}"
        )
    spec = matched[0]
    registry = await OperationalModels.get(OPERATIONAL_MODELS_REGISTRY_ID)
    await upsert_seeded_library_packages(
        catalog_registry=registry,
        specs=[spec],
        now_iso=utc_now_iso(),
        ensure_catalog_edge=ensure_catalog_edge,
    )
    rows = await OperationalModel.find(
        {"context.metadata.slug": slug, "context.library_package": True}
    )
    for row in rows:
        metadata = dict(row.metadata or {})
        recorded = metadata.get("bundle_dir_path") or metadata.get("bundle_dir") or ""
        if recorded and Path(recorded).resolve() == bundle_dir.resolve():
            return row
    if len(rows) == 1:
        return rows[0]
    raise BadRequestError(
        message="Import requires one matching package admitted by the destination host"
    )


async def _visible_admissions(
    *, slug: str, workspace_id: str, user_id: str
) -> list[Any]:
    from app.models.nodes import OperationalModel
    from app.services.library_package_visibility import visible_library_model

    candidates = await OperationalModel.find(
        {"context.metadata.slug": slug, "context.library_package": True}
    )
    admitted = []
    for candidate in candidates:
        current = await visible_library_model(
            candidate.id, workspace_id, user_id=user_id
        )
        if current is not None:
            admitted.append(current)
    return admitted


def _bundle_matches_payload(bundle_dir: Path, fingerprint: str) -> bool:
    from app.services.operational_model_loader import compute_bundle_fingerprint

    return (
        bundle_dir.is_dir()
        and (bundle_dir / "operational-model.yaml").is_file()
        and compute_bundle_fingerprint(bundle_dir) == fingerprint
    )


async def _materialize_and_admit(
    payload: PackagePayload,
    files: dict[str, bytes],
    *,
    created_bundle_dirs: Optional[list[Path]] = None,
) -> Any:
    """Admit a missing package by materializing archive members onto the host."""
    from app.services.operational_model_loader import compute_bundle_fingerprint
    from app.services.package_paths import resolve_bundle_dir_for_slug

    discovered = await asyncio.to_thread(resolve_bundle_dir_for_slug, payload.slug)
    if discovered:
        existing = Path(discovered).resolve()
        if not await asyncio.to_thread(
            _bundle_matches_payload, existing, payload.fingerprint
        ):
            raise BadRequestError(
                message=(
                    "Import requires the exact admitted package on the host's "
                    "configured package path"
                )
            )
        return await _seed_library_from_bundle(existing, payload.slug)

    root = await asyncio.to_thread(select_import_package_root, payload.slug)
    dest = (root / payload.slug).resolve()
    if dest.exists():
        if not await asyncio.to_thread(
            _bundle_matches_payload, dest, payload.fingerprint
        ):
            raise BadRequestError(
                message=(
                    "Import requires the exact admitted package on the host's "
                    "configured package path"
                )
            )
        return await _seed_library_from_bundle(dest, payload.slug)

    await asyncio.to_thread(materialize_package_members, payload, files, dest)
    if created_bundle_dirs is not None:
        created_bundle_dirs.append(dest)
    try:
        actual = await asyncio.to_thread(compute_bundle_fingerprint, dest)
        if actual != payload.fingerprint:
            raise BadRequestError(
                message="Package fingerprint does not match archive contents"
            )
        library = await _seed_library_from_bundle(dest, payload.slug)
    except BaseException:
        if dest.exists():
            shutil.rmtree(dest, ignore_errors=True)
        if created_bundle_dirs is not None and dest in created_bundle_dirs:
            created_bundle_dirs.remove(dest)
        raise
    return library


async def resolve_admitted_package(
    payload: PackagePayload,
    files: dict[str, bytes],
    *,
    user_id: str,
    workspace_id: str,
    created_bundle_dirs: Optional[list[Path]] = None,
) -> Any:
    """Resolve a host-admitted package, materializing from the archive when missing.

    When the destination already has exactly one visible library package for the
    slug, restore binds to that artifact and never rewrites it. When none is
    admitted, archive ``package/<slug>/`` members are written under a configured
    package root and seeded into the library catalog. Ambiguous or conflicting
    host packages remain fail-closed.
    """
    from app.services.package_trust import assert_library_artifact_trusted

    validate_package_members(payload, files)
    admitted = await _visible_admissions(
        slug=payload.slug, workspace_id=workspace_id, user_id=user_id
    )
    if len(admitted) > 1:
        raise BadRequestError(
            message="Import requires one matching package admitted by the destination host"
        )
    if len(admitted) == 0:
        library = await _materialize_and_admit(
            payload, files, created_bundle_dirs=created_bundle_dirs
        )
        await asyncio.to_thread(assert_library_artifact_trusted, library)
        if not await _artifact_matches_payload(library, payload):
            raise BadRequestError(
                message=(
                    "Import requires the exact admitted package on the host's "
                    "configured package path"
                )
            )
        return library

    library = admitted[0]
    await asyncio.to_thread(assert_library_artifact_trusted, library)
    if not await _artifact_matches_payload(library, payload):
        raise BadRequestError(
            message="Import requires the exact admitted package on the host's configured package path"
        )
    return library


async def _artifact_matches_payload(library: Any, payload: PackagePayload) -> bool:
    """True when the library row's on-disk tree matches the archive fingerprint."""
    from app.services.operational_model_loader import compute_bundle_fingerprint
    from app.services.package_paths import resolve_bundle_dir_for_slug

    metadata = dict(library.metadata or {})
    recorded = metadata.get("bundle_dir_path") or metadata.get("bundle_dir") or ""
    root = Path(recorded) if recorded else None
    discovered = await asyncio.to_thread(resolve_bundle_dir_for_slug, payload.slug)
    if (
        root is None
        or not root.is_dir()
        or not discovered
        or Path(discovered).resolve() != root.resolve()
    ):
        return False
    current = await asyncio.to_thread(compute_bundle_fingerprint, root)
    if current != payload.fingerprint:
        return False
    stored = str(metadata.get("bundle_fingerprint") or "")
    if not stored:
        return True
    if stored == current:
        return True
    legacy = await asyncio.to_thread(compute_bundle_fingerprint, root, legacy=True)
    return stored == legacy


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
