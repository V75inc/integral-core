"""Bounded readable schema declarations for native graph projections."""

from collections import defaultdict
from hashlib import sha256

from app.api.errors import BadRequestError
from app.models.nodes import EntryType
from app.schemas.graph_context import validate_entry_graph_context
from app.services.graph_hydration import batch_get_by_ids

RECORDED_ENTRY_FIELDS = (
    "title",
    "status",
    "type_id",
    "track_id",
    "schema_revision",
    "record_revision",
    "updated_at",
    "provenance.source",
)


async def build_recorded_profile(nodes, inventory, access, node_fields):
    """Grants bind to concrete type/revision/track, not shared Entry discriminator."""
    from jvspatial import (
        ProjectionContextConfig,
        ProjectionEvidence,
        ProjectionField,
        ProjectionRelation,
    )

    entries = [inventory[node["id"]] for node in nodes if node["id"] in inventory]
    type_ids = sorted({row["context"].get("type_id") for row in entries} - {None, ""})
    if len(type_ids) > 8:
        raise BadRequestError(message="Use a narrower graph root for recorded detail")
    types = await batch_get_by_ids(EntryType, type_ids)
    grants, hints, rules = {}, defaultdict(list), []
    for type_id, entry_type in types.items():
        row = {
            "id": type_id,
            "entity": "EntryType",
            "context": {"track_id": entry_type.track_id},
        }
        if not await access._node_allowed(row):
            continue
        schema = entry_type.form_schema or {}
        if "graph_context" not in schema:
            continue
        try:
            config = validate_entry_graph_context(
                schema["graph_context"], schema.get("fields", [])
            )
        except ValueError:
            continue  # Unvalidated legacy declarations grant nothing.
        if not any(
            row["context"].get("type_id") == type_id
            and row["context"].get("track_id") == entry_type.track_id
            and row["context"].get("schema_revision") == config.schema_revision
            for row in entries
        ):
            continue
        grants[(type_id, config.schema_revision, entry_type.track_id)] = tuple(
            f"custom_fields.{key}" for key in config.fields
        )
        for key, hint in config.fields.items():
            hints[f"custom_fields.{key}"].append(hint)
        for key, relation in config.relations.items():
            rules.append(
                ProjectionRelation(
                    id="om/"
                    + sha256(
                        f"{type_id}/{config.schema_revision}/{key}".encode()
                    ).hexdigest()[:24],
                    edge_type="REFERENCES",
                    **relation.model_dump(exclude_none=True),
                    edge_filter={
                        "context.field_key": key,
                        "context.relation_type": "operational_model",
                    },
                    source_filter={
                        "context.type_id": type_id,
                        "context.schema_revision": config.schema_revision,
                        "context.track_id": entry_type.track_id,
                    },
                )
            )
    if len(hints) > 12:
        raise BadRequestError(message="Use a narrower graph root for recorded detail")
    evidence = ProjectionEvidence(
        source_path="provenance.source", revision_path="record_revision"
    )
    profile = {
        entity: tuple(ProjectionField(path=path) for path in paths)
        for entity, paths in node_fields.items()
    }
    selected = [
        ProjectionField(
            path=path,
            priority=index,
            evidence=evidence,
            description=(
                "Substrate lifecycle state; not operational completion."
                if path == "status"
                else None
            ),
        )
        for index, path in enumerate(RECORDED_ENTRY_FIELDS)
    ]
    for path, declarations in sorted(hints.items()):
        # Conflicting schema hints are omitted rather than attributed to the
        # wrong type. Exact per-record grants still govern the selected value.
        hint = (
            declarations[0]
            if all(item == declarations[0] for item in declarations)
            else None
        )
        metadata, binding = {}, evidence
        if hint:
            metadata = {
                key: getattr(hint, key) for key in ("label", "description", "unit")
            }
            binding = ProjectionEvidence(
                source_path=(
                    f"custom_fields.{hint.source}"
                    if hint.source
                    else "provenance.source"
                ),
                observed_at_path=(
                    f"custom_fields.{hint.observed_at}" if hint.observed_at else None
                ),
                verification_path=(
                    f"custom_fields.{hint.verification}" if hint.verification else None
                ),
                confidence_path=(
                    f"custom_fields.{hint.confidence}" if hint.confidence else None
                ),
                revision_path="record_revision",
            )
        selected.append(
            ProjectionField(path=path, priority=10, evidence=binding, **metadata)
        )
    profile["Entry"] = tuple(selected)
    return (
        grants,
        tuple(rules),
        ProjectionContextConfig(
            profile_id="integral-recorded", revision="1", node_fields=profile
        ),
    )
