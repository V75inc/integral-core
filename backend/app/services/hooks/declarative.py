"""Declarative-block interpreters — transform / public_share / dedup.

Grammars are documented in DR-30-02 §"Declarative block grammar".
Substrate dispatcher calls these interpreters when binding.mode ==
"declarative".
"""

from __future__ import annotations

from typing import Any, Dict, List


class OverrideRequiredError(Exception):
    """Source entry has override_flag set; caller must pass override=True."""


class ShareGateDeniedError(Exception):
    """Public-share gate (e.g. published=true) not satisfied."""


class TransformGateDeniedError(Exception):
    """entry.transform gate (e.g. stage=won) not satisfied."""


def _get_path(obj: Dict[str, Any], path: str) -> Any:
    """Walk a dotted path like 'custom_fields.title' against a dict."""
    cur: Any = obj
    for seg in path.split("."):
        if not isinstance(cur, dict):
            return None
        cur = cur.get(seg)
    return cur


def _set_path(obj: Dict[str, Any], path: str, value: Any) -> None:
    cur = obj
    parts = path.split(".")
    for seg in parts[:-1]:
        nxt = cur.get(seg)
        if not isinstance(nxt, dict):
            nxt = {}
            cur[seg] = nxt
        cur = nxt
    cur[parts[-1]] = value


def _del_path(obj: Dict[str, Any], path: str) -> None:
    cur = obj
    parts = path.split(".")
    for seg in parts[:-1]:
        nxt = cur.get(seg)
        if not isinstance(nxt, dict):
            return
        cur = nxt
    cur.pop(parts[-1], None)


def resolve_gate_actual(entry: Dict[str, Any], gate_field: str) -> Any:
    """Resolve a gate field against an entry dict.

    Accepts either a short name (``stage`` → look under custom_fields first)
    or a dotted path (``custom_fields.stage``).
    """
    if not gate_field:
        return None
    # Prefer custom_fields.<gate_field> when gate_field is a bare key, then
    # fall back to the path as written (supports gate_field: custom_fields.stage).
    actual = _get_path(entry, f"custom_fields.{gate_field}")
    if actual is None:
        actual = _get_path(entry, gate_field)
    return actual


def gate_satisfied(entry: Dict[str, Any], block: Dict[str, Any]) -> bool:
    """True when block has no gate, or gate_field equals gate_value on entry."""
    gate_field = block.get("gate_field")
    if not gate_field:
        return True
    gate_value = block.get("gate_value", True)
    return resolve_gate_actual(entry, str(gate_field)) == gate_value


def apply_transform(
    source: Dict[str, Any],
    block: Dict[str, Any],
    override: bool = False,
    enforce_gate: bool = True,
) -> Dict[str, Any]:
    """Apply an entry.transform declarative block.

    Returns a NEW dict (does not mutate source).
    Raises ``TransformGateDeniedError`` when ``enforce_gate`` and gate fails.
    """
    if enforce_gate and block.get("gate_field") and not gate_satisfied(source, block):
        gate_field = block.get("gate_field")
        gate_value = block.get("gate_value", True)
        actual = resolve_gate_actual(source, str(gate_field))
        raise TransformGateDeniedError(
            f"gate {gate_field}={gate_value!r} not satisfied (got {actual!r})"
        )
    override_flag = block.get("override_flag")
    if override_flag and not override:
        val = _get_path(source, f"custom_fields.{override_flag}") or _get_path(
            source, override_flag
        )
        if val:
            raise OverrideRequiredError(
                f"source carries '{override_flag}' truthy; caller must pass override=true"
            )
    target: Dict[str, Any] = {"custom_fields": {}}
    for entry in block.get("copy_fields") or []:
        from_path = entry.get("from")
        to_path = entry.get("to") or from_path
        if from_path:
            val = _get_path(source, from_path)
            if val is not None:
                _set_path(target, to_path, val)
    # Strip is applied after copy so a copy_field pointing into a stripped
    # path on source still surfaces on target (semantics: source is read-only
    # input; strip removes from the PROJECTED target).
    for strip_path in block.get("strip_fields") or []:
        _del_path(target, strip_path)
    return target


def apply_public_share_projection(
    entry: Dict[str, Any],
    block: Dict[str, Any],
) -> Dict[str, Any]:
    """Apply an entry.public_share declarative projection.

    Returns whitelist-positive projection. Raises ShareGateDeniedError
    if gate_field/value not satisfied.
    """
    gate_field = block.get("gate_field")
    if gate_field:
        gate_value = block.get("gate_value", True)
        actual = resolve_gate_actual(entry, str(gate_field))
        if actual != gate_value:
            raise ShareGateDeniedError(
                f"gate {gate_field}={gate_value} not satisfied (got {actual!r})"
            )
    out: Dict[str, Any] = {}
    for field in block.get("projection_fields") or []:
        if isinstance(field, str):
            val = _get_path(entry, field)
            if val is None:
                # Also try custom_fields. fallback for short names.
                val = _get_path(entry, f"custom_fields.{field}")
            out[field] = val if val is not None else ""
        elif isinstance(field, dict):
            name = field.get("field")
            src = field.get("source") or name
            if name:
                out[name] = _get_path(entry, src)
    return out


def _normalize(value: Any, mode: str) -> Any:
    if value is None:
        return None
    if mode == "lower_strip":
        return str(value).strip().lower()
    return value


def dedup_candidates(
    source: Dict[str, Any],
    candidates: List[Dict[str, Any]],
    block: Dict[str, Any],
) -> List[Dict[str, Any]]:
    """Apply a connector.dedup declarative match.

    Returns the subset of candidates that match source by ALL declared
    match_field_pairs (after normalize).
    """
    pairs = block.get("match_field_pairs") or []
    if not pairs:
        return []
    out: List[Dict[str, Any]] = []
    for cand in candidates:
        ok = True
        for pair in pairs:
            src_path = pair.get("source")
            tgt_path = pair.get("target")
            norm = pair.get("normalize") or "exact"
            sv = _normalize(_get_path(source, src_path), norm)
            tv = _normalize(_get_path(cand, tgt_path), norm)
            if sv is None or tv is None or sv != tv:
                ok = False
                break
        if ok:
            out.append(cand)
    return out
