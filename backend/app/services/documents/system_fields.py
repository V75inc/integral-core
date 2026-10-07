"""Built-in document template fields (generation time, not entry data)."""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from app.services.documents.field_ref import format_field_ref_display
from app.services.documents.field_registry import _register_field_spec

SYSTEM_MODULE = "system"

SYSTEM_DOCUMENT_FIELD_SPECS: List[Dict[str, Any]] = [
    {
        "key": "system.current_date",
        "field_ref": "system.current_date",
        "placeholder": format_field_ref_display("system.current_date"),
        "label": "Current date",
        "description": "Calendar date when the document is generated (UTC).",
        "category": "System",
        "data_type": "date",
        "module": SYSTEM_MODULE,
        "track_key": SYSTEM_MODULE,
        "local_field_key": "current_date",
        "legacy_key": "current_date",
        "context_key": SYSTEM_MODULE,
        "source": {"kind": "generation_time", "unit": "date"},
        "active": True,
        "sample": "2026-09-11",
    },
    {
        "key": "system.current_datetime",
        "field_ref": "system.current_datetime",
        "placeholder": format_field_ref_display("system.current_datetime"),
        "label": "Current date & time",
        "description": "Timestamp when the document is generated (UTC).",
        "category": "System",
        "data_type": "datetime",
        "module": SYSTEM_MODULE,
        "track_key": SYSTEM_MODULE,
        "local_field_key": "current_datetime",
        "legacy_key": "current_datetime",
        "context_key": SYSTEM_MODULE,
        "source": {"kind": "generation_time", "unit": "datetime"},
        "active": True,
        "sample": "2026-09-11T09:00:00+00:00",
    },
]


def ingest_system_document_fields(workspace_id: str) -> None:
    """Register system fields into the workspace index (idempotent)."""
    from app.services.documents.field_registry import _FIELD_INDEX

    bucket = _FIELD_INDEX.setdefault(workspace_id, {})
    for spec in SYSTEM_DOCUMENT_FIELD_SPECS:
        _register_field_spec(bucket, dict(spec))


def list_system_document_fields(*, q: Optional[str] = None) -> List[Dict[str, Any]]:
    """Return built-in system field specs (optionally filtered by search)."""
    needle = (q or "").strip().lower()
    out: List[Dict[str, Any]] = []
    for spec in SYSTEM_DOCUMENT_FIELD_SPECS:
        if needle:
            hay = " ".join(
                [
                    str(spec.get("key") or ""),
                    str(spec.get("label") or ""),
                    str(spec.get("category") or ""),
                    str(spec.get("description") or ""),
                ]
            ).lower()
            if needle not in hay:
                continue
        out.append(dict(spec))
    return out


def get_system_field_spec(field_key: str) -> Optional[Dict[str, Any]]:
    key = str(field_key or "").strip()
    for spec in SYSTEM_DOCUMENT_FIELD_SPECS:
        if key == str(spec.get("key") or ""):
            return dict(spec)
    return None


def is_generation_time_field_key(field_key: str, workspace_id: str = "") -> bool:
    """True when the field is filled at PDF generation, not from candidate data."""
    key = str(field_key or "").strip()
    if key.startswith("system."):
        return True
    if workspace_id:
        from app.services.documents.field_registry import get_field_spec

        ingest_system_document_fields(workspace_id)
        spec = get_field_spec(workspace_id, key) or get_system_field_spec(key)
    else:
        spec = get_system_field_spec(key)
    if not spec:
        return False
    return str((spec.get("source") or {}).get("kind") or "") == "generation_time"


def resolve_generation_time(source: Dict[str, Any]) -> str:
    """Return ISO date or datetime for ``generation_time`` field sources."""
    from app.utils.time import utc_now

    unit = str(source.get("unit") or "date").strip().lower()
    now = utc_now()
    if unit == "datetime":
        return now.isoformat()
    return now.date().isoformat()
