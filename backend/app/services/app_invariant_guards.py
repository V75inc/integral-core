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
) -> None:
    """Reject generic writes that touch App-protected fields outside operations."""
    if operation_write_active():
        return
    if not proposed_custom_fields:
        return
    protected = protected_fields_for_entry_type(workspace_id, entry_type_key)
    if not protected:
        return
    touched = sorted(k for k in proposed_custom_fields.keys() if k in protected)
    if not touched:
        return
    raise BadRequestError(
        message=(
            "Protected App fields cannot be written through generic Entry updates; "
            "use the App's typed operation"
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
