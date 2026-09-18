"""Compile and activate workspace capability catalogue generations (ADR-012)."""

from __future__ import annotations

import json
import logging
import uuid
from typing import Dict, List, Optional

from app.models.catalogue_generation import CatalogueGeneration
from app.schemas.capabilities import (
    CapabilityCatalogueSnapshot,
    CapabilityDescriptor,
)
from app.utils.time import utc_now_iso

logger = logging.getLogger(__name__)

# Read-through cache: workspace_id → generation_id
_ACTIVE_GENERATION: Dict[str, str] = {}
_ACTIVE_SNAPSHOT: Dict[str, CapabilityCatalogueSnapshot] = {}


def _core_descriptors() -> List[CapabilityDescriptor]:
    return [
        CapabilityDescriptor(
            namespace="integral",
            key="describe_capabilities",
            kind="query",
            name="Describe capabilities",
            description="List permission-filtered capabilities for the active workspace",
            effects="read",
            policy_action="workspace.read",
        ),
        CapabilityDescriptor(
            namespace="integral",
            key="query",
            kind="query",
            name="Governed query",
            description="Execute QuerySpec v1 (declared App or open Core)",
            effects="read",
            policy_action="workspace.read",
            input_schema={"$ref": "QuerySpec"},
        ),
        CapabilityDescriptor(
            namespace="integral",
            key="core_entry_open",
            kind="query",
            name="Open Core entry query",
            description="Bounded open query over Core entry primitives (not App domain)",
            effects="read",
            policy_action="entry.read",
            resources=["entry"],
        ),
    ]


async def compile_workspace_catalogue(
    workspace_id: str,
    *,
    activate: bool = True,
) -> CapabilityCatalogueSnapshot:
    """Build descriptors from Core + registered App ops/queries/views."""
    from app.models.nodes import App

    caps: List[CapabilityDescriptor] = list(_core_descriptors())
    diagnostics: List[str] = []
    digests: Dict[str, str] = {}

    apps = await App.find({"workspace_id": workspace_id})
    app_by_id = {a.id: a for a in apps}
    # Also include apps present only in in-process registries (install race /
    # find-index lag must not yield an active App with empty catalogue).
    from app.services.app_operations import registry as ops_reg
    from app.services.app_operations.registry import list_registered_operations
    from app.services.app_queries.registry import (
        all_workspace_query_apps,
        list_registered_queries,
    )

    op_apps = set((ops_reg._OPERATIONS.get(workspace_id) or {}).keys())  # noqa: SLF001
    q_apps = set(all_workspace_query_apps(workspace_id).keys())
    for app_id in op_apps | q_apps:
        if app_id not in app_by_id:
            node = await App.get(app_id)
            if node is not None:
                app_by_id[app_id] = node

    for app in app_by_id.values():
        app_id = app.id
        state = str(getattr(app, "lifecycle_state", "active") or "active")
        slug = str(getattr(app, "installed_package_slug", "") or "") or str(
            getattr(app, "title", "") or app_id
        )
        version = str(getattr(app, "installed_package_version", "") or "0.0.0")
        digests[slug] = str(
            getattr(app, "installed_artifact_fingerprint", "") or version
        )
        availability = "active"
        if state == "paused":
            availability = "paused"
        elif state in ("activation_failed", "failed"):
            availability = "activation_failed"
        elif state == "installing":
            # Install registers the bundle before flipping lifecycle to
            # ``active``. Advertise as active so the post-register catalogue
            # is usable; a follow-up compile after activate confirms.
            availability = "active"
        elif state not in ("active", "awaiting_settings"):
            availability = "unavailable"

        ns = slug.replace(" ", "-").lower() or f"app-{app_id[:8]}"

        for key, spec in list_registered_queries(workspace_id, app_id).items():
            caps.append(
                CapabilityDescriptor(
                    namespace=ns,
                    key=str(key),
                    version=version,
                    owner_package=slug,
                    kind="query",
                    name=str(spec.get("name") or key),
                    description=str(spec.get("description") or ""),
                    input_schema=dict(spec.get("input_schema") or {}),
                    output_schema=dict(spec.get("output_schema") or {}),
                    effects="read",
                    policy_action=str(spec.get("policy_action") or "app.read"),
                    availability=availability,  # type: ignore[arg-type]
                    trust_tier=str(getattr(app, "trust_tier", "") or "") or None,
                    app_id=app_id,
                )
            )

        for key, spec in list_registered_operations(workspace_id, app_id).items():
            kind_raw = str(spec.get("kind") or "execute").lower()
            effects = "read" if kind_raw == "read" else "execute"
            if kind_raw == "propose":
                effects = "propose"
            caps.append(
                CapabilityDescriptor(
                    namespace=ns,
                    key=str(key),
                    version=version,
                    owner_package=slug,
                    kind="operation",
                    name=str(spec.get("name") or key),
                    description=str(spec.get("description") or ""),
                    input_schema=dict(spec.get("input_schema") or {}),
                    output_schema=dict(spec.get("output_schema") or {}),
                    effects=effects,  # type: ignore[arg-type]
                    policy_action=str(spec.get("policy_action") or "app.read"),
                    availability=availability,  # type: ignore[arg-type]
                    trust_tier=str(getattr(app, "trust_tier", "") or "") or None,
                    app_id=app_id,
                )
            )

        # Extension views from app settings / metadata when present
        meta = getattr(app, "metadata", None) or {}
        if isinstance(meta, dict):
            for view in meta.get("extension_views") or []:
                if not isinstance(view, dict):
                    continue
                vkey = str(view.get("key") or "").strip()
                if not vkey:
                    continue
                caps.append(
                    CapabilityDescriptor(
                        namespace=ns,
                        key=vkey,
                        version=version,
                        owner_package=slug,
                        kind="view",
                        name=str(view.get("name") or vkey),
                        description=str(view.get("description") or ""),
                        effects="read",
                        availability=availability,  # type: ignore[arg-type]
                        app_id=app_id,
                    )
                )

    generation_id = str(uuid.uuid4())
    compiled_at = utc_now_iso()
    snapshot = CapabilityCatalogueSnapshot(
        workspace_id=workspace_id,
        generation_id=generation_id,
        compiled_at=compiled_at,
        capabilities=caps,
        diagnostics=diagnostics,
    )

    record = CatalogueGeneration(
        workspace_id=workspace_id,
        generation_id=generation_id,
        package_digests_json=json.dumps(digests, sort_keys=True),
        descriptors_json=json.dumps([c.model_dump() for c in caps], sort_keys=True),
        diagnostics_json=json.dumps(diagnostics),
        status="failed" if not activate else "active",
        compiled_at=compiled_at,
        activated_at=compiled_at if activate else None,
    )
    await record.save()

    if activate:
        # Supersede prior active generations (best-effort)
        try:
            prior = await CatalogueGeneration.find(
                {"workspace_id": workspace_id, "status": "active"}
            )
            for row in prior:
                if row.generation_id == generation_id:
                    continue
                row.status = "superseded"
                await row.save()
        except Exception as exc:  # noqa: BLE001
            logger.warning("catalogue supersede failed: %s", exc)
        _ACTIVE_GENERATION[workspace_id] = generation_id
        _ACTIVE_SNAPSHOT[workspace_id] = snapshot

    return snapshot


def get_cached_snapshot(workspace_id: str) -> Optional[CapabilityCatalogueSnapshot]:
    """Return the in-process active catalogue snapshot, if any."""
    return _ACTIVE_SNAPSHOT.get(workspace_id)


def _registry_capability_fingerprint(workspace_id: str) -> frozenset:
    """Stable set of (app_id, kind, key) from in-process registries."""
    from app.services.app_operations import registry as ops_reg
    from app.services.app_queries.registry import all_workspace_query_apps

    fp: set = set()
    for app_id, queries in all_workspace_query_apps(workspace_id).items():
        for key in queries:
            fp.add((app_id, "query", key))
    for app_id, ops in (
        ops_reg._OPERATIONS.get(workspace_id) or {}
    ).items():  # noqa: SLF001
        for key in ops:
            fp.add((app_id, "operation", key))
    return frozenset(fp)


def _snapshot_capability_fingerprint(
    snapshot: CapabilityCatalogueSnapshot,
) -> frozenset:
    return frozenset(
        (c.app_id, c.kind, c.key)
        for c in snapshot.capabilities
        if c.app_id and c.kind in ("query", "operation")
    )


async def get_or_compile_catalogue(
    workspace_id: str,
) -> CapabilityCatalogueSnapshot:
    """Return the active catalogue, recompiling when registries have drifted."""
    cached = get_cached_snapshot(workspace_id)
    if cached is not None:
        reg_fp = _registry_capability_fingerprint(workspace_id)
        cached_fp = _snapshot_capability_fingerprint(cached)
        # Also bust when any app-scoped cap is still marked unavailable while
        # registries know the app (install-time stale compile).
        stale_unavailable = any(
            c.app_id and c.availability == "unavailable" for c in cached.capabilities
        )
        if reg_fp <= cached_fp and not stale_unavailable:
            return cached
    return await compile_workspace_catalogue(workspace_id, activate=True)


def filter_capabilities_for_principal(
    snapshot: CapabilityCatalogueSnapshot,
    *,
    include_paused: bool = False,
) -> List[CapabilityDescriptor]:
    """Basic lifecycle filter; policy row checks happen at invoke time."""
    out: List[CapabilityDescriptor] = []
    for cap in snapshot.capabilities:
        if not cap.discoverable:
            continue
        if (
            cap.availability == "active"
            or (include_paused and cap.availability == "paused")
            or cap.availability == "activation_failed"
        ):
            # activation_failed stays visible so callers can diagnose, not invoke
            out.append(cap)
    return out


def require_generation(workspace_id: str, generation_id: Optional[str]) -> None:
    """Reject stale catalogue invocations when a generation id is supplied."""
    if not generation_id:
        return
    active = _ACTIVE_GENERATION.get(workspace_id)
    if active and active != generation_id:
        from app.api.errors import BadRequestError

        raise BadRequestError(
            message="Stale capability catalogue generation",
            details={
                "error_code": "stale_catalogue",
                "expected": active,
                "got": generation_id,
            },
        )
