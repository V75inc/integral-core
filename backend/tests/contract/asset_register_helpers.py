"""Shared helpers for asset-register contract tests."""

from __future__ import annotations

import copy
from pathlib import Path
from typing import Any, Dict

from app.models.nodes import ContentProfile
from app.services.content_profile_loader import load_library_profiles_with_issues
from app.utils.time import utc_now_iso

REPO = Path(__file__).resolve().parents[3]
ASSET_APP = REPO / "examples" / "asset-register"


def load_asset_register_spec():
    specs, _ = load_library_profiles_with_issues(
        package_paths=[str(ASSET_APP.parent)],
        core_only=False,
        verify_signatures=False,
    )
    return next(s for s in specs if s.slug == "asset-register")


async def seed_asset_register_library_cp(
    *,
    version: str = "1.0.0",
    manifest_override: Dict[str, Any] | None = None,
) -> ContentProfile:
    spec = load_asset_register_spec()
    manifest = copy.deepcopy(spec.manifest)
    if manifest_override:
        manifest.update(manifest_override)
    manifest.setdefault("package", {})
    manifest["package"]["version"] = version
    now = utc_now_iso()
    return await ContentProfile.create(
        name=spec.name or "Asset Register",
        scope="app",
        manifest=manifest,
        library_package=True,
        version=version,
        metadata={
            "slug": spec.slug,
            "bundle_fingerprint": getattr(spec, "bundle_fingerprint", "") or "test-fp",
            "package_class": spec.package_class,
            "bundle_dir": str(spec.bundle_dir) if spec.bundle_dir else str(ASSET_APP),
        },
        created_at=now,
        updated_at=now,
    )
