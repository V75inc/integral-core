"""One-off: sync persisted Skill nodes from on-disk bundle SKILL.md + manifests.

Re-runs ``sync_operational_layer_from_manifest`` per installed App, then heals
Skill nodes:

- Backfill ``description`` from manifest when empty (or when no ``body_override``).
- Clear ``body_override`` when it matches the old scaffold stub text.
- Refresh ``bundle_default_digest`` so ``stale_default`` reflects disk changes.

Usage (from ``backend/`` with DB env loaded):

    python3 scripts/sync_skill_nodes_from_disk.py
    python3 scripts/sync_skill_nodes_from_disk.py --dry
    python3 scripts/sync_skill_nodes_from_disk.py --workspace-id <id>
"""

from __future__ import annotations

import argparse
import asyncio
import logging
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

_PROFILES_ROOT = Path(__file__).resolve().parent.parent / "app" / "profiles"

_STUB_BODY_MARKERS = (
    "run this workflow for the item",
    "run this workflow for the item/asset",
    "ground schema → resolve entities → `integral_begin_batch`",
    "ground schema -> resolve entities",
)


def _is_stub_body(text: str) -> bool:
    lowered = (text or "").lower()
    return any(marker in lowered for marker in _STUB_BODY_MARKERS)


def _manifest_skill_map(canonical: Dict[str, Any]) -> Dict[str, Dict[str, Any]]:
    skills = (canonical.get("app") or {}).get("skills") or []
    if not skills:
        skills = (canonical.get("track") or {}).get("skills") or []
    out: Dict[str, Dict[str, Any]] = {}
    for spec in skills:
        if isinstance(spec, dict):
            key = str(spec.get("key") or "").strip()
            if key:
                out[key] = spec
    return out


async def _canonical_for_app(app) -> Optional[Dict[str, Any]]:
    from app.models.nodes import ContentProfile
    from app.services.content_profile_compile import compile_canonical_manifest
    from app.services.content_profile_loader import load_library_profiles_with_issues

    md = dict(getattr(app, "metadata", None) or {})
    source = md.get("source_manifest")
    if isinstance(source, dict) and source:
        return compile_canonical_manifest(manifest=source)

    slug = str(getattr(app, "source_profile_slug", None) or "").strip()
    if slug:
        specs, _ = load_library_profiles_with_issues(profiles_root=_PROFILES_ROOT)
        spec = next((s for s in specs if s.slug == slug), None)
        if spec is not None:
            return compile_canonical_manifest(manifest=spec.manifest)

    lib_id = str(getattr(app, "installed_from_library_id", "") or "").strip()
    if lib_id:
        cp = await ContentProfile.get(lib_id)
        if cp is not None:
            manifest = getattr(cp, "manifest", None) or {}
            if isinstance(manifest, dict) and manifest:
                return compile_canonical_manifest(manifest=manifest)

    return None


async def _heal_skill_node(
    skill,
    *,
    manifest_spec: Optional[Dict[str, Any]],
    dry_run: bool,
) -> Tuple[bool, List[str]]:
    """Return (changed, reasons)."""
    reasons: List[str] = []
    origin = str(getattr(skill, "origin", "bundle") or "bundle")
    if origin != "bundle":
        return False, reasons

    manifest_desc = ""
    if manifest_spec:
        manifest_desc = str(manifest_spec.get("description") or "").strip()

    preserve_override = bool(str(getattr(skill, "body_override", None) or "").strip())
    node_desc = str(getattr(skill, "description", "") or "").strip()

    if manifest_desc:
        if not node_desc:
            reasons.append("description:empty→manifest")
        elif not preserve_override and node_desc != manifest_desc:
            reasons.append("description:refresh(no override)")

    override = str(getattr(skill, "body_override", None) or "").strip()
    if override and _is_stub_body(override):
        reasons.append("body_override:clear_stub")

    if not reasons:
        from app.agentive.workspace_agent_profile import resolve_bundle_default_body
        from app.models.nodes import App

        app = await App.get(str(getattr(skill, "app_id", "") or ""))
        if app is not None:
            _, new_digest = await resolve_bundle_default_body(skill, app=app)
            old_digest = str(getattr(skill, "bundle_default_digest", "") or "")
            if new_digest and new_digest != old_digest:
                reasons.append("bundle_default_digest:refresh")

    if not reasons:
        return False, []

    if dry_run:
        return True, reasons

    if manifest_desc:
        if (
            "description:empty→manifest" in reasons
            or "description:refresh(no override)" in reasons
        ):
            skill.description = manifest_desc

    if "body_override:clear_stub" in reasons:
        skill.body_override = None
        skill.stale_default = False
        skill.customized_at = None
        skill.customized_by = None
        if manifest_desc:
            skill.description = manifest_desc

    from app.agentive.workspace_agent_profile import resolve_bundle_default_body
    from app.models.nodes import App
    from app.utils.time import utc_now_iso

    app = await App.get(str(getattr(skill, "app_id", "") or ""))
    if app is not None:
        _, new_digest = await resolve_bundle_default_body(skill, app=app)
        if new_digest:
            old_digest = getattr(skill, "bundle_default_digest", None)
            skill.bundle_default_digest = new_digest
            if preserve_override and old_digest and old_digest != new_digest:
                skill.stale_default = True

    skill.updated_at = utc_now_iso()
    await skill.save()
    return True, reasons


async def sync_skill_nodes_from_disk(
    *,
    dry_run: bool = False,
    workspace_id: Optional[str] = None,
) -> Dict[str, int]:
    from app.agentive.workspace_agent_profile import invalidate_workspace_profile
    from app.models.edges import CONTAINS
    from app.models.nodes import App, Workspace
    from app.services.app_lifecycle import sync_operational_layer_from_manifest

    stats = {
        "workspaces": 0,
        "apps": 0,
        "apps_synced": 0,
        "apps_skipped": 0,
        "skills_healed": 0,
        "skills_unchanged": 0,
    }

    if workspace_id:
        workspaces = [await Workspace.get(workspace_id)]
        workspaces = [w for w in workspaces if w is not None]
    else:
        workspaces = await Workspace.find()
    stats["workspaces"] = len(workspaces)

    touched_workspaces: set[str] = set()

    for ws in workspaces:
        ws_id = str(getattr(ws, "id", "") or "")
        apps = await App.find({"workspace_id": ws_id})
        for app in apps or []:
            stats["apps"] += 1
            canonical = await _canonical_for_app(app)
            if canonical is None:
                stats["apps_skipped"] += 1
                continue

            skill_map = _manifest_skill_map(canonical)
            if not skill_map:
                stats["apps_skipped"] += 1
                continue

            app_id = str(getattr(app, "id", "") or "")
            if dry_run:
                logger.info(
                    "[dry] would sync operational layer for app %s (%s skills)",
                    app_id,
                    len(skill_map),
                )
            else:
                try:
                    await sync_operational_layer_from_manifest(
                        app, canonical, actor_id="sync_skill_nodes_from_disk"
                    )
                except Exception:
                    logger.exception(
                        "operational sync failed for app %s (workspace=%s)",
                        app_id,
                        ws_id,
                    )
                    stats["apps_skipped"] += 1
                    continue
            stats["apps_synced"] += 1
            touched_workspaces.add(ws_id)

            skills = await app.nodes(edge=[CONTAINS], node=["Skill"])
            for sk in skills:
                key = str(getattr(sk, "key", "") or "").strip()
                changed, reasons = await _heal_skill_node(
                    sk,
                    manifest_spec=skill_map.get(key),
                    dry_run=dry_run,
                )
                if changed:
                    stats["skills_healed"] += 1
                    logger.info(
                        "%s skill %s/%s: %s",
                        "[dry]" if dry_run else "healed",
                        app_id,
                        key,
                        ", ".join(reasons),
                    )
                else:
                    stats["skills_unchanged"] += 1

    if not dry_run:
        for ws_id in touched_workspaces:
            invalidate_workspace_profile(ws_id)

    return stats


async def _main(dry_run: bool, workspace_id: Optional[str]) -> None:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    from app.services.db_init import init_prime_db

    init_prime_db()
    stats = await sync_skill_nodes_from_disk(dry_run=dry_run, workspace_id=workspace_id)
    prefix = "[dry-run] " if dry_run else ""
    print(f"{prefix}sync_skill_nodes_from_disk => {stats}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry", action="store_true", help="Log actions only")
    parser.add_argument("--workspace-id", default=None, help="Limit to one workspace")
    args = parser.parse_args()
    asyncio.run(_main(dry_run=args.dry, workspace_id=args.workspace_id))
