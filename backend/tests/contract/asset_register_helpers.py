"""Shared helpers for asset-register contract tests."""

from __future__ import annotations

import copy
from dataclasses import replace
from pathlib import Path
from typing import Any, Dict

from app.models.nodes import OperationalModel
from app.services.operational_model_loader import (
    load_library_operational_models_with_issues,
)
from tests.fixtures.library_models import publish_library_spec

REPO = Path(__file__).resolve().parents[3]
ASSET_APP = REPO / "examples" / "asset-register"


def load_asset_register_spec(*, bundle_dir: Path | None = None):
    bundle_dir = bundle_dir or ASSET_APP
    specs, _ = load_library_operational_models_with_issues(
        package_paths=[str(bundle_dir.parent)],
        core_only=False,
        verify_signatures=False,
    )
    return next(s for s in specs if s.slug == "asset-register")


async def seed_asset_register_library_cp(
    *,
    version: str = "1.0.0",
    manifest_override: Dict[str, Any] | None = None,
    bundle_dir: Path | None = None,
) -> OperationalModel:
    spec = load_asset_register_spec(bundle_dir=bundle_dir)
    manifest = copy.deepcopy(spec.manifest)
    if manifest_override:
        manifest.update(manifest_override)
    manifest.setdefault("package", {})
    manifest["package"]["version"] = version
    return await publish_library_spec(
        replace(
            spec,
            manifest=manifest,
            version=version,
            bundle_fingerprint=spec.bundle_fingerprint or "test-fp",
            bundle_dir=spec.bundle_dir or bundle_dir or ASSET_APP,
        )
    )
