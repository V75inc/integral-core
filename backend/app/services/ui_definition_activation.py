"""Owner-governed preservation of unchanged action grants across UI releases."""

from copy import deepcopy
from datetime import datetime, timezone

from jvspatial.core.context import get_default_context

from app.api.errors import BadRequestError
from app.exceptions import AuthorizationUnavailableError
from app.models.access_delegation import (
    ACTION_BINDING_SUBJECT,
    HAS_ACCESS_GROUP,
    HAS_ACTION_BINDING,
    AppActionBinding,
    WorkspaceAccessGroup,
)
from app.models.edges import HAS_APPLICATION_DEFINITION
from app.models.nodes import ApplicationDefinition
from app.schemas.access_delegation import ActionCondition
from app.services.access_delegation import (
    MAX_BINDINGS,
    _audit,
    _current_authority_read,
    _edges,
    _record,
    _require_owner,
    current_definition,
    membership_baseline,
)
from app.services.application_definitions import definition_fingerprint
from app.services.authority_facts import required_node_text
from app.services.sharing_write_scope import sharing_write_scope


def _action_contract(manifest):
    """Strip only the presentation surfaces used by the React/lookup pilot."""
    result = deepcopy(manifest)
    app = result["app"]
    app.pop("ui_components", None)
    app.pop("queries", None)
    for track in app.get("tracks", []):
        for entry_type in track.get("entry_types", []):
            entry_type.pop("ui_contributions", None)
        for view in track.get("views", []):
            if view.get("view_type") not in {
                "region-system/editable-related-lines",
                "editable_related_lines",
            }:
                continue
            config = view.get("config") or {}
            config.pop("catalog_query", None)
            options = config.get("column_options") or {}
            for key, value in list(options.items()):
                if isinstance(value, dict):
                    value.pop("query", None)
                    if not value:
                        options.pop(key)
            if not options:
                config.pop("column_options", None)
    return result


def validate_ui_only_transition(previous, candidate):
    before = {query["key"]: query for query in previous["app"].get("queries", [])}
    after = {query["key"]: query for query in candidate["app"].get("queries", [])}
    if (
        len(before) != len(previous["app"].get("queries", []))
        or len(after) != len(candidate["app"].get("queries", []))
        or any(after.get(key) != value for key, value in before.items())
        or _action_contract(previous) != _action_contract(candidate)
    ):
        raise BadRequestError(message="UI-only activation changed the action contract")


@_current_authority_read
async def carry_forward_ui_action_bindings(*, actor_id, app_id, previous_definition_id):
    """Keep grant IDs/subjects/conditions, with fresh owner authority and audit.

    No grant is created, activated, extended, or assigned a new membership
    baseline. Only still-valid grants of the immediate predecessor may move.
    The caller's UI activation transaction owns commit/rollback.
    """
    app, _ = await current_definition(app_id)
    workspace_id = required_node_text(app, "workspace_id")
    async with sharing_write_scope(workspace_id, principal_ids=(actor_id,)):
        actor = await _require_owner(actor_id, workspace_id)
        app, current = await current_definition(app_id)
        previous = await _record(ApplicationDefinition, previous_definition_id)
        if (
            required_node_text(app, "workspace_id") != workspace_id
            or previous is None
            or previous["context"].get("app_id") != app_id
            or previous["context"].get("workspace_id") != workspace_id
            or previous["context"].get("status") != "superseded"
            or current["context"].get("base_definition_id") != previous_definition_id
            or len(
                await _edges(app_id, previous_definition_id, HAS_APPLICATION_DEFINITION)
            )
            != 1
            or definition_fingerprint(previous["context"].get("canonical_manifest", {}))
            != previous["context"].get("manifest_fingerprint")
        ):
            raise AuthorizationUnavailableError()
        validate_ui_only_transition(
            previous["context"]["canonical_manifest"],
            current["context"]["canonical_manifest"],
        )
        database = get_default_context().database
        allowed_roles = {
            role
            for operation in previous["context"]["canonical_manifest"]["app"].get(
                "operations", []
            )
            for role in (operation.get("authorization") or {}).get("roles", [])
        }
        edges = await database.find(
            "edge",
            {"source": app_id, "entity": HAS_ACTION_BINDING._entity_name()},
            limit=MAX_BINDINGS + 1,
        )
        if len(edges) > MAX_BINDINGS:
            raise AuthorizationUnavailableError()
        seen = set()
        carried = []
        for edge in edges:
            binding_id = edge.get("target")
            if not isinstance(binding_id, str) or binding_id in seen:
                raise AuthorizationUnavailableError()
            seen.add(binding_id)
            row = await _record(AppActionBinding, binding_id)
            if row is None or type(row["context"].get("active")) is not bool:
                raise AuthorizationUnavailableError()
            body = row["context"]
            if (
                not body["active"]
                or body.get("definition_id") != previous_definition_id
            ):
                continue
            if (
                not isinstance(body.get("role"), str)
                or body["role"] not in allowed_roles
            ):
                continue
            if (
                body.get("app_id") != app_id
                or body.get("workspace_id") != workspace_id
                or body.get("definition_fingerprint")
                != previous["context"]["manifest_fingerprint"]
            ):
                raise AuthorizationUnavailableError()
            if type(body.get("revision")) is not int or body["revision"] < 1:
                raise AuthorizationUnavailableError()
            if not isinstance(body.get("conditions"), list):
                raise AuthorizationUnavailableError()
            for condition in body["conditions"]:
                ActionCondition.model_validate(condition)
            if body.get("expires_at"):
                expiry = datetime.fromisoformat(body["expires_at"])
                if expiry.tzinfo is None:
                    raise AuthorizationUnavailableError()
                if expiry <= datetime.now(timezone.utc):
                    continue
            subjects = await database.find(
                "edge",
                {"source": binding_id, "entity": ACTION_BINDING_SUBJECT._entity_name()},
                limit=2,
            )
            if (
                len(subjects) != 1
                or subjects[0].get("target") != body.get("subject_id")
                or (subjects[0].get("context") or {}).get("subject_kind")
                != body.get("subject_kind")
            ):
                raise AuthorizationUnavailableError()
            if body.get("subject_kind") == "human":
                user, baseline = await membership_baseline(
                    body["subject_id"], workspace_id
                )
                if user is None or baseline != body.get("membership_fingerprint"):
                    continue
            elif body.get("subject_kind") == "group":
                group = await _record(WorkspaceAccessGroup, body["subject_id"])
                if (
                    group is None
                    or group["context"].get("workspace_id") != workspace_id
                    or len(
                        await _edges(workspace_id, body["subject_id"], HAS_ACCESS_GROUP)
                    )
                    != 1
                ):
                    raise AuthorizationUnavailableError()
            else:
                raise AuthorizationUnavailableError()
            query = {
                "id": binding_id,
                "entity": AppActionBinding._entity_name(),
                "context.app_id": app_id,
                "context.workspace_id": workspace_id,
                "context.active": True,
                "context.revision": body["revision"],
                "context.definition_id": previous_definition_id,
                "context.definition_fingerprint": previous["context"][
                    "manifest_fingerprint"
                ],
            }
            updated = await database.find_one_and_update(
                "node",
                query,
                {
                    "$set": {
                        "context.definition_id": current["id"],
                        "context.definition_fingerprint": current["context"][
                            "manifest_fingerprint"
                        ],
                        "context.revision": body["revision"] + 1,
                    }
                },
            )
            if updated is None:
                raise AuthorizationUnavailableError()
            node = await get_default_context()._deserialize_entity(
                AppActionBinding, updated
            )
            if node is None:
                raise AuthorizationUnavailableError()
            await _audit(
                actor.id,
                workspace_id,
                node,
                "app_action.ui_carry_forward",
                previous_definition_id=previous_definition_id,
                definition_id=current["id"],
            )
            carried.append(binding_id)
        return carried
