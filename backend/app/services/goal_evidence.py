"""Evaluate published standing-goal criteria over already-admitted sources."""

import hashlib
import json

from jvspatial.core.context import get_default_context

from app.contracts.goal_semantics import (
    EvidenceCount,
    GoalSemantics,
    validate_goal_semantics,
)
from app.contracts.task_semantics import FieldOutcome
from app.middleware.permissions_cache import isolated_permissions_cache
from app.models.edges import CONTAINS, HAS_OPERATIONAL_MODEL, IS_OF_TYPE, REFERENCES
from app.models.nodes import Entry, EntryType, Track
from app.schemas.policy import Resource, Subject
from app.services.app_graph import get_track_attached_operational_model
from app.services.policy_engine import evaluate
from app.services.resource_location import DATA_APP_STATES, locate_resource
from app.services.workspace_permissions import can_access_workspace


async def _published_fields(entry: Entry) -> dict | None:
    track = await Track.find_one({"id": entry.track_id})
    entry_type = (
        await EntryType.find_one({"id": entry.type_id}) if entry.type_id else None
    )
    profile = await get_track_attached_operational_model(track) if track else None
    if (
        entry_type is None
        or entry_type.track_id != entry.track_id
        or profile is None
        or profile.status != "published"
    ):
        return None
    graph = get_default_context()
    for source, target, edge in (
        (entry.track_id, profile.id, HAS_OPERATIONAL_MODEL),
        (profile.id, entry.type_id, CONTAINS),
        (entry.id, entry.type_id, IS_OF_TYPE),
    ):
        if len(await graph.find_edges_between(source, target, edge_class=edge)) != 1:
            return None
    return entry_type.form_schema or {}


def _field_key(schema: dict, field_id: str, field_type: str) -> str | None:
    fields = schema.get("fields")
    if not isinstance(fields, list):
        return None
    matches = [
        field
        for field in fields
        if isinstance(field, dict) and field.get("id") == field_id
    ]
    if len(matches) != 1 or matches[0].get("type") != field_type:
        return None
    key = matches[0].get("key")
    return key if isinstance(key, str) and key else None


def _goal_semantics(schema: dict) -> GoalSemantics | None:
    fields = schema.get("fields")
    if not isinstance(fields, list) or any(
        not isinstance(field, dict) for field in fields
    ):
        return None
    try:
        return validate_goal_semantics(schema.get("goal_semantics"), fields)
    except ValueError:
        return None


async def _linked_source(goal: Entry, schema: dict, field_id: str) -> str | None:
    relation_key = _field_key(schema, field_id, "relation")
    source_id = goal.custom_fields.get(relation_key) if relation_key else None
    if not isinstance(source_id, str) or source_id == goal.id:
        return None
    graph = get_default_context()
    edges = await graph.find_edges_between(goal.id, source_id, edge_class=REFERENCES)
    return (
        source_id
        if sum(edge.field_key == relation_key for edge in edges) == 1
        else None
    )


def _criterion_value(source: Entry, schema: dict, predicate):
    expected_type = (
        int if isinstance(predicate, EvidenceCount) else type(predicate.value)
    )
    field_type = {bool: "boolean", int: "number", str: "text"}[expected_type]
    key = _field_key(schema, predicate.field_id, field_type)
    value = source.custom_fields.get(key) if key else None
    return value if type(value) is expected_type else None


def _criterion_met(value, predicate) -> bool:
    if value is None:
        return False
    return (
        value >= 0 if isinstance(predicate, EvidenceCount) else value == predicate.value
    )


def _goal_outcome(goal: Entry, schema: dict, semantics: GoalSemantics) -> str | None:
    outcome = goal.title
    if isinstance(semantics.outcome, FieldOutcome):
        key = _field_key(schema, semantics.outcome.field_id, "text")
        outcome = goal.custom_fields.get(key) if key else None
    return outcome if isinstance(outcome, str) and outcome.strip() else None


async def goal_criteria_met(goal: Entry, sources: dict[str, Entry]) -> bool:
    """Check the exact goal relations and strict values from the locked Entries."""
    schema = await _published_fields(goal)
    semantics = _goal_semantics(schema) if schema else None
    if not schema or not semantics or not _goal_outcome(goal, schema, semantics):
        return False

    source_schemas: dict[str, dict] = {}
    used: set[str] = set()
    for criterion in semantics.criteria:
        source_id = await _linked_source(
            goal, schema, criterion.evidence_relation_field_id
        )
        if source_id is None or source_id not in sources:
            return False
        source = sources[source_id]
        if source_id not in source_schemas:
            source_schema = await _published_fields(source)
            if source_schema is None:
                return False
            source_schemas[source_id] = source_schema
        value = _criterion_value(source, source_schemas[source_id], criterion.predicate)
        if not _criterion_met(value, criterion.predicate):
            return False
        used.add(source_id)
    return used == sources.keys()


async def _read_goal_entry(
    user_id: str, workspace_id: str, entry_id: str
) -> Entry | None:
    records: dict[str, dict] = {}
    location = await locate_resource(
        "entry", entry_id, allowed_app_states=DATA_APP_STATES, records_out=records
    )
    if location is None or location.workspace_id != workspace_id:
        return None
    decision = await evaluate(
        subject=Subject(kind="human", id=user_id),
        action="entry.read",
        resource=Resource(
            kind="entry", id=entry_id, scope=f"track:{location.track_id}"
        ),
        _dry_run=True,
    )
    if not decision.allowed or entry_id not in records:
        return None
    graph = get_default_context()
    entry = await graph._deserialize_entity(Entry, records[entry_id])
    return entry if entry and entry.status not in {"archived", "deleted"} else None


async def read_goal_watch(
    user_id: str, workspace_id: str, entry_id: str
) -> tuple[str, str] | None:
    """Fingerprint authorized material goal and evidence state without releasing values."""
    with isolated_permissions_cache():
        if (
            not workspace_id
            or await can_access_workspace(user_id, workspace_id) == "none"
        ):
            return None
        goal = await _read_goal_entry(user_id, workspace_id, entry_id)
        schema = await _published_fields(goal) if goal else None
        semantics = _goal_semantics(schema) if schema else None
        if not goal or not schema or not semantics:
            return None
        outcome = _goal_outcome(goal, schema, semantics)
        if outcome is None:
            return None

        evidence: dict[str, tuple[Entry, dict]] = {}
        state = []
        for criterion in semantics.criteria:
            source_id = await _linked_source(
                goal, schema, criterion.evidence_relation_field_id
            )
            if source_id is None:
                state.append((criterion.key, None, None))
                continue
            if source_id not in evidence:
                source = await _read_goal_entry(user_id, workspace_id, source_id)
                source_schema = await _published_fields(source) if source else None
                if source is None or source_schema is None:
                    return None
                evidence[source_id] = (source, source_schema)
            source, source_schema = evidence[source_id]
            value = _criterion_value(source, source_schema, criterion.predicate)
            state.append((criterion.key, source_id, value))
        digest = hashlib.sha256(
            json.dumps(
                [goal.track_id, goal.status, outcome, semantics.model_dump(), state],
                sort_keys=True,
                separators=(",", ":"),
            ).encode()
        ).hexdigest()
        return goal.track_id, digest
