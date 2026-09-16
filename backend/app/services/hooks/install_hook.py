"""Bundle install/uninstall hook — wires manifest tools[] + hooks[] into the
per-workspace registry (DR-30-01 + DR-30-02).

Called by services/app_lifecycle.install_app + uninstall_app.
"""

from __future__ import annotations

import logging
from typing import Any, Dict

from app.services.hooks.registry import (
    register_workspace_hooks,
    register_workspace_tools,
    unregister_bundle_registrations,
)
from app.services.hooks.trust import check_tools_permitted

logger = logging.getLogger(__name__)


def _normalize_handler_ref(
    bundle_slug: str,
    ref: str,
    *,
    bundle_dir: str | None = None,
) -> str:
    """Convert bundle-relative ref ('tools.pricing:fn') to absolute.

    Default namespace: ``app.profiles.<slug>``. When the package lives outside
    Core's profiles tree (F0 external package paths), ``bundle_dir``'s parent
    is added to ``sys.path`` and the slug is used as the top-level package
    name (importlib accepts hyphenated segments).
    """
    import sys
    from pathlib import Path

    if not ref:
        return ref
    module_part, _, fn_part = ref.partition(":")
    if module_part.startswith("app."):
        return ref
    if bundle_dir:
        parent = str(Path(bundle_dir).resolve().parent)
        if parent not in sys.path:
            sys.path.insert(0, parent)
        abs_module = f"{bundle_slug}.{module_part}"
    else:
        abs_module = f"app.profiles.{bundle_slug}.{module_part}"
    return f"{abs_module}:{fn_part}"


async def register_bundle_on_install(
    workspace_id: str,
    canonical: Dict[str, Any],
    *,
    bundle_dir: str | None = None,
) -> None:
    """Register a bundle's manifest tools[] + hooks[] into the per-workspace registry."""
    package = canonical.get("package") or {}
    # Library/app packages identify themselves with ``slug`` (e.g. "hr_app");
    # ``slug`` is frequently absent. Fall back to ``name`` so the bundle slug —
    # used both as the registry key AND to build the handler import path
    # (app.profiles.<slug>.tools.…) — is non-empty. An empty slug
    # produced "app.profiles..tools.leave_balance" → ModuleNotFoundError, so a
    # correctly-registered hook still failed to import its handler.
    bundle_slug = str(package.get("slug") or package.get("name") or "")
    trust_tier = str(package.get("trust_tier") or "untrusted")
    app_block = canonical.get("app") or {}
    tools = list(app_block.get("tools") or [])
    hooks = list(app_block.get("hooks") or [])

    # A ContentProfile that is not a code bundle (no ``package.slug``) and
    # declares no tools/hooks has nothing to register. This is the common case
    # for plain Apps (most workspaces, incl. all personal ones), and
    # ``rehydrate_all_installed_bundles`` replays this for EVERY active App on
    # every restart. Returning early avoids calling
    # ``unregister_bundle_registrations`` with an empty slug, which logged a
    # spurious WARNING per bundle-less workspace on each boot. A profile that
    # declares tools/hooks but no slug is a genuine misconfig and still falls
    # through to surface that (check_tools_permitted + the unregister warning).
    if not bundle_slug and not tools and not hooks:
        return

    check_tools_permitted(trust_tier, len(tools), bundle_slug)

    # Replace prior registration for this bundle (rehydrate / re-install safe).
    unregister_bundle_registrations(workspace_id, bundle_slug)

    # Normalize handler_ref → absolute import paths.
    normalized_tools = []
    for t in tools:
        nt = dict(t)
        nt["handler_ref"] = _normalize_handler_ref(
            bundle_slug,
            str(t.get("handler_ref") or ""),
            bundle_dir=bundle_dir,
        )
        normalized_tools.append(nt)

    register_workspace_tools(workspace_id, bundle_slug, normalized_tools)
    register_workspace_hooks(workspace_id, bundle_slug, hooks)
    from app.services.hooks.track_aliases import register_track_aliases_from_manifest

    register_track_aliases_from_manifest(workspace_id, canonical)
    logger.info(
        "bundle %s installed for workspace %s: %d tools, %d hooks",
        bundle_slug,
        workspace_id,
        len(normalized_tools),
        len(hooks),
    )


async def unregister_bundle_on_uninstall(workspace_id: str, bundle_slug: str) -> None:
    """Drop a bundle's tool + hook registrations from the workspace registry."""
    unregister_bundle_registrations(workspace_id, bundle_slug)


async def rehydrate_all_installed_bundles() -> None:
    """Re-register tools + hooks for every active App after a process restart.

    The hook registry is in-process (module-level dicts in
    ``services/hooks/registry.py``). Process restart → empty registry.
    Without this, bundles installed by ``install_app`` lose their
    declarative hook bindings + tool dispatch until they are re-installed.

    Walks every ``App`` node with ``lifecycle_state == 'active'``,
    compiles its attached ContentProfile manifest, and replays
    :func:`register_bundle_on_install` per workspace. Best-effort
    per-app — one bundle's failure does not abort the loop.

    Called from ``app.main._startup`` outside the TESTING gate so test
    boots stay fast; tests register hooks explicitly per the E2E
    pattern (see ``backend/tests/test_e2e_uat.py``).
    """
    from app.models.nodes import App
    from app.services.app_lifecycle import get_app_attached_content_profile
    from app.services.content_profile_runtime import compile_canonical_manifest

    apps = await App.find({"lifecycle_state": "active"})
    count_ok = 0
    count_err = 0
    count_healed = 0
    for app_node in apps:
        try:
            cp = await get_app_attached_content_profile(app_node)
            if cp is None:
                continue
            if await _heal_stripped_operational_layer(app_node, cp):
                count_healed += 1
            canonical = compile_canonical_manifest(manifest=cp.manifest or {})
            bundle_dir = (
                str((getattr(cp, "metadata", None) or {}).get("bundle_dir_path") or "")
                or None
            )
            if not bundle_dir:
                # Fall back to library row's recorded path when attached CP
                # was merged without metadata.
                lib_id = getattr(app_node, "installed_from_library_id", None)
                if lib_id:
                    from app.models import nodes as _nodes

                    lib = await _nodes.ContentProfile.get(lib_id)
                    if lib is not None:
                        bundle_dir = (
                            str(
                                (getattr(lib, "metadata", None) or {}).get(
                                    "bundle_dir_path"
                                )
                                or ""
                            )
                            or None
                        )
            await register_bundle_on_install(
                workspace_id=app_node.workspace_id,
                canonical=canonical,
                bundle_dir=bundle_dir,
            )
            count_ok += 1
        except Exception:  # noqa: BLE001
            logger.exception(
                "rehydrate: failed for app %s (ws %s)",
                getattr(app_node, "id", "?"),
                getattr(app_node, "workspace_id", "?"),
            )
            count_err += 1
    logger.info(
        "hook framework rehydration complete: %d apps rehydrated, %d healed, "
        "%d errors",
        count_ok,
        count_healed,
        count_err,
    )


async def _heal_stripped_operational_layer(app_node: Any, cp: Any) -> bool:
    """Backfill hooks/tools an attached profile lost to the pre-fix merge bug.

    ``merge_library_manifest`` used to rebuild the app block from a key
    allowlist that dropped ``hooks``/``tools`` (fixed in content_profile_merge),
    so apps installed-then-merged before the fix have attached profiles missing
    their bundle's hook/tool bindings — and register zero bindings here. Repair
    by copying the operational layer from the app's library source, then persist
    so the registration below (and every future boot) sees them.

    Idempotent: once the attached profile carries them, library==attached and
    this no-ops. Returns True iff a repair was written.
    """
    # Install provenance lives on ``installed_from_library_id``; older merge
    # paths also set ``library_merge_source_id``. Accept either so heal runs.
    lib_id = getattr(app_node, "library_merge_source_id", None) or getattr(
        app_node, "installed_from_library_id", None
    )
    if not lib_id:
        return False
    from app.models.nodes import ContentProfile

    lib = await ContentProfile.get(lib_id)
    if lib is None:
        return False
    lib_app = (getattr(lib, "manifest", {}) or {}).get("app") or {}
    lib_hooks = list(lib_app.get("hooks") or [])
    lib_tools = list(lib_app.get("tools") or [])
    cur_app = (cp.manifest or {}).get("app") or {}
    cur_hooks = list(cur_app.get("hooks") or [])
    cur_tools = list(cur_app.get("tools") or [])
    # Heal when the attached profile is missing the operational layer, or when
    # library hooks were revised (e.g. target_track_type projects ↔ customer-
    # projects) so boot rehydrate picks up the current bindings.
    hooks_stale = bool(lib_hooks) and (
        len(lib_hooks) > len(cur_hooks) or lib_hooks != cur_hooks
    )
    tools_stale = bool(lib_tools) and (
        len(lib_tools) > len(cur_tools) or lib_tools != cur_tools
    )
    if not hooks_stale and not tools_stale:
        return False

    manifest = dict(cp.manifest or {})
    app_block = dict(manifest.get("app") or {})
    if hooks_stale:
        app_block["hooks"] = lib_hooks
    if tools_stale:
        app_block["tools"] = lib_tools
    manifest["app"] = app_block
    cp.manifest = manifest
    await cp.save()
    logger.info(
        "rehydrate: healed operational layer for app %s (hooks %d→%d, tools %d→%d)",
        getattr(app_node, "id", "?"),
        len(cur_hooks),
        len(app_block.get("hooks") or cur_hooks),
        len(cur_tools),
        len(app_block.get("tools") or cur_tools),
    )
    return True
