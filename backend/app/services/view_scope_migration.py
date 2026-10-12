"""Bounded, fail-closed migration for legacy saved-View scope edges.

Automatic inference requires a unique catalog/model/Track path. Multiple
Track attachments are ambiguous even when a scalar Track ID names one of
those Tracks. They remain withheld for an explicit owner-reviewed repair.
"""

from dataclasses import asdict, dataclass
from typing import Any, Literal, TypedDict

from jvspatial.core import Edge
from jvspatial.core.context import get_default_context

from app.exceptions import AuthorizationUnavailableError
from app.models.edges import CATALOGS, HAS_OPERATIONAL_MODEL, HAS_VIEW
from app.models.nodes import OperationalModel, Track, View, Views
from app.services.authority_facts import primary_node_record
from app.services.resource_location import KNOWN_APP_STATES, locate_resource


@dataclass(frozen=True)
class ViewScopeMigrationItem:
    view_id: str
    status: str
    reason: str
    workspace_id: str = ""
    track_id: str = ""
    model_id: str = ""


class ViewScopeMigrationReport(TypedDict):
    mode: Literal["apply", "dry_run"]
    items: list[dict[str, Any]]
    counts: dict[str, int]


def text_cache(row: dict, key: str) -> str | None:
    """Read an exact text cache without normalizing conflicting representations."""
    context = row.get("context")
    if not isinstance(context, dict):
        return None
    value = context.get(key)
    if key in row and row[key] != value:
        return None
    return value if isinstance(value, str) and value == value.strip() else None


async def classify_view_scope(view_id: str) -> ViewScopeMigrationItem:
    """Inspect complete bounded parent sets, without warming or repairing facts."""
    context = get_default_context()
    row = await primary_node_record(context.database, View, view_id)

    def quarantine(reason: str):
        return ViewScopeMigrationItem(view_id, "quarantined", reason)

    if row is None:
        return quarantine("missing_view")
    data = row["context"]
    template = data.get("is_template")
    if not isinstance(template, bool) or (
        "is_template" in row and row["is_template"] is not template
    ):
        return quarantine("invalid_template_flag")
    track_id = text_cache(row, "track_id")
    model_cache = text_cache(row, "operational_model_id")
    if track_id is None or model_cache is None:
        return quarantine("invalid_scope_cache")
    if template or not track_id:
        return ViewScopeMigrationItem(view_id, "model_only", "not_a_saved_track_view")
    scopes = await context.database.find(
        "edge", {"target": view_id, "entity": HAS_VIEW._entity_name()}, limit=2
    )
    if scopes:
        location = await locate_resource(
            "view", view_id, allowed_app_states=KNOWN_APP_STATES
        )
        if location is None:
            return quarantine("invalid_existing_scope")
        return ViewScopeMigrationItem(
            view_id,
            "already_wired",
            "canonical_scope",
            location.workspace_id,
            location.track_id,
            model_cache,
        )
    catalogs = await context.database.find(
        "edge", {"target": view_id, "entity": CATALOGS._entity_name()}, limit=2
    )
    if len(catalogs) != 1:
        return quarantine("ambiguous_or_missing_catalog")
    registry = await primary_node_record(
        context.database, Views, catalogs[0].get("source", "")
    )
    if registry is None:
        return quarantine("invalid_catalog_parent")
    registry_parents = await context.database.find(
        "edge", {"target": registry["id"], "entity": Edge._entity_name()}, limit=2
    )
    if len(registry_parents) != 1:
        return quarantine("ambiguous_or_missing_model")
    model_id = registry_parents[0].get("source", "")
    model = await primary_node_record(context.database, OperationalModel, model_id)
    if (
        model is None
        or text_cache(registry, "operational_model_id") != model_id
        or (model_cache and model_cache != model_id)
    ):
        return quarantine("model_cache_mismatch")
    attachments = await context.database.find(
        "edge",
        {"target": model_id, "entity": HAS_OPERATIONAL_MODEL._entity_name()},
        limit=2,
    )
    if len(attachments) != 1:
        return quarantine("ambiguous_model_track_scope")
    canonical_track = attachments[0].get("source", "")
    track = await primary_node_record(context.database, Track, canonical_track)
    if (
        track is None
        or canonical_track != track_id
        or text_cache(track, "attached_operational_model_id") != model_id
    ):
        return quarantine("track_cache_mismatch")
    location = await locate_resource(
        "track", track_id, allowed_app_states=KNOWN_APP_STATES
    )
    if location is None:
        return quarantine("invalid_track_location")
    return ViewScopeMigrationItem(
        view_id,
        "eligible",
        "unique_legacy_structural_path",
        location.workspace_id,
        track_id,
        model_id,
    )


async def apply_view_scope_item(item: ViewScopeMigrationItem) -> ViewScopeMigrationItem:
    """Reclassify under the held workspace and View locks; no stale-plan adoption.

    Apply requires the supported graph transaction. Dry-run works on every
    adapter. Source-failure exceptions abort instead of becoming skipped rows.
    """
    from app.services.app_operations.transaction_scope import postgres_graph_transaction
    from app.services.authorization_scope import authorization_scope

    if item.status != "eligible":
        return item
    async with authorization_scope(item.workspace_id, authority_mutation=True):
        async with postgres_graph_transaction() as transaction:
            locked = await transaction.find_one_and_update(
                "node",
                {
                    "id": item.view_id,
                    "entity": View._entity_name(),
                    "context.track_id": item.track_id,
                },
                {"$set": {"context.track_id": item.track_id}},
            )
            if locked is None:
                raise AuthorizationUnavailableError()
            current = await classify_view_scope(item.view_id)
            if current != item:
                raise AuthorizationUnavailableError()
            context = get_default_context()
            track = await context._deserialize_entity(
                Track, await primary_node_record(context.database, Track, item.track_id)
            )
            view = await context._deserialize_entity(
                View, await primary_node_record(context.database, View, item.view_id)
            )
            if not view.operational_model_id:
                view.operational_model_id = item.model_id
                await view.save()
            await track.connect(view, edge=HAS_VIEW)
            if (
                await locate_resource(
                    "view", view.id, allowed_app_states=KNOWN_APP_STATES
                )
                is None
            ):
                raise AuthorizationUnavailableError()
            return ViewScopeMigrationItem(
                item.view_id,
                "wired",
                "applied_unique_scope",
                item.workspace_id,
                item.track_id,
                item.model_id,
            )


async def migrate_view_scopes(*, apply: bool = False) -> ViewScopeMigrationReport:
    """Keyset-paged inventory with metadata-only decisions for operator review."""
    database = get_default_context().database
    report: ViewScopeMigrationReport = {
        "mode": "apply" if apply else "dry_run",
        "items": [],
        "counts": {},
    }
    cursor = ""
    while True:
        query = {"entity": View._entity_name()}
        if cursor:
            query["id"] = {"$gt": cursor}
        rows = await database.find("node", query, limit=128, sort=[("id", 1)])
        for row in rows:
            item = await classify_view_scope(row["id"])
            if apply and item.status == "eligible":
                item = await apply_view_scope_item(item)
            report["items"].append(asdict(item))
            report["counts"][item.status] = report["counts"].get(item.status, 0) + 1
        if len(rows) < 128:
            return report
        next_cursor = rows[-1].get("id")
        if not isinstance(next_cursor, str) or next_cursor <= cursor:
            raise AuthorizationUnavailableError()
        cursor = next_cursor
