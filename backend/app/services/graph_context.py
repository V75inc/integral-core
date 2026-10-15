"""Integral authorization and semantics over JV Spatial's bounded projection."""

from __future__ import annotations

import asyncio
from time import monotonic
from typing import Any, Dict, List, Literal, Mapping, Optional, Tuple

from jvspatial.core.context import get_default_context

from app.api.errors import (
    BadRequestError,
    InsufficientPermissionsError,
    ServiceUnavailableError,
)
from app.middleware.permissions_cache import isolated_permissions_cache
from app.models.nodes import App, Track
from app.schemas.graph_context import GraphContextRequest
from app.schemas.policy import Resource, Subject
from app.services.graph_context_profile import (
    RECORDED_ENTRY_FIELDS,
    build_recorded_profile,
)
from app.services.graph_hydration import batch_get_by_ids
from app.services.policy_engine import evaluate as policy_evaluate
from app.services.query_boundary import decide_app, parent_app_for_track
from app.services.workspace_permissions import user_in_workspace_member_pool

_NODE_FIELDS = {
    "Workspace": ("name", "kind"),
    "Apps": (),
    "Tracks": (),
    "WorkspaceApp": ("name", "description"),
    "Track": ("title", "purpose"),
    "Entry": ("title", "status"),
    "EntryType": ("name",),
}
_EDGE_FIELDS = ("field_key", "relation_type", "cross_track")
_NODE_MEANINGS = {
    "Workspace": "Authenticated knowledge boundary containing apps and tracks.",
    "Apps": "Workspace registry of apps.",
    "Tracks": "Workspace registry of tracks.",
    "WorkspaceApp": "An app grouping tracks within this workspace.",
    "Track": "A collection of entries with an operational model.",
    "Entry": "A readable information record; selected detail is not readiness verification.",
    "EntryType": "The declared type of an entry in a readable track.",
}
_EDGE_MEANINGS = {
    "CONTAINS": "Source structurally contains target.",
    "CATALOGS": "Source registry catalogs target.",
    "REFERENCES": "Source entry references target entry via a declared field.",
    "Anchors": "Source entry anchors a related track via a declared field.",
    "IS_OF_TYPE": "Source entry has the declared target entry type.",
}


class WorkspaceProjectionAccess:
    """Fail closed before expansion; grants live only for this capture."""

    def __init__(
        self, principal_id: str, workspace_id: str, *, recorded=False, field_grants=None
    ) -> None:
        self.recorded = recorded
        self.field_grants = field_grants or {}
        self.inventory: Dict[str, Dict[str, Any]] = {}
        self.principal_id = principal_id
        self.workspace_id = workspace_id
        self.tracks: Dict[str, Any] = {}
        self.track_apps: Dict[str, Any] = {}

    async def _track(self, track_id: str) -> Any:
        if track_id not in self.tracks:
            self.tracks[track_id] = await Track.get(track_id) if track_id else None
        return self.tracks[track_id]

    async def _parent_app(self, track: Any) -> Any:
        if track.id not in self.track_apps:
            self.track_apps[track.id] = await parent_app_for_track(track)
        return self.track_apps[track.id]

    async def _node_allowed(self, row: Dict[str, Any]) -> bool:
        entity, node_id = row["entity"], row["id"]
        context = row.get("context") or {}
        if entity not in _NODE_FIELDS:
            return False
        if entity == "Workspace":
            return node_id == self.workspace_id
        if entity in {"Apps", "Tracks"}:
            return context.get("workspace_id") == self.workspace_id
        if entity in {"Entry", "EntryType"}:
            track = await self._track(str(context.get("track_id") or ""))
            if track is None or track.workspace_id != self.workspace_id:
                return False
            if not decide_app(await self._parent_app(track)).allowed:
                return False
            kind = "entry" if entity == "Entry" else "track"
            resource_id = node_id if kind == "entry" else track.id
            resource_scope = f"track:{track.id}"
        else:
            if context.get("workspace_id") != self.workspace_id:
                return False
            kind = "app" if entity == "WorkspaceApp" else "track"
            resource_id = node_id
            resource_scope = f"{kind}:{node_id}"
            if kind == "app":
                app = await App.get(node_id)
                if app is None or app.lifecycle_state != "active":
                    return False
            else:
                track = await self._track(node_id)
                if track is None:
                    return False
                # Track metadata is discoverable for an active packaged App,
                # but its Entry records require the App's declared queries.
                app = await self._parent_app(track)
                if app is not None and app.lifecycle_state != "active":
                    return False
        decision = await policy_evaluate(
            subject=Subject(kind="human", id=self.principal_id),
            action=f"{kind}.read",
            resource=Resource(kind=kind, id=resource_id, scope=resource_scope),
        )
        return decision.allowed

    async def authorize(
        self, kind: Literal["node", "edge"], records: List[Dict[str, Any]]
    ) -> Mapping[str, Optional[Tuple[str, ...]]]:
        """Omit denied records; no denied counts or field values reach the model."""
        if kind == "node":
            # Hydrate only this bounded candidate page, never the workspace
            # universe. Multiple entries can share one parent-track read.
            track_ids = (
                {
                    (
                        row["id"]
                        if row["entity"] == "Track"
                        else str((row.get("context") or {}).get("track_id") or "")
                    )
                    for row in records
                    if row["entity"] in {"Track", "Entry", "EntryType"}
                }
                - self.tracks.keys()
                - {""}
            )
            if track_ids:
                tracks = await batch_get_by_ids(Track, sorted(track_ids))
                self.tracks.update({tid: tracks.get(tid) for tid in track_ids})
        grants = {}
        for row in records:
            if kind == "edge":
                if row["entity"] in _EDGE_MEANINGS:
                    grants[row["id"]] = _EDGE_FIELDS
            elif await self._node_allowed(row):
                fields = _NODE_FIELDS[row["entity"]]
                if self.recorded and row["entity"] == "Entry":
                    context = row.get("context") or {}
                    key = (
                        context.get("type_id"),
                        context.get("schema_revision"),
                        context.get("track_id"),
                    )
                    custom = context.get("custom_fields") or {}
                    declared = tuple(
                        field
                        for field in self.field_grants.get(key, ())
                        if isinstance(custom, dict)
                        and (
                            field[14:] not in custom
                            or custom[field[14:]] is None
                            or isinstance(custom[field[14:]], (str, bool, int, float))
                        )
                    )
                    fields = RECORDED_ENTRY_FIELDS + declared
                    if key in self.field_grants and not await self._node_allowed(
                        {
                            "id": str(context.get("type_id") or ""),
                            "entity": "EntryType",
                            "context": {"track_id": context.get("track_id")},
                        }
                    ):
                        fields = tuple(
                            field
                            for field in RECORDED_ENTRY_FIELDS
                            if field not in {"type_id", "schema_revision"}
                        )
                    self.inventory[row["id"]] = row
                grants[row["id"]] = fields
        return grants


class _TransactionProjectionReads:
    """Projection's compatibility protocol over the held transaction only."""

    supports_native_graph_paging = False

    def __init__(self, transaction: Any) -> None:
        self.transaction = transaction

    async def graph_node_batch(self, ids):
        return await self.transaction.find_many("node", ids)

    async def graph_edge_page(self, node_id, *, after=None, limit=50):
        from jvspatial.db.database import Database

        return await Database.graph_edge_page(
            self.transaction, node_id, after=after, limit=limit
        )


async def _project_current_graph(context, request, **kwargs):
    """Bridge older JV transaction handles without escaping their snapshot."""
    database = context.database
    if callable(getattr(database, "graph_node_batch", None)) and callable(
        getattr(database, "graph_edge_page", None)
    ):
        return await context.project_graph(request, **kwargs)
    from types import SimpleNamespace

    from jvspatial.core.graph_projection import project_graph
    from jvspatial.db.transaction import Transaction

    if not isinstance(database, Transaction):
        raise ServiceUnavailableError(message="Graph projection storage is unavailable")
    return await project_graph(
        SimpleNamespace(database=_TransactionProjectionReads(database)),
        request,
        **kwargs,
    )


def _require_projection_root(payload: Dict[str, Any], *, recorded: bool) -> None:
    meta = payload["meta"]
    if meta["root_available"]:
        return
    # A deadline can expire during the root's live authorization reads. That
    # provides no permission decision; keep withholding data but report service
    # availability accurately rather than claiming the caller lost access.
    if "timeout" in meta["stop_reasons"]:
        raise ServiceUnavailableError(message="Graph context capture timed out")
    if recorded and "max_bytes" in meta["stop_reasons"]:
        raise BadRequestError(
            message="Increase max_bytes to represent the recorded graph root"
        )
    raise InsufficientPermissionsError(message="Workspace graph unavailable")


async def capture_workspace_graph(
    *, principal_id: str, workspace_id: str, spec: GraphContextRequest
) -> Dict[str, Any]:
    """Capture evidence under fresh current-user authorization, including in tools."""
    with isolated_permissions_cache():
        return await _capture_workspace_graph(
            principal_id=principal_id, workspace_id=workspace_id, spec=spec
        )


async def _capture_workspace_graph(
    *, principal_id: str, workspace_id: str, spec: GraphContextRequest
) -> Dict[str, Any]:
    """Extract bounded evidence with no process permission cache or model call."""
    if (
        not principal_id
        or not workspace_id
        or not await user_in_workspace_member_pool(principal_id, workspace_id)
    ):
        raise InsufficientPermissionsError(message="Workspace graph unavailable")

    # Public 0.1.1 lacks projections. Keep boot and old-wheel artifacts usable,
    # but refuse this capability explicitly rather than falling back to export.
    from jvspatial.core.graph_projection import (
        ProjectionRequest,
        ProjectionScope,
        ProjectionSemantics,
    )

    request = ProjectionRequest(
        **spec.model_dump(exclude={"root_id", "detail"}),
        root_id=spec.root_id or workspace_id,
        node_types=tuple(_NODE_FIELDS),
        max_frontier=spec.max_nodes,
        page_size=50,
        timeout_seconds=3,
        max_string=256,
    )
    fields = tuple(
        dict.fromkeys(field for values in _NODE_FIELDS.values() for field in values)
    )
    scope = ProjectionScope(
        name=f"ws:{workspace_id}",
        node_query={
            "$or": [
                {"id": workspace_id, "entity": "Workspace"},
                {"context.workspace_id": workspace_id},
                {"entity": {"$in": ["Entry", "EntryType"]}},
            ]
        },
        node_fields=fields,
        edge_fields=_EDGE_FIELDS,
    )
    semantics = ProjectionSemantics(
        node_types=_NODE_MEANINGS,
        edge_types=_EDGE_MEANINGS,
        label_fields={
            entity: "title" if entity in {"Entry", "Track"} else "name"
            for entity in _NODE_FIELDS
        },
    )
    context = get_default_context()
    access = WorkspaceProjectionAccess(principal_id, workspace_id)
    config = None
    if spec.detail == "recorded":
        if spec.max_candidates < 2:
            raise BadRequestError(
                message="Recorded detail requires at least two candidates"
            )
        started = monotonic()
        first_access = WorkspaceProjectionAccess(
            principal_id, workspace_id, recorded=True
        )
        # Native traversal discovers represented readable types. Both passes
        # share the caller's candidate limit and the host capture deadline.
        first_scope = ProjectionScope(
            name=scope.name,
            node_query=scope.node_query,
            node_fields=tuple(dict.fromkeys(fields + RECORDED_ENTRY_FIELDS)),
            edge_fields=_EDGE_FIELDS,
        )
        first_request = ProjectionRequest(
            **{
                **request.model_dump(),
                "max_candidates": max(1, spec.max_candidates // 2),
            }
        )
        first = await _project_current_graph(
            context,
            first_request,
            scope=first_scope,
            access=first_access,
            semantics=semantics,
        )
        _require_projection_root(first.to_dict(), recorded=True)
        remaining = spec.max_candidates - first.statistics["candidate_records"]
        timeout = 3 - (monotonic() - started)
        if remaining < 1 or timeout <= 0:
            raise BadRequestError(
                message="Use a narrower graph root for recorded detail"
            )
        access = WorkspaceProjectionAccess(principal_id, workspace_id, recorded=True)
        try:
            grants, rules, config = await asyncio.wait_for(
                build_recorded_profile(
                    first.to_dict()["nodes"],
                    first_access.inventory,
                    access,
                    _NODE_FIELDS,
                ),
                timeout=timeout,
            )
        except asyncio.TimeoutError as exc:
            raise ServiceUnavailableError(
                message="Graph context capture timed out"
            ) from exc
        # Permission decisions from type discovery must not survive into the
        # final capture. The context manager bypasses the process TTL cache.
        access = WorkspaceProjectionAccess(
            principal_id, workspace_id, recorded=True, field_grants=grants
        )
        semantics = ProjectionSemantics(
            **{**semantics.model_dump(), "relation_rules": rules}
        )
        fields = tuple(
            dict.fromkeys(
                fields
                + RECORDED_ENTRY_FIELDS
                + tuple(field for values in grants.values() for field in values)
            )
        )
        scope = ProjectionScope(
            name=scope.name,
            node_query=scope.node_query,
            node_fields=fields,
            edge_fields=_EDGE_FIELDS,
        )
        timeout = 3 - (monotonic() - started)
        if timeout <= 0:
            raise ServiceUnavailableError(message="Graph context capture timed out")
        request = ProjectionRequest(
            **{
                **request.model_dump(),
                "max_candidates": remaining,
                "timeout_seconds": timeout,
            }
        )
    with isolated_permissions_cache():
        projection = await _project_current_graph(
            context,
            request,
            scope=scope,
            access=access,
            semantics=semantics,
            context_config=config,
        )
    payload = projection.to_dict()
    _require_projection_root(payload, recorded=spec.detail == "recorded")
    # Host-only work statistics can reveal inaccessible cardinalities.
    return payload
