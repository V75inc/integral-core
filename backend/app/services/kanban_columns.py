"""Remove a board column and its unused select option as one guarded effect."""

from contextlib import AsyncExitStack
from copy import deepcopy

from app.api.errors import (
    BadRequestError,
    InsufficientPermissionsError,
    ResourceNotFoundError,
)
from app.api.utils import export_node
from app.models.edges import CONTAINS
from app.models.nodes import Entry, Track
from app.schemas.policy import Resource, Subject
from app.services.attached_model_scope import read_attached_model
from app.services.change_event import emit_change_event
from app.services.entry_type_resolver import canonical_entry_type_key
from app.services.entry_write_scope import (
    entry_write_scope,
    is_postgres_entry_transaction,
)
from app.services.model_authority import resolve_model_authority
from app.services.operational_model_runtime import sync_attached_manifest
from app.services.policy_engine import evaluate
from app.services.resource_location import MANAGEMENT_APP_STATES
from app.services.view_scope import list_scoped_track_views, read_scoped_view
from app.utils.time import utc_now_iso


def _column_key(column):
    return str(column.get("key") or "") if isinstance(column, dict) else str(column)


async def remove_kanban_column(*, request, user_id: str, view_id: str, column_key: str):
    """Remove an unused option and affected board columns under one write fence."""
    located = await read_scoped_view(view_id, allowed_app_states=MANAGEMENT_APP_STATES)
    if located is None:
        raise ResourceNotFoundError(message="View not found")
    workspace_id = located[1].workspace_id
    # The same workspace order as entry writes: PG row lock across workers,
    # local serialization only for the supported single-process stores.
    async with entry_write_scope(f"workspace-effects:{workspace_id}") as graph:
        async with AsyncExitStack() as stack:
            if is_postgres_entry_transaction(graph.database):
                from app.services.authorization_scope import authorization_scope

                await stack.enter_async_context(authorization_scope(workspace_id))
            located = await read_scoped_view(
                view_id, allowed_app_states=MANAGEMENT_APP_STATES
            )
            if located is None or located[1].workspace_id != workspace_id:
                raise ResourceNotFoundError(message="View not found")
            view, location = located
            from app.services.request_scope import require_effect_parent_scope

            await require_effect_parent_scope(
                request, user_id, Track, location.track_id
            )
            decision = await evaluate(
                subject=Subject(kind="human", id=user_id),
                action="track.update",
                resource=Resource(
                    kind="track",
                    id=location.track_id,
                    scope=f"track:{location.track_id}",
                ),
            )
            if not decision.allowed:
                raise InsufficientPermissionsError(message="Access denied")
            if view.type != "kanban":
                raise BadRequestError(message="This view is not a Kanban board")
            track = await Track.find_one({"id": location.track_id})
            if track is None:
                raise ResourceNotFoundError(message="Track not found")
            from app.services.migration_write_guard import assert_track_schema_writable

            await assert_track_schema_writable(track)
            attached = await read_attached_model("track", track.id)
            model = attached[1] if attached else None
            if model is None:
                raise BadRequestError(message="Track has no editable operational model")
            from app.services.operational_model_graph import (
                is_space_track_template_profile,
            )

            authority = await resolve_model_authority(
                model.id, allowed_app_states=MANAGEMENT_APP_STATES
            )
            if authority is None:
                raise BadRequestError(
                    message="The current model authority is unavailable"
                )
            shared_model = (
                is_space_track_template_profile(model)
                or model.library_package
                or not authority.editable
                or authority.owners != (("track", track.id),)
            )
            config = deepcopy(view.config or {})
            group_by = str(config.get("group_by") or "status")
            field_key = group_by.removeprefix("custom_fields.")
            if "." in field_key or "$" in field_key:
                raise BadRequestError(
                    message="This grouping cannot remove a schema option"
                )
            types = await model.nodes(edge=[CONTAINS], node=["EntryType"])
            keys = set(view.entry_type_keys or [])
            scoped = [
                item
                for item in types
                if not keys or canonical_entry_type_key(item) in keys
            ]
            changed = []
            for item in scoped:
                schema = deepcopy(item.form_schema or {})
                field = next(
                    (
                        f
                        for f in (schema.get("fields") or [])
                        if f.get("key") == field_key
                    ),
                    None,
                )
                if (
                    not field
                    or field.get("type") not in {"select", "multi_select"}
                    or column_key not in (field.get("enum") or [])
                ):
                    continue
                if shared_model or item.track_id != track.id:
                    raise BadRequestError(
                        message="This option belongs to a shared template. Edit its model instead."
                    )
                values = [v for v in field["enum"] if v != column_key]
                if not values:
                    raise BadRequestError(
                        message="Keep at least one option in this field"
                    )
                default = field.get("default")
                setting_key = field.get("default_from_setting")
                if setting_key:
                    from app.services.operational_model_entry_fields import (
                        _resolve_setting_default,
                    )

                    configured = await _resolve_setting_default(track, setting_key)
                    if configured is not None:
                        default = configured
                if default == column_key or (
                    isinstance(default, list) and column_key in default
                ):
                    raise BadRequestError(
                        message="Choose another default value before removing this option"
                    )
                # Query the complete type, including records hidden by current filters.
                if await Entry.find_one(
                    {
                        "context.track_id": track.id,
                        "context.type_id": {"$in": [item.id, "", None]},
                        f"context.custom_fields.{field_key}": {
                            "$all" if field.get("type") == "multi_select" else "$in": [
                                column_key
                            ]
                        },
                    }
                ):
                    raise BadRequestError(
                        message="Records still use this option. Move them to another column first."
                    )
                field["enum"] = values
                changed.append((item, schema))
            other_changes = []
            if changed:
                changed_keys = {canonical_entry_type_key(item) for item, _ in changed}
                all_keys = {canonical_entry_type_key(item) for item in types}
                for other in await list_scoped_track_views(track.id):
                    if other.id == view.id or other.type != "kanban":
                        continue
                    cfg = deepcopy(other.config or {})
                    if str(cfg.get("group_by") or "status") != group_by:
                        continue
                    # Keep an explicit column when another type on that board
                    # still defines it. Otherwise update every affected board.
                    other_keys = set(other.entry_type_keys or []) or all_keys
                    remaining_types = [
                        item
                        for item in types
                        if canonical_entry_type_key(item) in other_keys - changed_keys
                    ]
                    if any(
                        column_key in (field.get("enum") or [])
                        for item in remaining_types
                        for field in ((item.form_schema or {}).get("fields") or [])
                        if field.get("key") == field_key
                    ):
                        continue
                    if not changed_keys.intersection(other_keys):
                        continue
                    if any(
                        _column_key(c) == column_key
                        for c in (cfg.get("kanban_columns") or [])
                    ):
                        cfg["kanban_columns"] = [
                            c
                            for c in cfg["kanban_columns"]
                            if _column_key(c) != column_key
                        ]
                        other_changes.append((other, cfg))
            config["kanban_columns"] = [
                c
                for c in (config.get("kanban_columns") or [])
                if _column_key(c) != column_key
            ]
            before_view = await export_node(view)
            before_model = deepcopy(model.manifest)
            before_model_updated = model.updated_at
            before_types = [
                (item, deepcopy(item.form_schema), item.updated_at)
                for item, _ in changed
            ]
            before_config, before_updated = view.config, view.updated_at
            before_other = [
                (
                    other,
                    deepcopy(other.config),
                    other.updated_at,
                    await export_node(other),
                )
                for other, _ in other_changes
            ]
            try:
                for item, schema in changed:
                    item.form_schema = schema
                    item.updated_at = utc_now_iso()
                    await item.save()
                for other, other_config in other_changes:
                    other.config = other_config
                    other.updated_at = utc_now_iso()
                    await other.save()
                view.config = config
                view.updated_at = utc_now_iso()
                await view.save()
                if not shared_model:
                    await sync_attached_manifest(model)
            except BaseException:
                # PG rolls back. Local stores need compensation for all saves.
                if not is_postgres_entry_transaction(graph.database):
                    for item, schema, updated in before_types:
                        item.form_schema, item.updated_at = schema, updated
                        await item.save()
                    for other, prior_config, prior_updated, _ in before_other:
                        other.config, other.updated_at = prior_config, prior_updated
                        await other.save()
                    view.config, view.updated_at = before_config, before_updated
                    await view.save()
                    model.manifest = before_model
                    model.updated_at = before_model_updated
                    await model.save()
                raise
            await emit_change_event(
                actor_kind="human",
                actor_id=user_id,
                action="view.update",
                resource_type="View",
                resource_id=view.id,
                before=before_view,
                after=await export_node(view),
                scope=f"track:{track.id}",
            )
            for other, _, _, prior in before_other:
                await emit_change_event(
                    actor_kind="human",
                    actor_id=user_id,
                    action="view.update",
                    resource_type="View",
                    resource_id=other.id,
                    before=prior,
                    after=await export_node(other),
                    scope=f"track:{track.id}",
                )
            return {
                "affected_view_ids": [
                    view.id,
                    *[other.id for other, _ in other_changes],
                ],
                "view": await export_node(view),
                "removed_option": column_key if changed else None,
            }
