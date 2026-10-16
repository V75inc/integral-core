"""Permission-filtered schema graph; never export the raw administrative graph."""

import asyncio
import json
import re
from typing import Any, Dict, List, Literal, Mapping, Optional, Tuple

from jvspatial.core.context import get_default_context
from jvspatial.core.graph_projection import (
    ProjectionRequest,
    ProjectionScope,
    ProjectionSemantics,
)

from app.api.errors import InsufficientPermissionsError, ServiceUnavailableError
from app.middleware.permissions_cache import isolated_permissions_cache
from app.models.edges import HAS_OPERATIONAL_MODEL
from app.models.nodes import App, EntryType, OperationalModel, Track
from app.schemas.workspace_ontology import OntologyField, OntologyRequest, OntologyRoot
from app.services.graph_context import WorkspaceProjectionAccess
from app.services.graph_hydration import batch_get_by_ids
from app.services.workspace_permissions import user_in_workspace_member_pool

NODE_FIELDS = {
    "Workspace": ("name", "kind"),
    "Apps": (),
    "Tracks": (),
    "WorkspaceApp": ("name", "description"),
    "Track": ("title", "purpose"),
    "OperationalModel": ("name", "version_number"),
    "EntryType": ("name",),
}
EDGE_MEANINGS = {
    "CONTAINS": "Contains",
    "CATALOGS": "Catalogs",
    "HAS_OPERATIONAL_MODEL": "Uses model",
}


class OntologyAccess(WorkspaceProjectionAccess):
    """A fresh per-capture grant set; schema access follows current attachment."""

    def __init__(self, principal_id: str, workspace_id: str) -> None:
        super().__init__(principal_id, workspace_id)
        self._checks: Dict[Tuple[str, str], bool] = {}

    async def _model_allowed(self, model_id: str, owner_id: str = "") -> bool:
        model = await OperationalModel.get(model_id)
        if model is None or model.status != "published":
            return False
        if model.workspace_id and model.workspace_id != self.workspace_id:
            return False
        owners = await model.nodes(
            edge=[HAS_OPERATIONAL_MODEL],
            node=["WorkspaceApp", "Track"],
            direction="in",
            limit=50,
        )
        for owner in owners:
            if owner_id and owner.id != owner_id:
                continue
            # A shared template must not expose another workspace's consumers.
            if owner.workspace_id != self.workspace_id:
                continue
            if owner.attached_operational_model_id != model_id:
                continue
            entity = "Track" if isinstance(owner, Track) else "WorkspaceApp"
            if await self._node_allowed(
                {"id": owner.id, "entity": entity, "context": owner.model_dump()}
            ):
                return True
        return False

    async def _node_allowed(self, row: Dict[str, Any]) -> bool:
        key = (row["entity"], row["id"])
        if key not in self._checks:
            self._checks[key] = await self._evaluate_node(row)
        return self._checks[key]

    async def _evaluate_node(self, row: Dict[str, Any]) -> bool:
        if row["entity"] == "OperationalModel":
            return await self._model_allowed(row["id"])
        if row["entity"] == "EntryType":
            # A readable track ID alone is insufficient: draft and historical
            # type copies may retain it. Require a live structural definition.
            context = row.get("context") or {}
            track_id = str(context.get("track_id") or "")
            if track_id and not await super()._node_allowed(row):
                return False
            entry_type = await EntryType.get(row["id"])
            if entry_type is None or entry_type.track_id != track_id:
                return False
            parents = await entry_type.nodes(
                edge=["CONTAINS"],
                node=["OperationalModel", "Track"],
                direction="in",
                limit=50,
            )
            for parent in parents:
                if isinstance(parent, Track):
                    if track_id and parent.id == track_id:
                        return True
                elif await self._model_allowed(parent.id, owner_id=track_id):
                    return True
            return False
        return await super()._node_allowed(row)

    async def authorize(
        self, kind: Literal["node", "edge"], records: List[Dict[str, Any]]
    ) -> Mapping[str, Optional[Tuple[str, ...]]]:
        if kind == "edge":
            return {row["id"]: () for row in records if row["entity"] in EDGE_MEANINGS}
        # Independent admission checks share only this capture's read caches.
        # Limit concurrency so the permission layer cannot exhaust the DB pool.
        semaphore = asyncio.Semaphore(6)

        async def allowed(row):
            async with semaphore:
                return row["entity"] in NODE_FIELDS and await self._node_allowed(row)

        decisions = await asyncio.gather(*(allowed(row) for row in records))
        grants = {
            row["id"]: NODE_FIELDS[row["entity"]]
            for row, decision in zip(records, decisions)
            if decision
        }
        return grants


async def _require_workspace(principal_id: str, workspace_id: str) -> None:
    if (
        not principal_id
        or not workspace_id
        or not await user_in_workspace_member_pool(principal_id, workspace_id)
    ):
        raise InsufficientPermissionsError(message="Workspace graph unavailable")


def _safe_fields(entry_type: EntryType) -> List[Dict[str, Any]]:
    """Expose only visible field declarations, never defaults/options/targets."""
    fields = entry_type.form_schema.get("fields", [])
    if not isinstance(fields, list):
        return []
    out = []
    for field in fields[:100]:
        if not isinstance(field, dict) or field.get("hidden"):
            continue
        key = field.get("key")
        if not isinstance(key, str) or not key:
            continue
        label = field.get("label")
        field_type = field.get("type")
        out.append(
            OntologyField(
                key=key[:128],
                label=(label if isinstance(label, str) and label else key)[:128],
                type=(
                    field_type if isinstance(field_type, str) and field_type else "text"
                )[:64],
                required=field.get("required") is True,
            ).model_dump()
        )
        if len(out) == 24:
            break
    return out


async def capture_ontology(
    *, principal_id: str, workspace_id: str, spec: OntologyRequest
) -> Dict[str, Any]:
    """Use JV Spatial's read-only traversal with fresh policy on every request."""
    try:
        async with asyncio.timeout(8):
            with isolated_permissions_cache():
                await _require_workspace(principal_id, workspace_id)
                access = OntologyAccess(principal_id, workspace_id)
                context = get_default_context()
                projection = await context.project_graph(
                    ProjectionRequest(
                        root_id=spec.root_id or workspace_id,
                        view="ontology",
                        direction="both",
                        node_types=tuple(NODE_FIELDS),
                        edge_types=tuple(EDGE_MEANINGS),
                        max_depth=spec.max_depth,
                        max_nodes=100,
                        max_edges=180,
                        max_candidates=800,
                        max_edges_per_node=100,
                        max_frontier=100,
                        page_size=50,
                        timeout_seconds=6,
                        max_bytes=49152,
                        max_string=256,
                    ),
                    scope=ProjectionScope(
                        name=f"ws:{workspace_id}",
                        node_query={
                            "$or": [
                                {"id": workspace_id, "entity": "Workspace"},
                                {"context.workspace_id": workspace_id},
                                {"entity": {"$in": ["OperationalModel", "EntryType"]}},
                            ]
                        },
                        node_fields=(
                            "name",
                            "kind",
                            "description",
                            "title",
                            "purpose",
                            "version_number",
                        ),
                        edge_fields=(),
                    ),
                    access=access,
                    semantics=ProjectionSemantics(
                        label_fields={
                            entity: "title" if entity == "Track" else "name"
                            for entity in NODE_FIELDS
                        },
                        edge_types=EDGE_MEANINGS,
                    ),
                )
                payload = projection.to_dict()
                if not payload["meta"]["root_available"]:
                    raise InsufficientPermissionsError(
                        message="Workspace graph unavailable"
                    )
                ids = [
                    node["id"]
                    for node in payload["nodes"]
                    if node["entity"] == "EntryType"
                ]
                types = await batch_get_by_ids(EntryType, ids) if ids else {}
                detail_budget = 12000
                for node in payload["nodes"]:
                    if node["id"] in types:
                        fields = _safe_fields(types[node["id"]])
                        cost = len(json.dumps(fields, ensure_ascii=False).encode())
                        node["schema_fields"] = fields if cost <= detail_budget else []
                        node["schema_fields_limited"] = True
                        if cost <= detail_budget:
                            detail_budget -= cost
                # No host-only traversal statistics or raw schemas reach the UI.
                return {
                    "nodes": payload["nodes"],
                    "edges": payload["edges"],
                    "meta": {
                        "workspace_id": workspace_id,
                        "root_id": payload["meta"]["root_id"],
                        "max_depth": spec.max_depth,
                        "complete": payload["meta"]["complete"],
                        "captured_at": payload["meta"]["captured_at"],
                    },
                }
    except TimeoutError as exc:
        raise ServiceUnavailableError(
            message="Ontology view timed out; choose a narrower root"
        ) from exc


async def list_ontology_roots(
    *, principal_id: str, workspace_id: str, search: str = ""
) -> List[OntologyRoot]:
    """Bound discovery independently of traversal; return no hidden totals."""
    try:
        async with asyncio.timeout(5):
            with isolated_permissions_cache():
                await _require_workspace(principal_id, workspace_id)
                access = OntologyAccess(principal_id, workspace_id)
                context = get_default_context()
                roots = []
                for cls, label_field in ((App, "name"), (Track, "title")):
                    query: Dict[str, Any] = {"context.workspace_id": workspace_id}
                    if search.strip():
                        query[f"context.{label_field}"] = {
                            "$regex": re.escape(search.strip()),
                            "$options": "i",
                        }
                    candidates = await context.find(cls, query, limit=50)
                    entity = "Track" if cls is Track else "WorkspaceApp"
                    for node in candidates:
                        if await access._node_allowed(
                            {
                                "id": node.id,
                                "entity": entity,
                                "context": node.model_dump(),
                            }
                        ):
                            roots.append(
                                OntologyRoot(
                                    id=node.id,
                                    entity=entity,
                                    label=str(getattr(node, label_field))[:256],
                                )
                            )
                return sorted(roots, key=lambda root: (root.label.casefold(), root.id))
    except TimeoutError as exc:
        raise ServiceUnavailableError(
            message="Ontology search timed out; narrow the search"
        ) from exc
