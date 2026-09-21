"""Generic per-bundle post-seed runner (F0).

Core never imports App packages by name. When an install opts into seed data,
this module looks for ``<bundle_dir>/seeds/post_install.py`` with an async
``run(app_node, actor_id) -> int`` and invokes it via importlib.
"""

from __future__ import annotations

import importlib.util
import logging
import sys
from pathlib import Path
from types import ModuleType
from typing import Any, Optional

logger = logging.getLogger(__name__)


def _resolve_bundle_dir(
    *,
    source_profile_slug: Optional[str],
    bundle_dir: Optional[str] = None,
) -> Optional[Path]:
    if bundle_dir:
        path = Path(bundle_dir)
        return path if path.is_dir() else None
    slug = (source_profile_slug or "").strip()
    if not slug:
        return None
    from app.services.package_paths import resolve_package_paths

    for root in resolve_package_paths():
        candidate = root / slug
        if (candidate / "profile.yaml").is_file():
            return candidate
    return None


def _load_post_install_module(bundle_dir: Path) -> Optional[ModuleType]:
    post = bundle_dir / "seeds" / "post_install.py"
    if not post.is_file():
        return None
    mod_name = f"integral_bundle_post_seed_{bundle_dir.name.replace('-', '_')}"
    spec = importlib.util.spec_from_file_location(mod_name, post)
    if spec is None or spec.loader is None:
        return None
    module = importlib.util.module_from_spec(spec)
    # Ensure sibling imports (e.g. company_wiki_content) resolve.
    package_root = str(bundle_dir.resolve())
    if package_root not in sys.path:
        sys.path.insert(0, package_root)
    seeds_dir = str((bundle_dir / "seeds").resolve())
    if seeds_dir not in sys.path:
        sys.path.insert(0, seeds_dir)
    sys.modules[mod_name] = module
    spec.loader.exec_module(module)
    return module


async def run_bundle_post_seed(
    app_node: Any,
    actor_id: str,
    *,
    bundle_dir: Optional[str] = None,
    required: bool = False,
) -> int:
    """Run ``seeds/post_install.run`` for the App's originating package, if any."""
    slug = str(getattr(app_node, "source_profile_slug", "") or "").strip() or None
    root = _resolve_bundle_dir(source_profile_slug=slug, bundle_dir=bundle_dir)
    if root is None:
        return 0
    try:
        module = _load_post_install_module(root)
    except Exception as exc:  # noqa: BLE001
        logger.exception(
            "bundle_post_seed: failed loading post_install for %s",
            root,
        )
        if required:
            raise RuntimeError(
                f"Required post-install seed failed to load: {root}"
            ) from exc
        return 0
    if module is None:
        return 0
    run = getattr(module, "run", None)
    if run is None:
        return 0
    try:
        result = await run(app_node, actor_id)
        return int(result or 0)
    except Exception as exc:  # noqa: BLE001
        logger.exception(
            "bundle_post_seed: post_install.run failed for app %s (slug=%s)",
            getattr(app_node, "id", None),
            slug,
        )
        if required:
            raise RuntimeError(
                f"Required post-install seed failed for package {slug or root.name}"
            ) from exc
        return 0
