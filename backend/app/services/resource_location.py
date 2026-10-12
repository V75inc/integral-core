"""Canonical bounded resource containment for execution scope gates."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from jvspatial.core import Edge, Walker
from jvspatial.core.context import get_default_context
from jvspatial.core.decorators import on_visit
from jvspatial.core.entities import Root
from pydantic import PrivateAttr

from app.exceptions import AuthorizationUnavailableError
from app.models.edges import CATALOGS, CONTAINS, HAS_OPERATIONAL_MODEL, HAS_VIEW
from app.models.nodes import (
    APP_NODE_ID,
    WORKSPACES_REGISTRY_ID,
    App,
    Apps,
    Entry,
    IntegralApp,
    OperationalModel,
    Track,
    Tracks,
    View,
    Views,
    Workspace,
    Workspaces,
)
from app.services.authority_facts import (
    primary_node_record,
    relationship_flag,
    relationship_literal,
    required_node_text,
)

# Internal capability paths select an exact lifecycle set. The default is
# execution-safe; no HTTP caller can supply or expand these sets.
ACTIVE_APP_STATES = frozenset({"active"})
DATA_APP_STATES = frozenset({"active", "paused"})
KNOWN_APP_STATES = frozenset(
    {"installing", "awaiting_settings", "active", "paused", "failed", "uninstalled"}
)
MANAGEMENT_APP_STATES = KNOWN_APP_STATES - {"uninstalled"}


@dataclass(frozen=True)
class ResourceLocation:
    workspace_id: str
    app_id: str = ""
    track_id: str = ""


class ResourceLocationWalker(Walker):
    """Resolve the complete raw parent set before hydrating or granting."""

    workspace_id: str = ""
    app_id: str = ""
    track_id: str = ""
    valid: bool = True
    allowed_app_states: frozenset[str] = ACTIVE_APP_STATES
    expected_view_model_id: str = ""
    view_model_matched: bool = False
    _records: dict[str, dict] = PrivateAttr(default_factory=dict)
    _root_checked: set[str] = PrivateAttr(default_factory=set)
    _parents: dict[tuple[str, str], list[dict]] = PrivateAttr(default_factory=dict)
    _missing: set[str] = PrivateAttr(default_factory=set)

    async def read_node(self, model: Any, node_id: str, context: Any) -> Any:
        row = self._records.get(node_id)
        if row is None and node_id in self._missing:
            return None
        if row is None:
            row = await primary_node_record(context.database, model, node_id)
            if row is None:
                return None
            self._records[node_id] = row
        if row.get("entity") != model._entity_name():
            raise AuthorizationUnavailableError()
        node = await context._deserialize_entity(model, row)
        if node is None:
            raise AuthorizationUnavailableError()
        return node

    async def parent_rows(
        self, node_id: str, edge: Any, maximum: int, context: Any
    ) -> list[dict]:
        """Bound candidates with one extra row; never let hydration hide an edge."""
        key = (node_id, edge._entity_name())
        rows = self._parents.get(key)
        if rows is None:
            rows = await context.database.find(
                "edge",
                {"target": node_id, "entity": edge._entity_name()},
                limit=maximum + 1,
            )
        if any(
            row.get("target") != node_id
            or row.get("entity") != edge._entity_name()
            or not isinstance(row.get("source"), str)
            or not row["source"]
            for row in rows
        ):
            raise AuthorizationUnavailableError()
        return rows

    async def source_record(
        self, row: dict, models: tuple, context: Any
    ) -> dict | None:
        """Check every parent discriminator before selecting its visit hook."""
        cached = self._records.get(row["source"])
        if row["source"] in self._missing:
            return None
        rows = (
            [cached]
            if cached is not None
            else await context.database.find("node", {"id": row["source"]}, limit=2)
        )
        if not rows:
            return None
        if len(rows) != 1 or rows[0].get("id") != row["source"]:
            raise AuthorizationUnavailableError()
        model = next(
            (
                model
                for model in models
                if rows[0].get("entity") == model._entity_name()
            ),
            None,
        )
        if model is None:
            return None
        if not isinstance(rows[0].get("context"), dict):
            raise AuthorizationUnavailableError()
        self._records[row["source"]] = rows[0]
        return rows[0]

    async def source_node(self, row: dict, models: tuple, context: Any) -> Any:
        record = await self.source_record(row, models, context)
        if record is None:
            return None
        model = next(
            model for model in models if record["entity"] == model._entity_name()
        )
        return await self.read_node(model, record["id"], context)

    async def check_workspace_root(self, workspace_id: str, context: Any) -> bool:
        """Require the unique named catalog path, ignoring optional edge metadata."""
        if workspace_id in self._root_checked:
            return True
        roots = {
            "n.Root.root": Root,
            APP_NODE_ID: IntegralApp,
            WORKSPACES_REGISTRY_ID: Workspaces,
        }
        if all(node_id in self._records for node_id in roots):
            rows = [self._records[node_id] for node_id in roots]
        else:
            rows = await context.database.find(
                "node", {"id": {"$in": list(roots)}}, limit=len(roots) + 1
            )
        if len(rows) != len(roots) or {row.get("id") for row in rows} != set(roots):
            return False
        for row in rows:
            if row.get("entity") != roots[row["id"]]._entity_name() or not isinstance(
                row.get("context"), dict
            ):
                raise AuthorizationUnavailableError()
            self._records[row["id"]] = row
        # One bounded raw read proves both shell edges and the complete incoming
        # workspace authority-parent set. An extra or malformed parent remains
        # visible; optional audit metadata never enters the proof.
        shell = (
            ("n.Root.root", APP_NODE_ID, Edge._entity_name()),
            (APP_NODE_ID, WORKSPACES_REGISTRY_ID, Edge._entity_name()),
        )
        rows = await context.database.find(
            "edge",
            {
                "$or": [
                    {"source": source, "target": target, "entity": entity}
                    for source, target, entity in shell
                ]
                + [
                    {
                        "target": workspace_id,
                        "entity": {
                            "$in": [CATALOGS._entity_name(), CONTAINS._entity_name()]
                        },
                    }
                ]
            },
            limit=4,
        )
        if len(rows) != 3:
            return False
        identities = [
            (row.get("source"), row.get("target"), row.get("entity")) for row in rows
        ]
        expected = [
            *shell,
            (WORKSPACES_REGISTRY_ID, workspace_id, CATALOGS._entity_name()),
        ]
        if any(identities.count(identity) != 1 for identity in expected):
            return False
        self._root_checked.add(workspace_id)
        return True

    @on_visit(Workspace)
    async def on_workspace(self, here: Workspace) -> None:
        context = await here.get_context()
        here = await self.read_node(Workspace, here.id, context)
        if here is None:
            self.valid = False
            return
        relationship_literal(
            self._records[here.id], "kind", {"personal", "organization"}
        )
        if not await self.check_workspace_root(here.id, context):
            self.valid = False
            return
        self.workspace_id = here.id

    async def check_workspace_catalog(self, node: Any, kind: str) -> bool:
        from app.services.app_graph import _workspace_branch_id

        context = await node.get_context()
        workspace_id = required_node_text(self._records[node.id], "workspace_id")
        if self.workspace_id and self.workspace_id != workspace_id:
            return False
        workspace = await self.read_node(Workspace, workspace_id, context)
        if workspace is None:
            return False
        relationship_literal(
            self._records[workspace_id], "kind", {"personal", "organization"}
        )
        registry_class = Apps if kind == "apps" else Tracks
        branch_id = _workspace_branch_id(workspace_id, kind)
        branch = await self.read_node(registry_class, branch_id, context)
        if branch is None or (
            required_node_text(self._records[branch_id], "workspace_id") != workspace_id
        ):
            return False
        if not await self.check_workspace_root(workspace_id, context):
            return False
        parents = await self.parent_rows(branch_id, CONTAINS, 1, context)
        if len(parents) != 1 or parents[0]["source"] != workspace_id:
            return False
        registries = await self.parent_rows(node.id, CATALOGS, 1, context)
        if len(registries) != 1 or registries[0]["source"] != branch_id:
            return False
        self.workspace_id = workspace_id
        return True

    @on_visit(View)
    async def on_view(self, here: View) -> None:
        """Require one Track scope plus matching model catalog facts."""
        context = await here.get_context()
        record = self._records[here.id]
        model_only = bool(self.expected_view_model_id)
        if "is_template" not in record["context"] and "is_template" not in record:
            raise AuthorizationUnavailableError()
        is_template = relationship_flag(record, "is_template")
        if is_template and not model_only:
            self.valid = False
            return
        parents = await self.parent_rows(here.id, HAS_VIEW, 1, context)
        catalogs = await self.parent_rows(here.id, CATALOGS, 1, context)
        if len(catalogs) != 1 or (len(parents) != (0 if model_only else 1)):
            self.valid = False
            return
        track = (
            None
            if model_only
            else await self.source_node(parents[0], (Track,), context)
        )
        registry = await self.source_node(catalogs[0], (Views,), context)
        if (track is None and not model_only) or registry is None:
            self.valid = False
            return
        model_id = required_node_text(record, "operational_model_id")
        if (
            required_node_text(record, "track_id", allow_empty=model_only)
            != ("" if model_only else track.id)
            or required_node_text(self._records[registry.id], "operational_model_id")
            != model_id
            or (model_only and model_id != self.expected_view_model_id)
            or (
                not model_only
                and required_node_text(
                    self._records[track.id], "attached_operational_model_id"
                )
                != model_id
            )
        ):
            self.valid = False
            return
        model = await self.read_node(OperationalModel, model_id, context)
        registry_parents = await self.parent_rows(registry.id, Edge, 1, context)
        attachments = (
            []
            if model_only
            else await context.database.find(
                "edge",
                {"source": track.id, "entity": HAS_OPERATIONAL_MODEL._entity_name()},
                limit=2,
            )
        )
        if (
            model is None
            or len(registry_parents) != 1
            or registry_parents[0]["source"] != model_id
            or (
                not model_only
                and (len(attachments) != 1 or attachments[0].get("target") != model_id)
            )
        ):
            self.valid = False
            return
        if model_only:
            # Membership in an explicitly authorized model is a separate
            # contract; it does not grant Track or workspace data access.
            self.view_model_matched = True
        else:
            await self.visit(track)

    @on_visit(Entry)
    async def on_entry(self, here: Entry) -> None:
        context = await here.get_context()
        relationship_literal(
            self._records[here.id], "visibility", {"inherit", "private"}
        )
        parents = await self.parent_rows(here.id, CONTAINS, 1, context)
        if len(parents) != 1 or (
            required_node_text(self._records[here.id], "track_id")
            != parents[0]["source"]
        ):
            self.valid = False
            return
        parent = await self.source_node(parents[0], (Track,), context)
        if parent is None:
            self.valid = False
            return
        await self.visit(parent)

    @on_visit(Track)
    async def on_track(self, here: Track) -> None:
        context = await here.get_context()
        relationship_literal(
            self._records[here.id],
            "visibility",
            {"inherit", "private", "workspace", "organization", "public"},
        )
        if not await self.check_workspace_catalog(here, "tracks"):
            self.valid = False
            return
        self.track_id = here.id
        parents = await self.parent_rows(here.id, CONTAINS, 2, context)
        if len(parents) > 2:
            self.valid = False
            return
        apps = []
        workspace_parents = []
        for row in parents:
            parent = await self.source_record(row, (Workspace, App), context)
            if parent is None:
                self.valid = False
                return
            if parent["entity"] == Workspace._entity_name():
                workspace_parents.append(parent)
            else:
                apps.append(parent)
        if (
            len(apps) > 1
            or len(workspace_parents) > 1
            or any(parent["id"] != self.workspace_id for parent in workspace_parents)
        ):
            self.valid = False
            return
        if apps:
            parent = await self.read_node(App, apps[0]["id"], context)
            await self.visit(parent)

    @on_visit(App)
    async def on_app(self, here: App) -> None:
        context = await here.get_context()
        relationship_literal(
            self._records[here.id],
            "visibility",
            {"private", "workspace", "organization", "public"},
        )
        state = relationship_literal(
            self._records[here.id], "lifecycle_state", set(KNOWN_APP_STATES)
        )
        if state not in self.allowed_app_states:
            self.valid = False
            return
        if not await self.check_workspace_catalog(here, "apps"):
            self.valid = False
            return
        parents = await self.parent_rows(here.id, CONTAINS, 1, context)
        if len(parents) > 1:
            self.valid = False
            return
        for row in parents:
            parent = await self.source_node(row, (Workspace,), context)
            if parent is None or parent.id != self.workspace_id:
                self.valid = False
                return
        self.app_id = here.id


async def locate_resource(
    kind: str,
    resource_id: str,
    *,
    allowed_app_states: frozenset[str] = ACTIVE_APP_STATES,
    records_out: dict[str, dict] | None = None,
) -> ResourceLocation | None:
    """Resolve live canonical scope; deny corrupt facts and surface read outages."""
    model = {
        "entry": Entry,
        "track": Track,
        "app": App,
        "workspace": Workspace,
        "view": View,
    }.get(kind)
    if model is None or not resource_id:
        return None
    try:
        context = get_default_context()
        walker = ResourceLocationWalker(allowed_app_states=allowed_app_states)
        node = await walker.read_node(model, resource_id, context)
        if node is None:
            return None
        await walker.spawn(node)
        if any(
            isinstance(report, dict) and "hook_error" in report
            for report in await walker.get_report()
        ):
            raise AuthorizationUnavailableError()
        if not walker.valid or not walker.workspace_id:
            return None
        if records_out is not None:
            records_out.update(walker._records)
        return ResourceLocation(walker.workspace_id, walker.app_id, walker.track_id)
    except Exception as exc:
        raise AuthorizationUnavailableError() from exc


async def locate_resources(
    kind: str,
    resource_ids: list[str],
    *,
    workspace_hints: list[str] | None = None,
    allowed_app_states: frozenset[str] = ACTIVE_APP_STATES,
    records_out: dict[str, dict] | None = None,
    selected_records: list[dict] | None = None,
) -> dict[str, ResourceLocation | None]:
    """Batch fresh raw ancestors; each candidate still traverses its complete path.

    Facts exist only inside this invocation, never as a reusable allow. Bounded
    raw frontiers preserve extra parents and unhydratable nodes for the Walker.
    """
    from app.services.app_graph import _workspace_branch_id

    model = {"entry": Entry, "track": Track, "app": App}.get(kind)
    if model is None or not resource_ids:
        return {}
    ids = list(dict.fromkeys(resource_ids))
    if len(ids) > 500:
        bulk_result: dict[str, ResourceLocation | None] = {}
        for offset in range(0, len(ids), 500):
            bulk_result.update(
                await locate_resources(
                    kind,
                    ids[offset : offset + 500],
                    workspace_hints=workspace_hints,
                    allowed_app_states=allowed_app_states,
                    records_out=records_out,
                    selected_records=[
                        row
                        for row in selected_records or []
                        if row.get("id") in ids[offset : offset + 500]
                    ],
                )
            )
        return bulk_result
    try:
        context = get_default_context()
        records: dict[str, dict] = {}
        for row in selected_records or []:
            node_id = row.get("id")
            if (
                node_id not in ids
                or node_id in records
                or row.get("entity") != model._entity_name()
                or not isinstance(row.get("context"), dict)
            ):
                raise AuthorizationUnavailableError()
            records[node_id] = row
        missing: set[str] = set()
        parents: dict[tuple[str, str], list[dict]] = {}
        seen: set[str] = set()
        pending = set(ids) | {"n.Root.root", APP_NODE_ID, WORKSPACES_REGISTRY_ID}
        if kind == "entry":
            pending.update(
                required_node_text(row, "track_id") for row in records.values()
            )
        # Hints only widen the fresh prefetch. Raw facts still prove all links;
        # a stale scalar can never determine the returned scope.
        for workspace_id in list(dict.fromkeys(workspace_hints or []))[:500]:
            pending.update(
                {
                    workspace_id,
                    _workspace_branch_id(workspace_id, "apps"),
                    _workspace_branch_id(workspace_id, "tracks"),
                }
            )
        for _ in range(5):
            frontier = pending - seen
            if not frontier:
                break
            seen.update(frontier)
            unread = frontier - records.keys()
            rows = (
                await context.database.find(
                    "node", {"id": {"$in": sorted(unread)}}, limit=len(unread) + 1
                )
                if unread
                else []
            )
            row_ids = [row.get("id") for row in rows]
            if len(row_ids) != len(set(row_ids)) or not set(row_ids).issubset(unread):
                raise AuthorizationUnavailableError()
            if any(not isinstance(row.get("context"), dict) for row in rows):
                raise AuthorizationUnavailableError()
            records.update({row["id"]: row for row in rows})
            missing.update(unread - set(row_ids))
            edge_rows = await context.database.find(
                "edge",
                {
                    "target": {"$in": sorted(frontier)},
                    "entity": {
                        "$in": [CATALOGS._entity_name(), CONTAINS._entity_name()]
                    },
                },
                limit=4 * len(frontier) + 1,
            )
            if len(edge_rows) > 4 * len(frontier):
                raise AuthorizationUnavailableError()
            for target in frontier:
                for edge in (CATALOGS, CONTAINS):
                    parents[(target, edge._entity_name())] = []
            pending = set()
            for row in edge_rows:
                target, entity, source = (
                    row.get("target"),
                    row.get("entity"),
                    row.get("source"),
                )
                if (
                    (target, entity) not in parents
                    or not isinstance(source, str)
                    or not source
                ):
                    raise AuthorizationUnavailableError()
                parents[(target, entity)].append(row)
                pending.add(source)
            for row in [records[node_id] for node_id in frontier if node_id in records]:
                entity = row.get("entity")
                if entity in (App._entity_name(), Track._entity_name()):
                    workspace_id = required_node_text(row, "workspace_id")
                    pending.add(workspace_id)
                    kind_name = "apps" if entity == App._entity_name() else "tracks"
                    pending.add(_workspace_branch_id(workspace_id, kind_name))
        if pending - seen:
            raise AuthorizationUnavailableError()
        result: dict[str, ResourceLocation | None] = {}
        if records_out is not None:
            records_out.update(records)
        checked: set[str] = set()
        for node_id in ids:
            walker = ResourceLocationWalker(allowed_app_states=allowed_app_states)
            walker._records = records
            walker._parents = parents
            walker._missing = missing
            walker._root_checked = checked
            node = await walker.read_node(model, node_id, context)
            if node is None:
                result[node_id] = None
                continue
            await walker.spawn(node)
            if any(
                isinstance(report, dict) and "hook_error" in report
                for report in await walker.get_report()
            ):
                raise AuthorizationUnavailableError()
            result[node_id] = (
                ResourceLocation(walker.workspace_id, walker.app_id, walker.track_id)
                if walker.valid and walker.workspace_id
                else None
            )
        return result
    except Exception as exc:
        raise AuthorizationUnavailableError() from exc
