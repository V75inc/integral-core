"""App invariant guards — protected fields cannot bypass operations (ADR-012)."""

from __future__ import annotations

import contextvars
from typing import Any, Dict, Iterable, Optional, Set

from app.api.errors import BadRequestError

# Set True while an App operation / declared-query handler mutates substrate.
_OPERATION_WRITE_ACTIVE: contextvars.ContextVar[bool] = contextvars.ContextVar(
    "operation_write_active", default=False
)

# entry_type_key → protected custom_field keys (compiled from App manifests)
_PROTECTED: Dict[str, Dict[str, Set[str]]] = {}
_PROTECTED_DETAILS: Dict[str, Dict[str, Dict[str, Any]]] = {}
# workspace → app → entry_type → fields


def set_operation_write_active(active: bool) -> contextvars.Token:
    """Mark the current context as an App operation/query write."""
    return _OPERATION_WRITE_ACTIVE.set(active)


def reset_operation_write_active(token: contextvars.Token) -> None:
    """Restore the operation-write flag after an App handler returns."""
    _OPERATION_WRITE_ACTIVE.reset(token)


def operation_write_active() -> bool:
    """True while an App operation or declared query is mutating substrate."""
    return bool(_OPERATION_WRITE_ACTIVE.get())


def register_protected_fields(
    workspace_id: str,
    app_id: str,
    by_entry_type: Dict[str, Iterable[str]],
) -> None:
    """Register App-protected custom fields for generic write rejection."""
    ws = _PROTECTED.setdefault(workspace_id, {})
    app_bucket: Dict[str, Set[str]] = {}
    for et, fields in (by_entry_type or {}).items():
        key = str(et or "").strip()
        if not key:
            continue
        app_bucket[key] = {str(f).strip() for f in fields if str(f).strip()}
    ws[app_id] = app_bucket


def unregister_protected_fields(workspace_id: str, app_id: str) -> None:
    """Drop protected-field registration for an App instance."""
    ws = _PROTECTED.get(workspace_id)
    if not ws:
        return
    ws.pop(app_id, None)
    (_PROTECTED_DETAILS.get(workspace_id) or {}).pop(app_id, None)
    if not ws:
        _PROTECTED.pop(workspace_id, None)


def protected_fields_for_entry_type(workspace_id: str, entry_type_key: str) -> Set[str]:
    """Union of protected custom-field keys for an entry type in a workspace."""
    want = str(entry_type_key or "").strip()
    out: Set[str] = set()
    for app_bucket in (_PROTECTED.get(workspace_id) or {}).values():
        out |= set(app_bucket.get(want) or set())
    return out


async def enforce_protected_field_write(
    *,
    workspace_id: str,
    entry_type_key: str,
    proposed_custom_fields: Optional[Dict[str, Any]],
    track_id: str = "",
    entry_id: str = "",
    mutation_kind: str = "update",
    proposed_fields: Optional[Dict[str, Any]] = None,
    principal_id: str = "",
) -> None:
    """Reject generic writes that touch App-protected fields outside operations."""
    protected = protected_fields_for_entry_type(workspace_id, entry_type_key)
    current_state = None
    if entry_id or track_id:
        current_state = await _canonical_protected_state(
            workspace_id, track_id, entry_id
        )
        if current_state is not None:
            body = current_state.get(entry_type_key, {})
            protected = set(body.get("fields", []))
    details = [
        bucket.get(entry_type_key, {})
        for bucket in (_PROTECTED_DETAILS.get(workspace_id) or {}).values()
    ]
    if current_state is not None:
        details = [current_state.get(entry_type_key, {})]
    protected_top = {key for body in details for key in body.get("top_level", [])}
    touched = sorted(k for k in (proposed_custom_fields or {}) if k in protected)
    top_touched = set(proposed_fields or {}) & protected_top
    destructive = mutation_kind in {"delete", "type_change"} and (
        bool(protected or protected_top)
        or any(body.get("deny_delete") for body in details)
    )
    if not touched and not top_touched and not destructive:
        return
    from app.services.app_operation_authority import permits_mutation

    if await permits_mutation(
        workspace_id=workspace_id,
        entry_type_key=entry_type_key,
        fields=set(touched),
        track_id=track_id,
        entry_id=entry_id,
        mutation_kind=mutation_kind,
        top_level=top_touched,
        principal_id=principal_id,
    ):
        return
    raise BadRequestError(
        message=(
            "This record is managed by its App and can't be created or edited "
            "directly. Use the App's own actions instead."
        ),
        details={
            "error_code": "protected_field_write",
            "fields": touched,
            "entry_type": entry_type_key,
        },
    )


def protected_from_canonical(canonical: Dict[str, Any]) -> Dict[str, Set[str]]:
    """Parse optional app.protected_state map from compiled manifest."""
    raw = (canonical.get("app") or {}).get("protected_state") or {}
    if not isinstance(raw, dict):
        return {}
    out: Dict[str, Set[str]] = {}
    for et, body in raw.items():
        if isinstance(body, dict):
            fields = body.get("fields") or []
        elif isinstance(body, list):
            fields = body
        else:
            continue
        key = str(et or "").strip()
        if key:
            out[key] = {str(f).strip() for f in fields if str(f).strip()}
    return out


def register_protected_state(
    workspace_id: str, app_id: str, canonical: Dict[str, Any]
) -> None:
    """Register compiled top-level/destructive guards alongside custom fields."""
    raw = (canonical.get("app") or {}).get("protected_state") or {}
    _PROTECTED_DETAILS.setdefault(workspace_id, {})[app_id] = {
        key: dict(body) for key, body in raw.items() if isinstance(body, dict)
    }


async def _read_canonical_protected_state(
    workspace_id: str, track_id: str, entry_id: str
) -> Dict[str, Any]:
    """Read current App definition guards even on an unacknowledged peer worker."""
    from jvspatial.core.context import get_default_context

    from app.exceptions import AuthorizationUnavailableError
    from app.models.edges import HAS_APPLICATION_DEFINITION, HAS_OPERATIONAL_MODEL
    from app.models.nodes import App, OperationalModel
    from app.services.access_delegation import current_definition
    from app.services.authority_facts import primary_node_record
    from app.services.resource_location import DATA_APP_STATES, locate_resource

    location = await locate_resource(
        "entry" if entry_id else "track",
        entry_id or track_id,
        allowed_app_states=DATA_APP_STATES,
    )
    if location is None or location.workspace_id != workspace_id:
        raise AuthorizationUnavailableError()
    if not location.app_id:
        return {}
    database = get_default_context().database
    app = await primary_node_record(database, App, location.app_id)
    if app is None:
        raise AuthorizationUnavailableError()
    pointer = app["context"].get("active_definition_id")
    if pointer:
        _, definition = await current_definition(location.app_id, execution=False)
        return (definition["context"]["canonical_manifest"].get("app") or {}).get(
            "protected_state"
        ) or {}
    definitions = await database.find(
        "edge",
        {
            "source": location.app_id,
            "entity": HAS_APPLICATION_DEFINITION._entity_name(),
        },
        limit=1,
    )
    if (
        definitions
        or app["context"].get("installed_package_slug")
        or app["context"].get("installed_artifact_fingerprint")
    ):
        raise AuthorizationUnavailableError()
    models = await database.find(
        "edge",
        {"source": location.app_id, "entity": HAS_OPERATIONAL_MODEL._entity_name()},
        limit=2,
    )
    if len(models) > 1:
        raise AuthorizationUnavailableError()
    if models:
        model = await primary_node_record(
            database, OperationalModel, models[0].get("target", "")
        )
        if model is None:
            raise AuthorizationUnavailableError()
        app_body = (model["context"].get("manifest") or {}).get("app") or {}
        if app_body.get("operations") or app_body.get("protected_state"):
            raise AuthorizationUnavailableError()
    return {}


async def _canonical_protected_state(
    workspace_id: str, track_id: str, entry_id: str
) -> Dict[str, Any]:
    """Preserve unavailable state instead of treating corrupt declarations as empty."""
    from app.exceptions import AuthorizationUnavailableError

    try:
        state = await _read_canonical_protected_state(workspace_id, track_id, entry_id)
        if not isinstance(state, dict):
            raise AuthorizationUnavailableError()
        for body in state.values():
            if (
                not isinstance(body, dict)
                or not isinstance(body.get("fields", []), list)
                or not isinstance(body.get("top_level", []), list)
            ):
                raise AuthorizationUnavailableError()
            if any(
                type(key) is not str
                for key in body.get("fields", []) + body.get("top_level", [])
            ):
                raise AuthorizationUnavailableError()
            if "deny_delete" in body and type(body["deny_delete"]) is not bool:
                raise AuthorizationUnavailableError()
        return state
    except AuthorizationUnavailableError:
        raise
    except Exception as exc:
        raise AuthorizationUnavailableError() from exc
