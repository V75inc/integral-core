"""Phase 32 — batch install N library bundles in one transaction.

Public surface:
    await batch_install(workspace_id, items, actor_id) -> dict

Items shape::

    [
        {
            "library_cp_id": "n.ContentProfile.X",
            "name": "Optional override (defaults to package.name)",
            "description": "Optional override (defaults to package.description)",
            "settings": {…optional settings payload…},
        },
        …
    ]

Behaviour:

* Each library bundle is compiled once at the head of the call so dep
  resolution + topological sort happen before any write.
* The batch is topologically sorted by ``app.requires_apps[]``: if
  bundle A declares a hard dep on bundle B AND B is also in the batch,
  B installs first. Bundles whose hard deps live OUTSIDE the batch are
  validated against existing installs via the standard
  ``_check_requires_apps`` path inside ``install_app``.
* Circular deps inside the batch raise ``BadRequestError`` BEFORE any
  install runs.
* Idempotency: a bundle whose ``library_cp_id`` already has an active
  App installation in this workspace is reported as ``skipped`` (no
  duplicate install attempt).
* Per-app exceptions DO NOT halt the batch — they are surfaced in the
  ``failed[]`` list. The substrate's existing install-side rollback
  cleans up the failed bundle's partial writes; subsequent bundles in
  the ordered list still try to install.

Response shape::

    {
        "installed": [{"library_cp_id", "app_id", "name", "status"}],
        "skipped":   [{"library_cp_id", "reason", "app_id"}],
        "failed":    [{"library_cp_id", "error", "error_code"}],
        "order":     ["library_cp_id_1", "library_cp_id_2", …],
    }
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional, Set, Tuple

from app.api.errors import BadRequestError
from app.models.nodes import App, ContentProfile
from app.services.app_lifecycle import install_app
from app.services.content_profile_compile import slug_manifest_key
from app.services.content_profile_runtime import compile_canonical_manifest

logger = logging.getLogger(__name__)


def _library_bundle_aliases(
    canonical: Dict[str, Any], cp: Optional[ContentProfile] = None
) -> List[str]:
    package = canonical.get("package") or {}
    candidates: List[str] = [
        str(package.get("name") or "").strip(),
        str(package.get("slug") or "").strip(),
        str(package.get("key") or "").strip(),
    ]
    if cp is not None:
        candidates.extend(
            [
                str(getattr(cp, "name", "") or "").strip(),
                str(getattr(cp, "slug", "") or "").strip(),
                str(cp.id or "").strip(),
            ]
        )
    seen: set[str] = set()
    out: List[str] = []
    for cand in candidates:
        if not cand:
            continue
        for form in (cand, cand.casefold(), slug_manifest_key(cand)):
            if form and form not in seen:
                seen.add(form)
                out.append(form)
    return out


def _lookup_lib(dep_key: str, name_to_lib: Dict[str, str]) -> Optional[str]:
    k = str(dep_key or "").strip()
    if not k:
        return None
    return (
        name_to_lib.get(k)
        or name_to_lib.get(k.casefold())
        or name_to_lib.get(slug_manifest_key(k))
    )


def _has_path(start: str, target: str, edges: Dict[str, Set[str]]) -> bool:
    """Return True if ``target`` can be reached from ``start`` following ``edges``."""
    if start == target:
        return True
    visited: Set[str] = set()
    queue = [start]
    while queue:
        curr = queue.pop(0)
        if curr == target:
            return True
        if curr in visited:
            continue
        visited.add(curr)
        for nxt in edges.get(curr, ()):
            if nxt not in visited:
                queue.append(nxt)
    return False


def _topo_sort(
    nodes: List[str], edges: Dict[str, List[str]]
) -> Tuple[List[str], List[str]]:
    """Kahn's algorithm. Returns (ordered_nodes, cycle_remnant).

    ``edges[a] = [b, c]`` means a depends on b AND c (b, c must install
    before a). A non-empty cycle_remnant indicates an unresolved cycle.
    """
    remaining: Dict[str, set] = {n: set(edges.get(n) or []) for n in nodes}
    order: List[str] = []
    while True:
        no_incoming = [n for n, deps in remaining.items() if not deps]
        if not no_incoming:
            break
        # Sort by node id for deterministic output.
        no_incoming.sort()
        for n in no_incoming:
            order.append(n)
            del remaining[n]
            for deps in remaining.values():
                deps.discard(n)
    return order, sorted(remaining.keys())


def default_settings_for_canonical(canonical: Dict[str, Any]) -> Dict[str, Any]:
    """Settings dict built from a bundle's ``settings_schema`` defaults.

    Lets a caller activate an app immediately (no paused ``awaiting_settings``
    state) by pre-supplying each declared property's ``default``. Required
    properties WITHOUT a default are omitted, so the install then fails schema
    validation rather than silently mis-configuring.
    """
    schema = (canonical.get("app") or {}).get("settings_schema") or {}
    props = schema.get("properties") or {}
    return {
        key: spec["default"]
        for key, spec in props.items()
        if isinstance(spec, dict) and "default" in spec
    }


async def _expand_with_dependencies(
    items: List[Dict[str, Any]],
) -> Tuple[List[Dict[str, Any]], int]:
    """Append auto-resolved HARD app dependencies from the library catalog.

    A bundle's ``app.requires_apps[]`` (``optional: false``) names other apps by
    ``key``, matched against another library package's ``package.name`` (the same
    wiring the topo-sort uses). Any such dependency that exists as an app-scope
    library package — and isn't already selected — is appended (transitively) so
    the batch is self-consistent. Returns ``(expanded_items, added_count)``.
    """
    selected = [
        str((it or {}).get("library_cp_id") or "").strip()
        for it in items
        if str((it or {}).get("library_cp_id") or "").strip()
    ]
    name_to_id: Dict[str, str] = {}
    requires: Dict[str, List[str]] = {}
    for cp in await ContentProfile.all():
        if not getattr(cp, "library_package", False):
            continue
        try:
            canonical = compile_canonical_manifest(manifest=cp.manifest or {})
        except Exception:  # noqa: BLE001 — bad manifest surfaces during install
            continue
        app = canonical.get("app") or {}
        if not app:
            continue  # track-scope bundles aren't installable apps
        for alias in _library_bundle_aliases(canonical, cp):
            name_to_id[alias] = cp.id
        requires[cp.id] = [
            str(dep.get("key") or "").strip()
            for dep in (app.get("requires_apps") or [])
            if isinstance(dep, dict)
            and not dep.get("optional", False)
            and str(dep.get("key") or "").strip()
        ]

    expanded = list(items)
    seen = set(selected)
    queue = list(selected)
    added = 0
    while queue:
        cid = queue.pop()
        for dep_key in requires.get(cid, []):
            dep_id = _lookup_lib(dep_key, name_to_id)
            if dep_id and dep_id not in seen:
                seen.add(dep_id)
                expanded.append({"library_cp_id": dep_id})
                queue.append(dep_id)
                added += 1
    return expanded, added


async def batch_install(
    *,
    workspace_id: str,
    items: List[Dict[str, Any]],
    actor_id: str,
    resolve_dependencies: bool = False,
    use_default_settings: bool = False,
) -> Dict[str, Any]:
    """Install multiple library bundles into ``workspace_id`` in topological dependency order.

    ``resolve_dependencies`` — auto-pull each item's hard app dependencies from
    the library catalog (so the caller need not hand-pick prerequisites).
    ``use_default_settings`` — for any item WITHOUT explicit ``settings``, fill
    the bundle's ``settings_schema`` defaults so the app activates immediately
    instead of pausing at ``awaiting_settings``. Both default off (the Manage
    Apps dialog opts into dependency resolution only; the create wizard opts
    into both).
    """
    if not workspace_id:
        raise BadRequestError(message="batch_install: workspace_id is required")
    if not items:
        raise BadRequestError(message="batch_install: items[] is empty")

    auto_dependencies = 0
    if resolve_dependencies:
        items, auto_dependencies = await _expand_with_dependencies(items)

    # ---- Pre-flight: load every library + compile canonical manifest ----
    loaded: Dict[str, Dict[str, Any]] = {}
    for raw in items:
        lib_id = str((raw or {}).get("library_cp_id") or "").strip()
        if not lib_id:
            raise BadRequestError(message="batch_install: item missing library_cp_id")
        if lib_id in loaded:
            raise BadRequestError(
                message=f"batch_install: duplicate library_cp_id {lib_id!r} in batch"
            )
        cp = await ContentProfile.get(lib_id)
        if cp is None or not getattr(cp, "library_package", False):
            raise BadRequestError(
                message=f"batch_install: library bundle {lib_id!r} not found or not a library package"
            )
        canonical = compile_canonical_manifest(manifest=cp.manifest or {})
        item_settings = raw.get("settings")
        if item_settings is None and use_default_settings:
            # Activate immediately with the schema's declared defaults instead
            # of pausing at awaiting_settings (auto-pulled deps included).
            item_settings = default_settings_for_canonical(canonical)
        loaded[lib_id] = {
            "cp": cp,
            "canonical": canonical,
            "name_override": (raw.get("name") or "").strip() or None,
            "description_override": (raw.get("description") or "").strip() or None,
            "settings": item_settings,
            "include_seed_data": raw.get("include_seed_data", True),
        }

    # ---- Build dep graph (edges keyed by library_cp_id) ----
    # Map intra-batch dep keys (package.name, slug, aliases) → library_cp_id
    # so we can wire edges. Bundles whose deps live OUTSIDE the batch are
    # left for the standard _check_requires_apps gate inside install_app.
    name_to_lib: Dict[str, str] = {}
    for lib_id, info in loaded.items():
        for alias in _library_bundle_aliases(info["canonical"], info.get("cp")):
            name_to_lib[alias] = lib_id

    # Hard dependencies (optional: False) strictly enforce ordering.
    # Soft dependencies (optional: True) guide ordering preferences when acyclic,
    # but are omitted if they would introduce an intra-batch cycle (e.g. mutual
    # soft references between CRM and Projects).
    hard_edges: Dict[str, Set[str]] = {n: set() for n in loaded.keys()}
    soft_edge_candidates: List[Tuple[str, str]] = []
    for lib_id, info in loaded.items():
        requires = (info["canonical"].get("app") or {}).get("requires_apps") or []
        for dep in requires:
            if not isinstance(dep, dict):
                continue
            dep_key = str(dep.get("key") or "").strip()
            target = _lookup_lib(dep_key, name_to_lib)
            if target and target != lib_id:
                optional = bool(dep.get("optional", False))
                if not optional:
                    hard_edges[lib_id].add(target)
                else:
                    soft_edge_candidates.append((lib_id, target))

    # Reject real circular dependencies among hard requirements before any write.
    _, hard_cycle = _topo_sort(
        list(loaded.keys()), {k: list(v) for k, v in hard_edges.items()}
    )
    if hard_cycle:
        raise BadRequestError(
            message="batch_install: circular dep detected in batch",
            details={"unresolved": hard_cycle},
        )

    # Incorporate soft dependency edges where they do not introduce cycles.
    current_edges: Dict[str, Set[str]] = {k: set(v) for k, v in hard_edges.items()}
    for u, v in sorted(set(soft_edge_candidates)):
        # u depends on v (v must install before u). A cycle would occur if v already reaches u.
        if not _has_path(start=v, target=u, edges=current_edges):
            current_edges[u].add(v)
        else:
            logger.info(
                "batch_install: soft dep edge %s -> %s skipped to prevent cycle",
                u,
                v,
            )

    order, cycle = _topo_sort(
        list(loaded.keys()), {k: sorted(v) for k, v in current_edges.items()}
    )
    if cycle:
        # Defense-in-depth: fall back to validated acyclic hard dependencies
        order, _ = _topo_sort(
            list(loaded.keys()), {k: sorted(v) for k, v in hard_edges.items()}
        )

    # ---- Execute installs in order ----
    installed: List[Dict[str, Any]] = []
    skipped: List[Dict[str, Any]] = []
    failed: List[Dict[str, Any]] = []
    for lib_id in order:
        info = loaded[lib_id]
        # Idempotency — skip if this library is already installed (active) in this workspace.
        existing = await App.find(
            {
                "context.workspace_id": workspace_id,
                "context.installed_from_library_id": lib_id,
                "context.lifecycle_state": "active",
            }
        )
        if existing:
            skipped.append(
                {
                    "library_cp_id": lib_id,
                    "reason": "already_installed",
                    "app_id": existing[0].id,
                }
            )
            continue
        try:
            result = await install_app(
                workspace_id=workspace_id,
                library_cp_id=lib_id,
                actor_id=actor_id,
                settings=info["settings"],
                name_override=info["name_override"],
                description_override=info["description_override"],
                include_seed_data=bool(info.get("include_seed_data", True)),
            )
            # Phase 33 — report the HUMAN display name (ContentProfile.name)
            # in the response payload, not the slug-form
            # manifest.package.name. install_app already wrote the right
            # value to App.name; report that here so the frontend dialog
            # matches what the apps list will show.
            installed_name = (
                info["name_override"]
                or str(getattr(info["cp"], "name", "") or "")
                or (info["canonical"].get("package") or {}).get("name")
            )
            installed.append(
                {
                    "library_cp_id": lib_id,
                    "app_id": result.get("app_id"),
                    "name": installed_name,
                    "status": result.get("status"),
                    **(
                        {
                            "install_token": result["install_token"],
                            "settings_schema": result.get("settings_schema"),
                        }
                        if result.get("install_token")
                        else {}
                    ),
                }
            )
        except Exception as exc:  # noqa: BLE001 — partial-success protocol
            logger.exception(
                "batch_install: bundle %s failed to install in workspace %s",
                lib_id,
                workspace_id,
            )
            # Report the human display name alongside the id so the
            # frontend dialog never has to show a raw node id to the user.
            failed_name = (
                info["name_override"]
                or str(getattr(info["cp"], "name", "") or "")
                or (info["canonical"].get("package") or {}).get("name")
            )
            failed.append(
                {
                    "library_cp_id": lib_id,
                    "name": failed_name,
                    "error": str(exc),
                    "error_code": getattr(exc, "error_code", "install_failed"),
                }
            )

    return {
        "installed": installed,
        "skipped": skipped,
        "failed": failed,
        "order": order,
        "auto_dependencies": auto_dependencies,
    }
