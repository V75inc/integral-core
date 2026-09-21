"""YAML-based library Operational Model loader (v3 — Phase B2).

Walks ``backend/app/packages/*/operational-model.yaml`` and returns
``LibraryProfileSpec`` instances for upserting into the graph at startup.

v3 additions (Spec §4.2):
  - I-BUNDLE-02 validation: each skill key in manifest must have skills/<key>/SKILL.md
  - I-BUNDLE-04 validation: dir name MUST match package.slug
  - Bundle fingerprint (sha256 over sorted file manifest, excludes signature.bin)
  - Signature gate via operational_model_signature.verify_bundle_signature
  - LibraryProfileSpec carries bundle_dir, skill_keys, ships_python,
    signature_path, signature_verified, signature_reason,
    manifest_fingerprint, bundle_fingerprint

Backward-compat: the existing v2 callers continue to function — the loader
function name and core return shape are unchanged; new fields default to
empty/None. The emitted ``manifest.operational_model_schema_version`` is pinned
at ``2`` so the v2 canonical-manifest validator keeps accepting bundles
regardless of whether the YAML declares ``integral_operational_model_version: 2`` or
``3``; the bundle's v3-ness lives on ``LibraryProfileSpec`` fields.
"""

from __future__ import annotations

import hashlib
import logging
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence

import yaml

logger = logging.getLogger(__name__)


def _default_packages_root() -> Path:
    from app.services.package_paths import default_packages_root

    return default_packages_root()


# Absolute path to the first configured package root (default: app/packages/).
# Tests may monkeypatch this module attribute; prefer package_paths.resolve_*.
_PROFILES_ROOT = Path(__file__).parent.parent / "packages"


@dataclass(frozen=True)
class LibraryProfileSpec:
    """One library package loaded from a operational-model.yaml file (v3)."""

    name: str
    version: str
    description: str
    manifest: Dict[str, Any]
    slug: str = ""  # populated by loader; empty for backward-compat callers
    scope: str = "platform"
    library_package: bool = True
    bundle_dir: Optional[Path] = None
    skill_keys: List[str] = field(default_factory=list)
    ships_python: bool = False
    signature_path: Optional[Path] = None
    signature_verified: bool = True
    signature_reason: str = "dev_mode"
    manifest_fingerprint: str = ""
    bundle_fingerprint: str = ""
    # F0 — package class taxonomy (FOUNDATION_EXTENSION_SAAS.md).
    package_class: str = "community_app"


@dataclass(frozen=True)
class ProfileLoadIssue:
    """Structured diagnostics for bundle load failures/skips."""

    slug: str
    code: str
    message: str
    level: str = "error"
    model_path: str = ""


def compute_bundle_fingerprint(bundle_dir: Path) -> str:
    """SHA-256 over sorted file manifest. Stable across machines.

    Each entry: ``<rel_path>\\0<sha256_hex>\\0<size>``. signature.bin
    is excluded so the fingerprint doesn't change when the bundle is
    re-signed.
    """
    entries: List[str] = []
    for p in sorted(bundle_dir.rglob("*")):
        if not p.is_file():
            continue
        rel = p.relative_to(bundle_dir).as_posix()
        if rel == "signature.bin":
            continue
        data = p.read_bytes()
        entries.append(f"{rel}\x00{hashlib.sha256(data).hexdigest()}\x00{len(data)}")
    return hashlib.sha256("\n".join(entries).encode()).hexdigest()


def _expand_bare_skill_keys(tier: Dict[str, Any]) -> Dict[str, Any]:
    """If ``tier['skills']`` is a v3 bare-string list, expand it to v2 dict form.

    v3 bundles declare ``track.skills`` / ``app.skills`` as a list of skill
    key strings (e.g. ``[k1, k2]``). The actual skill body lives in
    ``skills/<key>/SKILL.md`` under the bundle dir, surfaced separately via
    ``LibraryProfileSpec.skill_keys``. But the v2 canonical-manifest validator
    expects each entry to be a dict-shaped object AND uses the declared keys
    to validate cross-references from ``app.agents[].skills`` and
    ``default_schedules[].skill``. We synthesize a minimal declarative-shape
    dict per bare-string key so both gates pass — keeping the stored manifest
    a valid v2 shape while preserving the v3 file layout on disk.

    Dict-shaped skill blocks (legacy / v2) pass through unchanged. Returns
    a shallow-cloned ``tier`` dict; the caller swaps it back into the
    manifest. Mixed lists (some strings, some dicts) are left as-is — that
    shape is invalid and the v2 validator will report it clearly.
    """
    sk = tier.get("skills")
    if not (isinstance(sk, list) and sk and all(isinstance(x, str) for x in sk)):
        return tier
    out = dict(tier)
    out["skills"] = [
        {
            "key": k,
            "name": k,
            "kind": "declarative",
            "prompt_template": f"skills/{k}/SKILL.md",
        }
        for k in sk
    ]
    return out


def _assemble_manifest(raw: Dict[str, Any]) -> Dict[str, Any]:
    """Reconstruct the manifest dict from a parsed operational-model.yaml.

    The manifest package block uses ``name`` as the slug identifier (matching
    the old Python dict convention where ``package.name`` was the slug). The
    human display name lives on ``LibraryProfileSpec.name`` instead.

    The emitted ``operational_model_schema_version`` stays pinned at ``2`` so
    the existing canonical-manifest validator (``_canonical_manifest_base``)
    accepts it regardless of whether the YAML declares
    ``integral_operational_model_version: 2`` (legacy) or ``3`` (Phase B bundles). The
    v3-ness of the bundle is carried on ``LibraryProfileSpec`` fields
    (``bundle_dir``, ``skill_keys``, ``ships_python``, signature/fingerprint
    metadata) — not via the manifest's internal schema version, which is a
    runtime contract.

    v3 normalization (Phase B4): bare-string skill keys in
    ``track.skills`` / ``app.skills`` are expanded to minimal v2 dict shape
    so the stored manifest is directly compilable by ``compile_canonical_manifest``.
    """
    scope = str(raw.get("scope", "track"))
    pkg_raw = raw.get("package") or {}
    slug = str(pkg_raw.get("slug") or "")
    pkg: Dict[str, Any] = {"name": slug} if slug else {}
    for k, v in pkg_raw.items():
        if k not in ("name", "slug", "version", "description"):
            pkg[k] = v

    manifest: Dict[str, Any] = {
        "operational_model_schema_version": 2,
        "scope": scope,
    }
    if pkg:
        manifest["package"] = pkg

    # Copy scope body, expanding v3 bare-string skill lists to v2 dict shape.
    for key in ("track", "app", "workspace"):
        if key in raw:
            section = raw[key]
            if isinstance(section, dict):
                section = _expand_bare_skill_keys(section)
            manifest[key] = section

    # Copy optional extension sections
    for key in ("migrations", "field_types", "view_types", "plugins"):
        if key in raw:
            manifest[key] = raw[key]

    return manifest


def _declared_skill_keys(manifest: Dict[str, Any]) -> List[str]:
    """Collect skill keys declared at the manifest's scope tier.

    Tolerates both v3 bare-string entries (``skills: [k1, k2]``) and v2
    dict-shape entries (``skills: [{key: k1, ...}]``). ``_assemble_manifest``
    expands bare strings to dict shape before the manifest reaches here, but
    keep the dual handling so callers passing un-normalized manifests still
    work.
    """
    out: List[str] = []
    for scope_key in ("track", "app"):
        tier = manifest.get(scope_key) or {}
        if not isinstance(tier, dict):
            continue
        for entry in tier.get("skills") or []:
            if isinstance(entry, str) and entry.strip():
                out.append(entry.strip())
            elif isinstance(entry, dict):
                k = str(entry.get("key") or "").strip()
                if k:
                    out.append(k)
    # workspace-scope: skills come via nested app sub-manifests; not enumerated here.
    return out


def _validate_skill_dirs(
    bundle_dir: Path,
    declared: List[str],
    *,
    issues: Optional[List[ProfileLoadIssue]] = None,
    model_path: str = "",
) -> List[str]:
    """Return only skills with skills/<key>/SKILL.md present (I-BUNDLE-02)."""
    out: List[str] = []
    for k in declared:
        if (bundle_dir / "skills" / k / "SKILL.md").exists():
            out.append(k)
        else:
            logger.warning(
                "bundle %s declares skill '%s' but skills/%s/SKILL.md missing — excluding",
                bundle_dir.name,
                k,
                k,
            )
            if issues is not None:
                _issue(
                    issues,
                    slug=bundle_dir.name,
                    code="missing_skill_markdown",
                    message=f"skills/{k}/SKILL.md missing (skill excluded)",
                    level="warning",
                    model_path=model_path,
                )
    return out


def _issue(
    issues: List[ProfileLoadIssue],
    *,
    slug: str,
    code: str,
    message: str,
    level: str = "error",
    model_path: str = "",
) -> None:
    issues.append(
        ProfileLoadIssue(
            slug=slug,
            code=code,
            message=message,
            level=level,
            model_path=model_path,
        )
    )


def _bundle_ships_python(bundle_dir: Path) -> bool:
    for subdir in ("skills", "tools"):
        base = bundle_dir / subdir
        if not base.exists():
            continue
        for p in base.rglob("*.py"):
            if p.is_file():
                return True
    return False


def load_library_operational_models(
    packages_root: Path | None = None,
    *,
    verify_signatures: bool = True,
    package_paths: Sequence[Path] | None = None,
    core_only: bool | None = None,
) -> Sequence[LibraryProfileSpec]:
    """Load library Operational Model specs, discarding per-package load issues.

    Convenience wrapper around ``load_library_operational_models_with_issues`` for callers
    that only need successfully parsed packages.
    """
    specs, _ = load_library_operational_models_with_issues(
        packages_root=packages_root,
        verify_signatures=verify_signatures,
        package_paths=package_paths,
        core_only=core_only,
    )
    return specs


def load_library_operational_models_with_issues(
    packages_root: Path | None = None,
    *,
    verify_signatures: bool = True,
    package_paths: Sequence[Path] | None = None,
    core_only: bool | None = None,
) -> tuple[Sequence[LibraryProfileSpec], Sequence[ProfileLoadIssue]]:
    """Walk package roots for ``*/operational-model.yaml`` and return one spec per package.

    Packages whose operational-model.yaml cannot be parsed are logged and skipped —
    a bad YAML file must not crash startup.

    v3 gates (Spec §4.2):
      - I-BUNDLE-01: bundles without operational-model.yaml are silently skipped (handled
        by the ``*/operational-model.yaml`` glob).
      - I-BUNDLE-02: declared skill keys whose ``skills/<key>/SKILL.md`` is
        missing are excluded from ``skill_keys`` with a WARNING.
      - I-BUNDLE-04: ``bundle_dir.name`` MUST equal ``package.slug``; mismatch
        skips the entire bundle with an ERROR.
      - Signature gate: when ``verify_signatures`` is on and a pubkey is
        configured via ``INTEGRAL_OPERATIONAL_MODEL_PUBKEY``, a bundle that ``ships_python``
        but fails verification is skipped.

    F0: when ``core_only`` (or ``INTEGRAL_CORE_ONLY``) is set, only
    ``core_package`` artifacts are returned.
    """
    from app.services.package_paths import (
        is_core_only_mode,
        resolve_package_class,
        resolve_package_paths,
        should_include_package,
    )

    if packages_root is not None:
        roots = [Path(packages_root)]
    elif package_paths is not None:
        roots = resolve_package_paths(package_paths)
    else:
        # Honor monkeypatched _PROFILES_ROOT (tests) when it diverges from default.
        default_root = Path(__file__).parent.parent / "packages"
        if _PROFILES_ROOT != default_root:
            roots = [Path(_PROFILES_ROOT)]
        else:
            roots = resolve_package_paths()

    if core_only is None:
        core_only = is_core_only_mode()

    specs: List[LibraryProfileSpec] = []
    issues: List[ProfileLoadIssue] = []
    seen_slugs: set[str] = set()

    existing_roots = [r for r in roots if r.exists()]
    if not existing_roots:
        logger.warning(
            "Operational Model packages roots %s do not exist — no library packages loaded",
            roots,
        )
        _issue(
            issues,
            slug="",
            code="packages_root_missing",
            message=f"Operational Model packages roots {roots} do not exist",
            level="warning",
        )
        return specs, issues

    pubkey = (
        os.environ.get("INTEGRAL_OPERATIONAL_MODEL_PUBKEY")
        if verify_signatures
        else None
    )

    model_paths: List[Path] = []
    for root in existing_roots:
        model_paths.extend(sorted(root.glob("*/operational-model.yaml")))

    for model_path in model_paths:
        bundle_dir = model_path.parent
        try:
            raw = yaml.safe_load(model_path.read_text(encoding="utf-8"))
            if not isinstance(raw, dict):
                raise ValueError("operational-model.yaml must be a YAML mapping")

            pkg = raw.get("package") or {}
            yaml_slug = str(pkg.get("slug") or "")
            dir_slug = bundle_dir.name

            # I-BUNDLE-04: dir name MUST match package.slug (when slug declared)
            if yaml_slug and yaml_slug != dir_slug:
                logger.error(
                    "bundle dir '%s' has package.slug '%s' (I-BUNDLE-04) — skipping",
                    dir_slug,
                    yaml_slug,
                )
                _issue(
                    issues,
                    slug=dir_slug,
                    code="slug_mismatch",
                    message=f"bundle dir '{dir_slug}' does not match package.slug '{yaml_slug}'",
                    model_path=str(model_path),
                )
                continue
            slug = yaml_slug or dir_slug
            if slug in seen_slugs:
                logger.warning(
                    "duplicate package slug %s at %s — skipping later root",
                    slug,
                    model_path,
                )
                _issue(
                    issues,
                    slug=slug,
                    code="duplicate_slug",
                    message=f"duplicate package slug already loaded; skipping {model_path}",
                    level="warning",
                    model_path=str(model_path),
                )
                continue

            pkg_class = resolve_package_class(
                slug=slug,
                declared=str(pkg.get("class") or "") or None,
            )
            if not should_include_package(
                slug=slug, package_class=pkg_class, core_only=core_only
            ):
                logger.debug(
                    "skipping package %s (class=%s) under core_only=%s",
                    slug,
                    pkg_class,
                    core_only,
                )
                continue

            manifest = _assemble_manifest(raw)
            ships_python = _bundle_ships_python(bundle_dir)
            sig_path_obj = bundle_dir / "signature.bin"
            sig_path = sig_path_obj if sig_path_obj.exists() else None

            # I-BUNDLE-03: signature gate (only when verify_signatures is on)
            if verify_signatures:
                from app.services.operational_model_signature import (
                    verify_bundle_signature,
                )

                sig_result = verify_bundle_signature(bundle_dir, pubkey)
            else:
                from app.services.operational_model_signature import SignatureResult

                sig_result = SignatureResult(True, "dev_mode")

            if not sig_result.verified and ships_python:
                logger.error(
                    "bundle %s signature verification failed (%s) and ships python — skipping",
                    slug,
                    sig_result.reason,
                )
                _issue(
                    issues,
                    slug=slug,
                    code="signature_verification_failed",
                    message=(
                        f"signature verification failed ({sig_result.reason}) for python-shipping bundle"
                    ),
                    model_path=str(model_path),
                )
                continue

            declared_keys = _declared_skill_keys(manifest)
            skill_keys = _validate_skill_dirs(
                bundle_dir,
                declared_keys,
                issues=issues,
                model_path=str(model_path),
            )

            from app.services.operational_model_library_seed import (
                canonical_manifest_fingerprint,
            )

            try:
                mfp_manifest = manifest
                mfp = canonical_manifest_fingerprint(mfp_manifest)
            except Exception:
                logger.exception("canonical_manifest_fingerprint failed for %s", slug)
                _issue(
                    issues,
                    slug=slug,
                    code="manifest_fingerprint_failed",
                    message="canonical_manifest_fingerprint failed",
                    model_path=str(model_path),
                )
                continue

            name = str(pkg.get("name") or slug)
            version = str(pkg.get("version") or "1.0.0")
            description = str(pkg.get("description") or "")

            specs.append(
                LibraryProfileSpec(
                    name=name,
                    slug=slug,
                    version=version,
                    description=description,
                    manifest=manifest,
                    scope="platform",
                    library_package=True,
                    bundle_dir=bundle_dir,
                    skill_keys=skill_keys,
                    ships_python=ships_python,
                    signature_path=sig_path,
                    signature_verified=sig_result.verified,
                    signature_reason=sig_result.reason,
                    manifest_fingerprint=mfp,
                    bundle_fingerprint=compute_bundle_fingerprint(bundle_dir),
                    package_class=pkg_class,
                )
            )
            seen_slugs.add(slug)
        except Exception as exc:
            logger.error(
                "Failed to load profile from %s: %s",
                model_path,
                exc,
                exc_info=True,
            )
            _issue(
                issues,
                slug=model_path.parent.name,
                code="profile_load_failed",
                message=str(exc),
                model_path=str(model_path),
            )

    logger.info(
        "Loaded %d library Operational Models from %s (core_only=%s)",
        len(specs),
        existing_roots,
        core_only,
    )
    return specs, issues
