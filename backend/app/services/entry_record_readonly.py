"""Deny-only record restrictions declared by an Entry's owning App."""

from __future__ import annotations

from typing import Any

LOCKED_MESSAGE = "This record is read-only under its App's policy."


def normalize_readonly_rule(raw: Any) -> dict[str, str]:
    """A literal comparison on the record, or on one same-App relation."""
    keys = {"field", "equals"}
    if not isinstance(raw, dict) or set(raw) not in (
        keys,
        keys | {"relation", "related_entry_type"},
    ):
        raise ValueError(
            "requires field and equals, with optional relation and related_entry_type"
        )
    if any(
        not isinstance(value, str) or not value or value != value.strip()
        for value in raw.values()
    ):
        raise ValueError("requires non-empty string values")
    return dict(raw)


def validate_readonly_rules(state: dict, tracks: list) -> None:
    """Require unambiguous declared types and single-hop, same-App relations."""
    rules = {
        key: body["readonly_when"]
        for key, body in state.items()
        if "readonly_when" in body
    }
    if not rules:
        return
    types: dict[str, list[dict]] = {}
    for track in tracks:
        for spec in track.get("entry_types", []):
            types.setdefault(spec["key"], []).append(spec)

    def fields(key: str) -> dict:
        matches = types.get(key, [])
        if len(matches) != 1:
            raise ValueError(f"must name one declared Entry type: {key}")
        return {field["key"]: field for field in matches[0].get("fields", [])}

    for key, rule in rules.items():
        source = fields(key)
        target = source
        if relation_key := rule.get("relation"):
            field = source.get(relation_key, {})
            relation = field.get("relation") or {}
            related_type = rule["related_entry_type"]
            if (
                field.get("type") != "relation"
                or relation.get("target") != "entry"
                or relation.get("many")
                or relation.get("allow_cross_app")
                or relation.get("target_app")
                or relation.get("target_entry_types") != [related_type]
                or rules.get(related_type, {}).get("relation")
            ):
                raise ValueError(
                    f"{key} requires a single, non-chained relation to the declared type"
                )
            target = fields(related_type)
        field = target.get(rule["field"], {})
        primitive = (field.get("composite") or {}).get("base", field.get("type"))
        if primitive not in {"text", "select"}:
            raise ValueError(
                f"{key} must compare a declared text or single-select field"
            )


async def _entry_type_key(database: Any, record: dict[str, Any]) -> str:
    from app.exceptions import AuthorizationUnavailableError
    from app.models.nodes import EntryType
    from app.services.authority_facts import primary_node_record, required_node_text
    from app.services.operational_model_compile import slug_manifest_key

    type_id = required_node_text(record, "type_id")
    entry_type = await primary_node_record(database, EntryType, type_id)
    if entry_type is None:
        raise AuthorizationUnavailableError()
    body = entry_type["context"]
    schema = body.get("form_schema")
    if not isinstance(schema, dict):
        raise AuthorizationUnavailableError()
    return slug_manifest_key(
        str(schema.get("_manifest_entry_type_key") or body.get("name") or "")
    )


async def record_matches_readonly_rule(
    *,
    workspace_id: str,
    entry_id: str,
    principal_id: str,
    rule: dict[str, str],
    for_write: bool = False,
    entry_type_key: str = "",
) -> bool:
    """Evaluate persisted values only. Unavailable policy facts keep a record read-only."""
    from jvspatial.core.context import get_default_context

    from app.exceptions import AuthorizationUnavailableError
    from app.models.nodes import Entry
    from app.schemas.policy import Resource, Subject
    from app.services.authority_facts import primary_node_record
    from app.services.entry_write_scope import is_postgres_entry_transaction
    from app.services.policy_engine import evaluate
    from app.services.resource_location import DATA_APP_STATES, locate_resource

    database = get_default_context().database
    try:
        record = await primary_node_record(database, Entry, entry_id)
        if record is None:
            return True
        if entry_type_key and await _entry_type_key(database, record) != entry_type_key:
            # Retyping checks the source separately. The destination rule
            # starts governing the record once that type is persisted.
            return False
        fields = record["context"].get("custom_fields")
        if not isinstance(fields, dict):
            return True
        if relation := rule.get("relation"):
            related_id = fields.get(relation)
            if isinstance(related_id, list) and len(related_id) == 1:
                related_id = related_id[0]
            if (
                not isinstance(related_id, str)
                or not related_id
                or related_id == entry_id
            ):
                return True
            owner = await locate_resource(
                "entry",
                entry_id,
                allowed_app_states=DATA_APP_STATES,
                selected_record=record,
            )
            related = await locate_resource(
                "entry", related_id, allowed_app_states=DATA_APP_STATES
            )
            if (
                owner is None
                or related is None
                or not owner.app_id
                or owner.workspace_id != workspace_id
                or (owner.workspace_id, owner.app_id)
                != (related.workspace_id, related.app_id)
            ):
                return True
            readable = await evaluate(
                subject=Subject(kind="human", id=principal_id),
                action="entry.read",
                resource=Resource(
                    kind="entry", id=related_id, scope=f"track:{related.track_id}"
                ),
                _dry_run=True,
            )
            if not readable.allowed:
                return True
            if for_write and is_postgres_entry_transaction(database):
                # Keep the related phase stable through the current write's commit.
                record = await database.lock_record("node", related_id, shared=True)
            else:
                record = await primary_node_record(database, Entry, related_id)
            if (
                record is None
                or record.get("id") != related_id
                or record.get("entity") != Entry._entity_name()
                or not isinstance(record.get("context"), dict)
                or record["context"].get("track_id") != related.track_id
                or await _entry_type_key(database, record) != rule["related_entry_type"]
            ):
                return True
            fields = record["context"].get("custom_fields")
            if not isinstance(fields, dict):
                return True
        value = fields.get(rule["field"])
        return not isinstance(value, str) or value == rule["equals"]
    except AuthorizationUnavailableError:
        return True


async def entry_is_app_readonly(
    *, workspace_id: str, entry_id: str, principal_id: str, for_write: bool = False
) -> bool:
    """Call after authorizing access to the subject Entry."""
    from jvspatial.core.context import get_default_context

    from app.exceptions import AuthorizationUnavailableError
    from app.models.nodes import Entry
    from app.services.app_invariant_guards import _canonical_protected_state
    from app.services.authority_facts import primary_node_record

    try:
        state = await _canonical_protected_state(workspace_id, "", entry_id)
        if not any("readonly_when" in body for body in state.values()):
            return False
        database = get_default_context().database
        record = await primary_node_record(database, Entry, entry_id)
        if record is None:
            return True
        rule = state.get(await _entry_type_key(database, record), {}).get(
            "readonly_when"
        )
    except AuthorizationUnavailableError:
        return True
    return bool(rule) and await record_matches_readonly_rule(
        workspace_id=workspace_id,
        entry_id=entry_id,
        principal_id=principal_id,
        rule=rule,
        for_write=for_write,
    )
