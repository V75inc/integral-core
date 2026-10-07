"""Qualified document field references: ``module.track_key.local_field``."""

from __future__ import annotations

import re
from typing import Any, Dict, List, Optional

_BRACE_RE = re.compile(r"^\{\{\s*(.+?)\s*\}\}$")


def slug_ref_part(value: str) -> str:
    from app.services.documents.profile_compat import slug_manifest_key

    return slug_manifest_key(str(value or "").strip()) or "unknown"


def normalize_field_ref(field_key: str) -> str:
    """Strip ``{{ … }}`` wrappers if present."""
    raw = str(field_key or "").strip()
    m = _BRACE_RE.match(raw)
    if m:
        return m.group(1).strip()
    return raw


def format_field_ref_display(field_ref: str) -> str:
    """Human/editor placeholder: ``{{module.track.field}}``."""
    inner = normalize_field_ref(field_ref)
    return f"{{{{{inner}}}}}" if inner else ""


def build_field_ref(*, module: str, track_key: str, local_field: str) -> str:
    """Build ``module.track_key.local_field`` (no braces)."""
    mod = slug_ref_part(module)
    track = slug_ref_part(track_key)
    local = str(local_field or "").strip()
    if not local:
        raise ValueError("local_field is required for a field reference")
    return f"{mod}.{track}.{local}"


def parse_field_ref(field_key: str) -> Optional[Dict[str, str]]:
    """Parse ``module.track_key.local_field``; returns None if not qualified."""
    key = normalize_field_ref(field_key)
    parts = key.split(".")
    if len(parts) < 3:
        return None
    return {
        "module": parts[0],
        "track_key": parts[1],
        "local_field": ".".join(parts[2:]),
    }


def local_field_from_context_key(context_key: str, raw_field_key: str) -> str:
    """``employee.full_name`` under context ``employee`` → ``full_name``."""
    raw = str(raw_field_key or "").strip()
    prefix = f"{context_key}."
    if context_key and raw.startswith(prefix):
        return raw[len(prefix) :]
    return raw


def resolve_context_track_key(context_spec: Dict[str, Any]) -> str:
    ctx_key = str(context_spec.get("key") or "").strip()
    explicit = str(context_spec.get("track_key") or "").strip()
    if explicit:
        return slug_ref_part(explicit)
    return slug_ref_part(ctx_key)


def legacy_keys_for_spec(spec: Dict[str, Any]) -> List[str]:
    keys: List[str] = []
    for candidate in (
        spec.get("local_field_key"),
        spec.get("legacy_key"),
    ):
        val = str(candidate or "").strip()
        if val and val not in keys:
            keys.append(val)
    ctx = str(spec.get("context_key") or "").strip()
    local = str(spec.get("local_field_key") or "").strip()
    if ctx and local:
        composite = f"{ctx}.{local}"
        if composite not in keys:
            keys.append(composite)
    return keys
