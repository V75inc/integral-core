"""Historical provenance written by the first package installation only."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from app.exceptions import AuthorizationUnavailableError
from app.models.edges import CONTAINS, HAS_APPLICATION_DEFINITION, HAS_OPERATIONAL_MODEL
from app.models.nodes import (
    App,
    ApplicationDefinition,
    EntryType,
    OperationalModel,
    Track,
)
from app.services.authority_facts import primary_node_record, required_node_text


@dataclass(frozen=True)
class MaterializedTrack:
    """Exact IDs emitted by create branches, not recovered by looking at names."""

    track_key: str
    track_id: str
    entry_types: tuple[tuple[str, str], ...]


async def _has_exact_parent(
    database: Any, edge: Any, *, source: str, target: str
) -> bool:
    rows = await database.find(
        "edge", {"entity": edge._entity_name(), "target": target}, limit=2
    )
    return len(rows) == 1 and rows[0].get("source") == source


async def _verified_tracks(
    database: Any,
    app_id: str,
    workspace_id: str,
    manifest: dict[str, Any],
    created: list[MaterializedTrack],
) -> list[dict[str, Any]] | None:
    from app.services.operational_model_runtime import app_manifest_provisioning_enabled
    from app.services.resource_location import MANAGEMENT_APP_STATES, locate_resource

    if not app_manifest_provisioning_enabled(manifest):
        return None
    specs = {
        spec["key"]: {entry["key"] for entry in spec.get("entry_types", [])}
        for spec in (manifest.get("app") or {}).get("tracks", [])
        if spec.get("provision_on_create", True)
    }
    if (
        len({track.track_id for track in created}) != len(created)
        or len(specs) != len(created)
        or set(specs) != {track.track_key for track in created}
    ):
        return None

    evidence = []
    for fact in created:
        location = await locate_resource(
            "track", fact.track_id, allowed_app_states=MANAGEMENT_APP_STATES
        )
        if (
            location is None
            or location.workspace_id != workspace_id
            or location.app_id != app_id
            or location.track_id != fact.track_id
        ):
            return None
        track = await primary_node_record(database, Track, fact.track_id)
        if track is None:
            return None
        model_id = required_node_text(track, "attached_operational_model_id")
        model = await primary_node_record(database, OperationalModel, model_id)
        if (
            model is None
            or model["context"].get("scope") != "track"
            or not await _has_exact_parent(
                database, HAS_OPERATIONAL_MODEL, source=fact.track_id, target=model_id
            )
            or len(
                await database.find(
                    "edge",
                    {
                        "entity": HAS_OPERATIONAL_MODEL._entity_name(),
                        "source": fact.track_id,
                    },
                    limit=2,
                )
            )
            != 1
        ):
            return None
        types = dict(fact.entry_types)
        if (
            len(types) != len(fact.entry_types)
            or len(set(types.values())) != len(types)
            or set(types) != specs[fact.track_key]
        ):
            return None
        for type_id in types.values():
            entry_type = await primary_node_record(database, EntryType, type_id)
            if (
                entry_type is None
                or required_node_text(entry_type, "track_id") != fact.track_id
                or entry_type["context"].get("is_template") is not False
                or not await _has_exact_parent(
                    database, CONTAINS, source=model_id, target=type_id
                )
            ):
                return None
        evidence.append(
            {
                "track_id": fact.track_id,
                "track_key": fact.track_key,
                "entry_types": types,
            }
        )
    return evidence


async def record_initial_track_materialization(
    app: App,
    definition: ApplicationDefinition,
    created: list[MaterializedTrack],
    *,
    actor_id: str,
) -> bool:
    """Write one complete receipt, or leave the install unqualified.

    Caller supplies only IDs captured within the fresh install flow. The
    receipt describes creation, not current attachment, policy, or readiness.
    """
    from jvspatial.core.context import get_default_context

    from app.services.application_definitions import definition_fingerprint
    from app.services.membership_write_scope import membership_write_scope

    if not created:
        return False
    outer_graph = get_default_context()
    async with membership_write_scope(app.workspace_id, principal_ids=(actor_id,)):
        graph = get_default_context()
        database = graph.database
        try:
            app_record = await primary_node_record(database, App, app.id)
            stored = await primary_node_record(
                database, ApplicationDefinition, definition.id
            )
            if app_record is None or stored is None:
                return False
            current = app_record["context"]
            initial = stored["context"]
            manifest = initial.get("canonical_manifest")
            if (
                current.get("workspace_id") != app.workspace_id
                or current.get("lifecycle_state") != "installing"
                or current.get("active_definition_id") != definition.id
                or current.get("active_definition_revision") != 1
                or initial.get("app_id") != app.id
                or initial.get("workspace_id") != app.workspace_id
                or initial.get("status") != "active"
                or initial.get("revision") != 1
                or initial.get("source_kind") != "package"
                or not current.get("installed_from_library_id")
                or initial.get("source_operational_model_id")
                != current["installed_from_library_id"]
                or initial.get("initial_track_materialization") is not None
                or not isinstance(manifest, dict)
                or initial.get("manifest_fingerprint")
                != definition.manifest_fingerprint
                or initial.get("manifest_fingerprint")
                != definition_fingerprint(manifest)
                or not await _has_exact_parent(
                    database,
                    HAS_APPLICATION_DEFINITION,
                    source=app.id,
                    target=definition.id,
                )
            ):
                return False
            evidence = await _verified_tracks(
                database, app.id, app.workspace_id, manifest, created
            )
        except AuthorizationUnavailableError:
            return False
        if evidence is None:
            return False

        try:
            await definition.set_context(graph)
            definition.initial_track_materialization = {
                "version": 1,
                "tracks": evidence,
            }
            await definition.save()
        finally:
            await definition.set_context(outer_graph)
    return True
