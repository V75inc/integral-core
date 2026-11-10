"""Schema-derived relation choices, with fresh canonical audience admission."""

import re

from jvspatial.api.exceptions import InsufficientPermissionsError, ResourceNotFoundError

from app.exceptions import BadRequestError
from app.middleware.permissions_cache import computation_fact_scope
from app.models.edges import CONTAINS
from app.models.nodes import App, Track
from app.schemas.policy import Resource, Subject
from app.schemas.relation_options import (
    RelationOption,
    RelationOptionsRequest,
    RelationOptionsResponse,
)
from app.services.entry_access_projection import merge_queries, select_page
from app.services.entry_search import entry_search_query_clause
from app.services.operational_model_compile import (
    relation_allows_cross_track,
    slug_manifest_key,
)
from app.services.operational_model_entry_fields import relation_track_type
from app.services.perf_trace import perf_span
from app.services.policy_engine import evaluate
from app.services.relation_runtime import read_cross_app_label, resolve_target_app
from app.services.resource_location import DATA_APP_STATES, locate_resource


async def _require_read(user_id: str, kind: str, node_id: str, track_id: str) -> None:
    decision = await evaluate(
        subject=Subject(kind="human", id=user_id),
        action=f"{kind}.read",
        resource=Resource(kind=kind, id=node_id, scope=f"track:{track_id}"),
    )
    if not decision.allowed:
        raise InsufficientPermissionsError(message="Relation source is not accessible")


async def relation_options(
    user_id: str, workspace_id: str, params: RelationOptionsRequest
) -> RelationOptionsResponse:
    """Resolve schema-derived relation choices under current source and target access."""
    # The 10K picker probe spent ~30ms repeating source authorization within
    # a ~70–90ms read. Reuse only this source computation's current facts;
    # target admission below runs after the scope has ended.
    with computation_fact_scope():
        async with perf_span("relations.source"):
            location = await locate_resource(
                "track", params.track_id, allowed_app_states=DATA_APP_STATES
            )
            if location is None or location.workspace_id != workspace_id:
                raise ResourceNotFoundError(message="Relation source not found")
            await _require_read(user_id, "track", params.track_id, params.track_id)
            if params.entry_id:
                entry_location = await locate_resource(
                    "entry", params.entry_id, allowed_app_states=DATA_APP_STATES
                )
                if entry_location is None or entry_location.track_id != params.track_id:
                    raise ResourceNotFoundError(
                        message="Relation source entry not found"
                    )
                await _require_read(user_id, "entry", params.entry_id, params.track_id)
            source = await Track.get(params.track_id)
            if source is None:
                raise ResourceNotFoundError(message="Relation source not found")
            from app.services.app_graph import get_track_attached_operational_model

            model = await get_track_attached_operational_model(source)
            types = (
                await model.nodes(edge=[CONTAINS], node=["EntryType"], limit=1001)
                if model
                else []
            )
            entry_type = next(
                (item for item in types if item.id == params.entry_type_id), None
            )
            if len(types) > 1000 or entry_type is None:
                raise ResourceNotFoundError(
                    message="Entry type not found on source model"
                )
            field = next(
                (
                    f
                    for f in (entry_type.form_schema or {}).get("fields", [])
                    if f.get("key") == params.field_key
                ),
                None,
            )
            if field is None or field.get("type") != "relation":
                raise ResourceNotFoundError(message="Relation field not found")
            relation = field.get("relation") or {}
            if relation.get("target", "entry") != "entry":
                return RelationOptionsResponse()

    async with perf_span("relations.candidates"):
        target_app_id = location.app_id
        if relation.get("target_app"):
            if not relation.get("allow_cross_app"):
                raise BadRequestError(
                    message="Relation target App requires allow_cross_app"
                )
            source_app = await App.get(location.app_id)
            if source_app is None:
                raise ResourceNotFoundError(message="Relation source App not found")
            target_app_id = await resolve_target_app(
                workspace_id=workspace_id,
                target_app_key=relation["target_app"],
                resolution=relation.get("resolution", "workspace"),
                source_app=source_app,
            )
            target_app = await App.get(target_app_id)
            if target_app is None or target_app.lifecycle_state != "active":
                return RelationOptionsResponse()
        elif relation.get("allow_cross_app"):
            raise BadRequestError(
                message="Cross-App relation requires a declared target App"
            )
        if relation_allows_cross_track(relation):
            app_node = await App.get(target_app_id) if target_app_id else None
            tracks = (
                await app_node.nodes(edge=[CONTAINS], node=["Track"], limit=1001)
                if app_node
                else [source]
            )
        else:
            tracks = [source]
        if len(tracks) > 1000:
            raise BadRequestError(
                message="Relation target has too many tracks; narrow the schema"
            )
        track_keys = {
            slug_manifest_key(str(x)) for x in relation.get("target_track_types", [])
        }
        candidates = []
        for track in tracks:
            if track.workspace_id != workspace_id:
                continue
            if (
                track_keys
                and slug_manifest_key(await relation_track_type(track))
                not in track_keys
            ):
                continue
            candidates.append(track)
        if not candidates:
            return RelationOptionsResponse()
        track_by_id = {t.id: t for t in candidates}
        query = {"context.track_id": {"$in": list(track_by_id)}}
        type_keys = {
            slug_manifest_key(str(x)) for x in relation.get("target_entry_types", [])
        }
        if type_keys:
            # A shared model can serve several Tracks; resolve its types once.
            target_models = {}
            for track in candidates:
                target_model = await get_track_attached_operational_model(track)
                if target_model:
                    target_models[target_model.id] = target_model
            target_types = {}
            for target_model in target_models.values():
                items = await target_model.nodes(
                    edge=[CONTAINS], node=["EntryType"], limit=1001
                )
                if len(items) > 1000:
                    raise BadRequestError(
                        message="Relation model has too many entry types"
                    )
                for item in items:
                    aliases = {
                        slug_manifest_key(item.name),
                        slug_manifest_key(
                            str(
                                (item.form_schema or {}).get(
                                    "_manifest_entry_type_key", ""
                                )
                            )
                        ),
                    }
                    if aliases & type_keys:
                        target_types[item.id] = item
            if not target_types:
                return RelationOptionsResponse()
            query = merge_queries(
                query, {"context.type_id": {"$in": list(target_types)}}
            )
        query = merge_queries(
            query, entry_search_query_clause(re.escape(params.q)) or {}
        )

    async with perf_span("relations.page") as span:
        entries, metadata = await select_page(
            user_id,
            query,
            workspace_id=workspace_id,
            app_id=target_app_id or None,
            limit=params.limit,
            cursor=params.cursor,
            include_total=False,
            sort=[("id", 1)],
        )
        span["count"] = len(entries)
    options = []
    # A direct Entry grant does not reveal its private parent's metadata.
    # Source Track read was verified above; other returned parents need their
    # own current decision before their titles can be projected.
    track_titles = {source.id: source.title or ""}
    for track_id in {entry.track_id for entry in entries} - {source.id}:
        decision = await evaluate(
            subject=Subject(kind="human", id=user_id),
            action="track.read",
            resource=Resource(kind="track", id=track_id, scope=f"track:{track_id}"),
        )
        track_titles[track_id] = (
            (track_by_id[track_id].title or "") if decision.allowed else ""
        )
    for entry in entries:
        label = entry.title or "Untitled entry"
        if relation.get("target_app"):
            rendered = await read_cross_app_label(
                viewer=Subject(kind="human", id=user_id),
                relation_field_key=params.field_key,
                target_entry_id=entry.id,
                target_app_id=target_app_id,
                label_field=relation.get("label_field", "title"),
            )
            if rendered.get("kind") == "restricted_relation":
                continue
            label = rendered["label"]
        track = track_by_id[entry.track_id]
        options.append(
            RelationOption(
                id=entry.id,
                label=label,
                track_id=track.id,
                track_title=track_titles.get(track.id, ""),
            )
        )
    return RelationOptionsResponse(
        options=options,
        next_cursor=metadata["next_cursor"],
        has_more=metadata["has_more"],
    )
