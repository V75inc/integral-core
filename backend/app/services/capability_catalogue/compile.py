"""Compile and activate workspace capability catalogue generations (ADR-012)."""

from __future__ import annotations

import hashlib
import json
import logging
from copy import deepcopy
from typing import Any, AsyncIterator, Dict, List, Optional

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
_ACTIVE_REGISTRY_DEFINITIONS: Dict[str, str] = {}
_ACTIVE_APP_DEFINITIONS: Dict[str, str] = {}


async def _app_source_records(workspace_id: str) -> AsyncIterator[dict]:
    """Bound fresh primary App records; hydration must not hide bad sources."""
    from jvspatial.core.context import get_default_context

    from app.api.errors import QueryUnavailableError
    from app.models.nodes import App
    from app.services.authority_facts import required_node_text

    database = get_default_context().database
    position = None
    while True:
        query: dict[str, Any] = {
            "entity": App._entity_name(),
            "context.workspace_id": workspace_id,
        }
        if position:
            query["id"] = {"$gt": position}
        try:
            page = await database.find("node", query, limit=500, sort=[("id", 1)])
        except Exception as exc:
            raise QueryUnavailableError() from exc
        for row in page:
            if (
                not isinstance(row.get("context"), dict)
                or row.get("entity") != App._entity_name()
                or not isinstance(row.get("id"), str)
                or not row["id"]
                or (position and row["id"] <= position)
                or required_node_text(row, "workspace_id") != workspace_id
            ):
                raise QueryUnavailableError(message="Invalid App catalogue source")
            position = row["id"]
            yield row
        if len(page) < 500:
            return


async def _app_definition_fingerprint(workspace_id: str) -> str:
    """Hash current lifecycle/manifest metadata; this never caches a grant."""
    fields = (
        "lifecycle_state",
        "installed_package_slug",
        "name",
        "description",
        "installed_package_version",
        "installed_artifact_fingerprint",
        "trust_tier",
        "metadata",
    )
    digest = hashlib.sha256()
    async for row in _app_source_records(workspace_id):
        data = row["context"]
        value = (
            row["id"],
            {key: data[key] for key in fields if key in data},
            {key: row[key] for key in fields if key in row},
        )
        digest.update(json.dumps(value, sort_keys=True).encode())
        digest.update(b"\n")
    return digest.hexdigest()


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

    registry_before = _registry_definition_fingerprint(workspace_id)
    apps_before = await _app_definition_fingerprint(workspace_id)
    caps: List[CapabilityDescriptor] = list(_core_descriptors())
    diagnostics: List[str] = []
    digests: Dict[str, str] = {}

    from jvspatial.core.context import get_default_context

    from app.api.errors import QueryUnavailableError

    context = get_default_context()
    app_by_id = {}
    async for row in _app_source_records(workspace_id):
        node = await context._deserialize_entity(App, row)
        if node is None:
            raise QueryUnavailableError(message="Invalid App catalogue payload")
        app_by_id[node.id] = node
    # Registrations cannot resurrect deleted, foreign or malformed App nodes.
    # The final source fingerprint rejects concurrent installation races.
    from app.services.app_operations import registry as ops_reg
    from app.services.app_operations.registry import (
        list_registered_operations,
        operation_policy_action,
    )
    from app.services.app_queries.registry import (
        all_workspace_query_apps,
        list_registered_queries,
    )

    op_apps = set((ops_reg._OPERATIONS.get(workspace_id) or {}).keys())  # noqa: SLF001
    q_apps = set(all_workspace_query_apps(workspace_id).keys())
    for app_id in op_apps | q_apps:
        if app_id not in app_by_id:
            # Registry (query/operation) holds an app_id with no App node —
            # the signature of a stale bucket left by an earlier install
            # whose unregister did not run in this process. This id is
            # dropped from the catalogue but still counts as a match in
            # _run_declared, producing "ambiguous; pass app_id".
            logger.warning(
                "catalogue: registry app_id %s in workspace %s has no App node "
                "(stale query/operation bucket?)",
                app_id,
                workspace_id,
            )

    for app in app_by_id.values():
        app_id = app.id
        state = str(getattr(app, "lifecycle_state", "active") or "active")
        slug = str(getattr(app, "installed_package_slug", "") or "") or str(
            getattr(app, "name", "") or app_id
        )
        version = str(getattr(app, "installed_package_version", "") or "0.0.0")
        digests[app_id] = str(
            getattr(app, "installed_artifact_fingerprint", "") or version
        )
        availability = "active"
        if state == "paused":
            availability = "paused"
        elif state in ("activation_failed", "failed"):
            availability = "activation_failed"
        elif state != "active":
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
                    input_schema=deepcopy(spec.get("input_schema") or {}),
                    output_schema=deepcopy(spec.get("output_schema") or {}),
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
                    input_schema=deepcopy(spec.get("input_schema") or {}),
                    output_schema=deepcopy(spec.get("output_schema") or {}),
                    effects=effects,  # type: ignore[arg-type]
                    policy_action=operation_policy_action(app_id, str(key), spec),
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

    caps.sort(
        key=lambda cap: (
            cap.app_id or "",
            cap.namespace,
            cap.kind,
            cap.key,
            cap.version,
        )
    )
    # Identical contracts have the same identity after restart and across
    # workers. Timestamp/random UUID changes are not schema generations.
    generation_id = hashlib.sha256(
        json.dumps(
            {
                "workspace": workspace_id,
                "capabilities": [cap.model_dump() for cap in caps],
                "packages": digests,
                "registry": registry_before,
                "apps": apps_before,
            },
            sort_keys=True,
        ).encode()
    ).hexdigest()
    if registry_before != _registry_definition_fingerprint(
        workspace_id
    ) or apps_before != await _app_definition_fingerprint(workspace_id):
        from app.api.errors import QueryUnavailableError

        raise QueryUnavailableError(
            message="Capability definitions changed during compilation; retry"
        )
    compiled_at = utc_now_iso()
    snapshot = CapabilityCatalogueSnapshot(
        workspace_id=workspace_id,
        generation_id=generation_id,
        compiled_at=compiled_at,
        capabilities=caps,
        diagnostics=diagnostics,
    )

    record = CatalogueGeneration(
        id=f"o.{CatalogueGeneration._entity_name()}.{generation_id}{'a' if activate else 'p'}",
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
        _ACTIVE_REGISTRY_DEFINITIONS[workspace_id] = registry_before
        _ACTIVE_APP_DEFINITIONS[workspace_id] = apps_before

    return snapshot


def get_cached_snapshot(workspace_id: str) -> Optional[CapabilityCatalogueSnapshot]:
    """Return the in-process active catalogue snapshot, if any."""
    return _ACTIVE_SNAPSHOT.get(workspace_id)


def _registry_definition_fingerprint(workspace_id: str) -> str:
    """Detect same-key contract edits without caching executable authority."""
    from app.services.app_operations import registry as ops_reg
    from app.services.app_queries.registry import all_workspace_query_apps

    fields = (
        "name",
        "description",
        "input_schema",
        "output_schema",
        "kind",
        "policy_action",
        "handler_ref",
        "tool",
        "query_template",
        "authorization",
        "agent_invocation",
    )
    rows = []
    for kind, apps in (
        ("query", all_workspace_query_apps(workspace_id)),
        ("operation", ops_reg._OPERATIONS.get(workspace_id) or {}),
    ):
        for app_id, specs in apps.items():
            for key, spec in specs.items():
                rows.append(
                    (app_id, kind, key, {field: spec.get(field) for field in fields})
                )
    return hashlib.sha256(
        json.dumps(
            sorted(rows, key=lambda row: row[:3]), sort_keys=True, default=str
        ).encode()
    ).hexdigest()


async def get_or_compile_catalogue(
    workspace_id: str,
) -> CapabilityCatalogueSnapshot:
    """Return the active catalogue, recompiling when registries have drifted."""
    cached = get_cached_snapshot(workspace_id)
    if cached is not None:
        if _ACTIVE_REGISTRY_DEFINITIONS.get(
            workspace_id
        ) == _registry_definition_fingerprint(
            workspace_id
        ) and _ACTIVE_APP_DEFINITIONS.get(
            workspace_id
        ) == await _app_definition_fingerprint(
            workspace_id
        ):
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
