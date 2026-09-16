"""Unified package-root resolution for library App bundles (F0).

All disk walks for ``profile.yaml`` packages MUST go through this module —
not scattered ``_PROFILES_ROOT`` copies. Roots come from:

1. Explicit ``profiles_root`` / ``package_paths`` call arguments (tests)
2. ``INTEGRAL_PACKAGE_PATHS`` (comma-separated absolute or repo-relative paths)
3. Default: ``backend/app/profiles/``

When ``INTEGRAL_CORE_ONLY`` is set, only packages whose ``package.class`` is
``core_package`` (or the well-known core-seed slugs when class is absent)
are loaded into the library catalog.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Iterable, List, Optional, Sequence

# Package class taxonomy (FOUNDATION_EXTENSION_SAAS.md).
PACKAGE_CLASSES = frozenset(
    {
        "core_package",
        "community_app",
        "verified_app",
        "commercial_app",
        "private_org_app",
    }
)

# Slugs treated as core_package when manifest omits package.class.
# personal-context is commercial (loaded via INTEGRAL_PACKAGE_PATHS).
CORE_SEED_SLUGS = frozenset({"agent-scratch"})

_DEFAULT_PROFILES = Path(__file__).resolve().parent.parent / "profiles"
_REPO_ROOT = Path(__file__).resolve().parents[3]


def _truthy(value: Optional[str]) -> bool:
    if value is None:
        return False
    return value.strip().lower() in {"1", "true", "yes", "on"}


def is_core_only_mode() -> bool:
    """True when Core must boot without domain App library sync."""
    try:
        from app.config import settings

        if getattr(settings, "INTEGRAL_CORE_ONLY", False):
            return True
    except Exception:  # noqa: BLE001
        pass
    return _truthy(os.environ.get("INTEGRAL_CORE_ONLY"))


def resolve_package_class(
    *,
    slug: str,
    declared: Optional[str] = None,
) -> str:
    """Normalize package.class; default community_app; core seeds → core_package."""
    raw = (declared or "").strip()
    if raw in PACKAGE_CLASSES:
        return raw
    if slug in CORE_SEED_SLUGS:
        return "core_package"
    return "community_app"


def should_include_package(
    *,
    slug: str,
    package_class: str,
    core_only: Optional[bool] = None,
) -> bool:
    """Filter packages for library sync / catalog under core-only mode."""
    if core_only is None:
        core_only = is_core_only_mode()
    if not core_only:
        return True
    return package_class == "core_package" or slug in CORE_SEED_SLUGS


def default_profiles_root() -> Path:
    """Legacy single-root path (first resolved package path)."""
    paths = resolve_package_paths()
    return paths[0] if paths else _DEFAULT_PROFILES


def resolve_package_paths(
    package_paths: Optional[Sequence[str | Path]] = None,
) -> List[Path]:
    """Return ordered absolute package roots to walk for profile.yaml.

    Empty / missing roots are omitted. Deduplicates while preserving order.
    """
    if package_paths is not None:
        candidates = [Path(p) for p in package_paths if str(p).strip()]
    else:
        env = os.environ.get("INTEGRAL_PACKAGE_PATHS", "").strip()
        if not env:
            try:
                from app.config import settings

                env = (getattr(settings, "INTEGRAL_PACKAGE_PATHS", None) or "").strip()
            except Exception:  # noqa: BLE001
                env = ""
        if env:
            candidates = [Path(part.strip()) for part in env.split(",") if part.strip()]
        else:
            # Core default: seeds under app/profiles/. Commercial monorepo also
            # ships Apps at packages/apps/ — include when present so product
            # boots without forcing every shell to export INTEGRAL_PACKAGE_PATHS.
            # Core-only mode still filters to core_package via should_include_package.
            candidates = [_DEFAULT_PROFILES]
            apps_root = _REPO_ROOT / "packages" / "apps"
            if apps_root.is_dir():
                candidates.append(apps_root)

    resolved: List[Path] = []
    seen: set[str] = set()
    for raw in candidates:
        path = raw.expanduser()
        if not path.is_absolute():
            # Prefer repo-root relative, then cwd.
            for base in (_REPO_ROOT, Path.cwd()):
                candidate = (base / path).resolve()
                if candidate.exists() or base == Path.cwd():
                    path = candidate
                    break
            else:
                path = path.resolve()
        else:
            path = path.resolve()
        key = str(path)
        if key in seen:
            continue
        seen.add(key)
        resolved.append(path)
    # Always keep Core seeds root even when env lists only packages/apps.
    if str(_DEFAULT_PROFILES.resolve()) not in seen and _DEFAULT_PROFILES.exists():
        resolved.insert(0, _DEFAULT_PROFILES.resolve())
    return resolved or [_DEFAULT_PROFILES]


def iter_profile_yaml_paths(
    package_paths: Optional[Sequence[str | Path]] = None,
) -> Iterable[Path]:
    """Yield ``*/profile.yaml`` under each configured package root."""
    for root in resolve_package_paths(package_paths):
        if not root.exists():
            continue
        yield from sorted(root.glob("*/profile.yaml"))


# Back-compat alias used by modules that previously imported _PROFILES_ROOT.
def get_profiles_root() -> Path:
    """Return the primary package root (Core seeds / first configured path)."""
    return default_profiles_root()
