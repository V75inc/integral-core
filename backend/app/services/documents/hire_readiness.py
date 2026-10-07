"""Validate recruitment data against a document template before hire."""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional, Tuple

from app.models.nodes import Entry
from app.services.documents.context_resolver import (
    batch_resolve,
    collect_field_keys_from_document,
    token_meta_from_document,
)
from app.services.documents.entry_template_store import extract_contract_template_id
from app.services.documents.field_ref import parse_field_ref
from app.services.documents.field_registry import (
    get_field_spec,
    list_fields_for_track,
    rebuild_workspace_field_index,
)

logger = logging.getLogger(__name__)

_EMPLOYEE_KEY_ALIASES: Dict[str, Tuple[str, ...]] = {
    "employee.full_name": ("title",),
    "employee.personal_email": ("custom_fields.email",),
    "employee.phone": ("custom_fields.phone",),
    "employee.address": ("custom_fields.address_line",),
    "employee.city": ("custom_fields.city",),
    "employee.country": ("custom_fields.country",),
    "employee.job_title": (
        "custom_fields.offered_job_title",
        "custom_fields.current_role",
    ),
    "employee.start_date": ("custom_fields.proposed_start_date",),
    "employee.work_email": ("__input_work_email__",),
}


# Assigned during hire confirmation (not stored on the candidate beforehand).
_HIRE_TIME_FIELD_KEYS = frozenset({"employee.work_email"})

# Required on the recruitment candidate record before hire (stage → hired).
_HIRE_REQUIRED_CANDIDATE_FIELDS: tuple[tuple[str, str], ...] = (
    ("date_of_birth", "Date of birth"),
)

# When templates use track-qualified refs (``recruitment-app.recruitment.title``)
# the hire modal may populate a different local key (``offered_job_title``).
_QUALIFIED_LOCAL_ALIASES: Dict[str, Tuple[str, ...]] = {
    "title": ("custom_fields.offered_job_title", "custom_fields.current_role"),
    "full_name": ("title",),
}


def _cf(entry: Any) -> Dict[str, Any]:
    return dict(getattr(entry, "custom_fields", None) or {})


def _as_single_id(value: Any) -> str:
    from app.services.documents.context_resolver import _relation_entry_id

    return _relation_entry_id(value)


_OPENING_FIELD_LOCALS = frozenset(
    {
        "salary_range",
        "status",
        "department",
        "seniority",
        "position",
    }
)


def _spec_targets_opening_entry(spec: Dict[str, Any]) -> bool:
    """Fields declared on the opening entry type, resolved from a candidate."""
    category = str(spec.get("category") or "").strip().casefold()
    if category in ("opening", "job opening"):
        return True
    local = str(spec.get("local_field_key") or "").strip()
    return local in _OPENING_FIELD_LOCALS


async def _linked_opening_entry(entry: Any) -> Any:
    """Candidate custom field ``opening`` → opening entry."""
    if not entry:
        return None
    opening_id = _as_single_id(_cf(entry).get("opening"))
    if not opening_id:
        return None
    from app.services.documents.context_resolver import _load_entry

    return await _load_entry(opening_id)


async def _resolve_on_linked_opening(
    candidate: Any,
    field_key: str,
    spec: Dict[str, Any],
) -> Tuple[Any, Optional[str]]:
    """Resolve opening-scoped template fields from the candidate's linked opening."""
    from app.services.documents.context_resolver import _walk_relation

    if not _spec_targets_opening_entry(spec):
        parsed = parse_field_ref(field_key)
        local = (parsed or {}).get("local_field") or ""
        if local not in _OPENING_FIELD_LOCALS:
            return None, None

    opening = await _linked_opening_entry(candidate)
    if not opening:
        return None, f"missing:{field_key}"

    source = spec.get("source") or {}
    kind = str(source.get("kind") or "entry_field")
    if kind == "relation_walk":
        val = await _walk_relation(
            opening,
            str(source.get("path") or ""),
            str(source.get("field") or "title"),
        )
        if val not in (None, ""):
            return val, None
        return None, f"missing:{field_key}"

    if kind == "entry_field":
        val = _read_path(opening, str(source.get("path") or ""))
        if val not in (None, ""):
            return val, None
        return None, f"missing:{field_key}"

    return None, None


def _read_path(entry: Any, path: str) -> Any:
    if not entry or not path:
        return None
    if path == "__input_work_email__":
        return None
    if path == "title":
        return getattr(entry, "title", None)
    if path.startswith("custom_fields."):
        key = path.split(".", 1)[1]
        return _cf(entry).get(key)
    return getattr(entry, path, None)


async def _ensure_field_index(
    *,
    workspace_id: str,
    track_id: str,
    field_module: str = "hr_app",
) -> None:
    """Rebuild in-memory registry (lost on process restart) and track schema fields."""
    from app.services.documents.system_fields import ingest_system_document_fields

    await rebuild_workspace_field_index(workspace_id)
    ingest_system_document_fields(workspace_id)
    if track_id:
        await list_fields_for_track(workspace_id, track_id, module=field_module)
        if field_module != "hr_app":
            await list_fields_for_track(workspace_id, track_id, module="hr_app")


def _resolve_qualified_track_field(
    candidate: Any,
    field_key: str,
) -> Tuple[Any, Optional[str]]:
    """Read ``module.track.local`` directly from the candidate when registry lookup fails."""
    parsed = parse_field_ref(field_key)
    if not parsed:
        return None, None
    local = parsed["local_field"]
    paths: List[str] = []
    if local in ("title", "body"):
        paths.append(local)
    else:
        paths.append(f"custom_fields.{local}")
    for alt in _QUALIFIED_LOCAL_ALIASES.get(local, ()):
        if alt not in paths:
            paths.append(alt)
    for path in paths:
        val = _read_path(candidate, path)
        if val not in (None, ""):
            return val, None
    return None, f"missing:{field_key}"


async def _resolve_alias_on_candidate(
    candidate: Entry,
    field_key: str,
    *,
    input_values: Optional[Dict[str, Any]] = None,
) -> Tuple[Any, Optional[str]]:
    paths = _EMPLOYEE_KEY_ALIASES.get(field_key)
    if not paths:
        return None, None
    for path in paths:
        if path == "__input_work_email__":
            val = (input_values or {}).get("work_email")
            if val not in (None, ""):
                return val, None
            continue
        val = _read_path(candidate, path)
        if val not in (None, ""):
            return val, None
    opening_id = _as_single_id(_cf(candidate).get("opening"))
    if opening_id and field_key == "employee.department_name":
        opening = await Entry.get(opening_id)
        if opening:
            dept_id = _as_single_id(_cf(opening).get("department"))
            if dept_id:
                dept = await Entry.get(dept_id)
                if dept:
                    return getattr(dept, "title", None), None
            return getattr(opening, "title", None), None
    if opening_id and field_key in ("employee.location",):
        opening = await Entry.get(opening_id)
        if opening:
            return _cf(opening).get("location"), None
    return None, f"missing:{field_key}"


async def _load_template_version(
    *,
    workspace_id: str,
    template_id: str,
) -> Tuple[Any, Any]:
    from app.services.documents.entry_template_store import resolve_for_generate

    tmpl, ver = await resolve_for_generate(
        workspace_id=workspace_id,
        template_id=template_id,
    )
    if str(getattr(tmpl, "status", "") or "") != "active":
        raise ValueError("Contract template is not active")
    if str(getattr(ver, "status", "") or "") == "draft":
        raise ValueError("Contract template has no published version")
    return tmpl, ver


async def validate_hire_readiness(
    *,
    workspace_id: str,
    candidate_entry: Entry,
    template_id: str,
    input_values: Optional[Dict[str, Any]] = None,
    custom_fields_override: Optional[Dict[str, Any]] = None,
    for_stage_gate: bool = False,
    field_module: str = "hr_app",
) -> Dict[str, Any]:
    """Return readiness report for hiring with the given contract template."""
    cf = {**_cf(candidate_entry), **(custom_fields_override or {})}
    if template_id:
        cf["contract_template"] = template_id

    track_id = str(getattr(candidate_entry, "track_id", "") or "")
    await _ensure_field_index(
        workspace_id=workspace_id,
        track_id=track_id,
        field_module=field_module,
    )

    display_title = str(getattr(candidate_entry, "title", "") or "").strip()
    if not display_title:
        display_title = str(
            cf.get("offered_job_title") or cf.get("current_role") or ""
        ).strip()

    class _CandidateCtx:
        def __init__(self, entry_id: str, title: str, custom_fields: Dict[str, Any]):
            self.id = entry_id
            self.title = title
            self.custom_fields = custom_fields
            self.track_id = track_id

    ctx = _CandidateCtx(
        str(candidate_entry.id),
        display_title,
        cf,
    )
    candidate_entry = ctx  # type: ignore[assignment]
    tmpl, ver = await _load_template_version(
        workspace_id=workspace_id, template_id=template_id
    )
    doc = ver.editor_document or {}
    keys = collect_field_keys_from_document(doc, ver.token_metadata or {})

    resolved = await batch_resolve(
        workspace_id=workspace_id,
        field_keys=keys,
        context_entry=candidate_entry,
        caller_role="editor",
        input_values=input_values or {},
        mode="live",
        token_meta=token_meta_from_document(doc, ver.token_metadata),
        missing_policy="warn",
    )

    missing: List[Dict[str, str]] = []
    seen_keys = set()

    for key in keys:
        if key in seen_keys:
            continue
        seen_keys.add(key)
        if key in _HIRE_TIME_FIELD_KEYS:
            continue
        from app.services.documents.system_fields import is_generation_time_field_key

        if is_generation_time_field_key(key, workspace_id):
            continue
        spec = get_field_spec(workspace_id, key) or {}
        label = str(spec.get("label") or key)

        raw = resolved["values"].get(key)
        warn_hit = any(
            w == f"missing:{key}" or w == f"block:{key}" for w in resolved["warnings"]
        )
        if raw in (None, "") or warn_hit:
            qval, qwarn = _resolve_qualified_track_field(candidate_entry, key)
            if qval not in (None, "") and not qwarn:
                continue
            alias_val, alias_warn = await _resolve_alias_on_candidate(
                candidate_entry, key, input_values=input_values
            )
            if alias_val not in (None, "") and not alias_warn:
                continue
            opening_val, opening_warn = await _resolve_on_linked_opening(
                candidate_entry, key, spec
            )
            if opening_val not in (None, "") and not opening_warn:
                continue
            missing.append({"key": key, "label": label})

    for inp in ver.required_inputs or []:
        if not isinstance(inp, dict) or not inp.get("required", True):
            continue
        inp_key = str(inp.get("key") or "").strip()
        if inp_key and (input_values or {}).get(inp_key) in (None, ""):
            if for_stage_gate and inp_key == "work_email":
                continue
            missing.append(
                {
                    "key": f"input:{inp_key}",
                    "label": str(inp.get("label") or inp_key),
                }
            )

    cf = _cf(candidate_entry)
    for field_key, label in _HIRE_REQUIRED_CANDIDATE_FIELDS:
        if not str(cf.get(field_key) or "").strip():
            missing.append({"key": f"candidate.{field_key}", "label": label})
    if not str(cf.get("email") or "").strip():
        missing.append({"key": "candidate.email", "label": "Candidate email"})
    if not extract_contract_template_id(cf) and not str(template_id or "").strip():
        missing.append(
            {"key": "candidate.contract_template", "label": "Contract template"}
        )

    return {
        "ok": len(missing) == 0,
        "missing_fields": missing,
        "template_id": tmpl.id,
        "template_name": tmpl.name,
        "warnings": resolved["warnings"],
    }
