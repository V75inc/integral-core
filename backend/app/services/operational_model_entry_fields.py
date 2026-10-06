"""Entry field validation and materialization for operational models."""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional, Set, Tuple

from app.exceptions import BadRequestError
from app.models.nodes import Attachment, Entry, EntryType, Tag, Track
from app.services.computed_fields import bind_computed_fields
from app.services.operational_model_compile import (
    SYSTEM_CUSTOM_FIELD_KEYS,
    _as_dict,
    _as_list,
    _normalize_entry_type_base_fields,
    _normalize_field_spec,
    _slug,
    relation_allows_cross_track,
)

logger = logging.getLogger(__name__)

# Workflow column keys agents/users send when moving kanban cards. When the
# entry type has no matching profile field (e.g. bare ``Post``), remap to
# ``_kanban_stage`` (system slot, same contract as ``_kanban_order``).
_KANBAN_WORKFLOW_FIELD_ALIASES = frozenset({"status", "stage", "workflow_status"})
_TYPE_FIELD_CACHE_KEY = "_type_field_cache"

# Explicit opt-in for ``target: track`` relations with ``auto_provision: false``.
# Frontend sends this sentinel instead of a track id; the materializer creates
# the anchor track. Omitted / null + auto_provision false leaves the field null.
CREATE_ANCHOR_SENTINEL = "__create_anchor__"


def _resolve_field_default(raw: Any) -> Any:
    """Resolve declarative field ``default`` sentinels (parity with frontend)."""
    if not isinstance(raw, str):
        return raw
    key = raw.strip().lower()
    if key in {"$today", "today"}:
        from datetime import date

        return date.today().isoformat()
    return raw


async def _resolve_setting_default(track: Track, setting_key: str) -> Any:
    """Read one key from the parent App's settings for ``default_from_setting``.

    Returns ``None`` when the track has no App, the key is missing, or the
    stored value is empty — caller falls through to the normal required check.
    """
    key = str(setting_key or "").strip()
    if not key:
        return None
    from app.services.query_boundary import parent_app_for_track

    app_node = await parent_app_for_track(track)
    if app_node is None:
        return None
    settings = getattr(app_node, "settings", None) or {}
    if not isinstance(settings, dict):
        return None
    value = settings.get(key)
    if value is None:
        return None
    if isinstance(value, str) and value.strip() == "":
        return None
    return value


async def _resolve_or_create_labeled_relation(
    *,
    source_track: Track,
    relation: Dict[str, Any],
    label: str,
    actor_user_id: str,
    actor_kind: str = "human",
) -> Optional[str]:
    """Find or create a target entry by display label for ``create_from_label``.

    Used when a relation is empty on create but its ``write_label_to`` sibling
    carries a name (agent ``create_entry`` with ``customer_name`` only). Looks
    up by title / company_name on the matching target track in the same App;
    creates a minimal entry when none match.
    """
    display = str(label or "").strip()
    if not display:
        return None
    target_track_types = [
        _slug(str(x))
        for x in _as_list(
            relation.get("target_track_types"), where="relation.target_track_types"
        )
        if str(x).strip()
    ]
    target_entry_types = [
        _slug(str(x))
        for x in _as_list(
            relation.get("target_entry_types"), where="relation.target_entry_types"
        )
        if str(x).strip()
    ]
    if not target_track_types:
        return None
    workspace_id = str(getattr(source_track, "workspace_id", "") or "").strip()
    if not workspace_id:
        return None

    # Prefer sibling tracks under the same App (template_id match only — never
    # fall back to title, which can hit another App's similarly named track).
    # Track.app_id is not a persisted node field — resolve parent via CONTAINS
    # (entity name is WorkspaceApp, not App).
    from app.models.edges import CONTAINS
    from app.services.query_boundary import parent_app_for_track

    candidates: List[Track] = []
    app_node = await parent_app_for_track(source_track)
    if app_node is not None:
        candidates = await app_node.nodes(
            edge=[CONTAINS], node=["Track"], limit=200
        )
    if not candidates:
        candidates = await Track.find({"context.workspace_id": workspace_id})

    target_track: Optional[Track] = None
    for t in candidates:
        tmpl = _slug(str(getattr(t, "template_id", "") or ""))
        if tmpl and tmpl in target_track_types:
            target_track = t
            break
    if target_track is None:
        return None

    wanted = display.casefold()
    for existing in await Entry.find({"context.track_id": target_track.id}):
        title = str(getattr(existing, "title", "") or "").strip()
        company = str((existing.custom_fields or {}).get("company_name") or "").strip()
        if title.casefold() == wanted or company.casefold() == wanted:
            return str(existing.id)

    # Create a minimal counterparty-style entry on the target track.
    from app.services.entry_create import create_entry_in_track
    from app.services.entry_type_service import materialize_entry_types_from_tier

    entry_types = await EntryType.find({"context.track_id": target_track.id})
    if not entry_types:
        entry_types = await materialize_entry_types_from_tier(target_track)
    resolved_type: Optional[EntryType] = None
    if target_entry_types:
        for et in entry_types:
            key = _slug(
                str(
                    (et.form_schema or {}).get("_manifest_entry_type_key")
                    or getattr(et, "name", "")
                    or ""
                )
            )
            if key in target_entry_types:
                resolved_type = et
                break
    if resolved_type is None and entry_types:
        resolved_type = entry_types[0]
    if resolved_type is None:
        return None

    created = await create_entry_in_track(
        track=target_track,
        user_id=actor_user_id or "system",
        title=display,
        body="",
        custom_fields={"company_name": display},
        entry_type=resolved_type,
        type_id=resolved_type.id,
        workspace_id=workspace_id,
        actor_kind=actor_kind or "agent",
        skip_profanity=True,
    )
    return str(created.id) if created is not None else None


def _normalize_workflow_field_alias(key: str) -> str:
    return str(key or "").strip().lower().replace(" ", "_")


def restrict_custom_fields_to_entry_type(
    custom_fields: Dict[str, Any],
    *,
    entry_type: EntryType,
    runtime_tier: Dict[str, Any],
) -> Dict[str, Any]:
    """Keep only keys valid for *entry_type* (plus system / ``_`` slots).

    Used when an entry changes type so stale fields (e.g. ``invoice_number``
    on a prior entry type) do not block validation against the new schema.
    """
    spec = resolve_entry_type_spec(entry_type, runtime_tier)
    fields = _as_list(spec.get("fields"), where="entry_type.fields")
    allowed_keys = {str(_as_dict(f, where="field").get("key") or "") for f in fields}
    incoming = remap_kanban_workflow_fields_to_stage(
        dict(custom_fields or {}), allowed_keys
    )
    return {
        k: v
        for k, v in incoming.items()
        if k in SYSTEM_CUSTOM_FIELD_KEYS or k.startswith("_") or k in allowed_keys
    }


def _allowed_field_keys_for_entry_type(
    entry_type: EntryType, runtime_tier: Dict[str, Any]
) -> Set[str]:
    spec = resolve_entry_type_spec(entry_type, runtime_tier)
    fields = _as_list(spec.get("fields"), where="entry_type.fields")
    return {str(_as_dict(f, where="field").get("key") or "") for f in fields}


def transition_custom_fields_on_type_change(
    custom_fields: Dict[str, Any],
    *,
    old_entry_type: EntryType,
    new_entry_type: EntryType,
    runtime_tier: Dict[str, Any],
) -> Dict[str, Any]:
    """Archive dormant profile fields and restore cached values on type switch.

    Dormant values are stored in ``_type_field_cache`` keyed by entry-type
    slug so switching back (e.g. QB_invoice → Post → QB_invoice) recovers
    prior field data without weakening per-type validation.
    """
    incoming = dict(custom_fields or {})
    old_slug = _entry_type_match_key(old_entry_type)
    new_slug = _entry_type_match_key(new_entry_type)
    old_allowed = _allowed_field_keys_for_entry_type(old_entry_type, runtime_tier)
    new_allowed = _allowed_field_keys_for_entry_type(new_entry_type, runtime_tier)

    cache_raw = incoming.get(_TYPE_FIELD_CACHE_KEY)
    cache: Dict[str, Dict[str, Any]] = {}
    if isinstance(cache_raw, dict):
        for slug, bucket in cache_raw.items():
            cache[str(slug)] = dict(bucket) if isinstance(bucket, dict) else {}

    old_bucket = dict(cache.get(old_slug) or {})
    for key, value in incoming.items():
        if key.startswith("_") or key in SYSTEM_CUSTOM_FIELD_KEYS:
            continue
        if key in old_allowed:
            old_bucket[key] = value
    if old_bucket:
        cache[old_slug] = old_bucket

    restricted = restrict_custom_fields_to_entry_type(
        incoming,
        entry_type=new_entry_type,
        runtime_tier=runtime_tier,
    )

    new_bucket = cache.get(new_slug) or {}
    for key, value in new_bucket.items():
        if key in new_allowed:
            restricted[key] = value

    restricted[_TYPE_FIELD_CACHE_KEY] = cache
    return restricted


def remap_kanban_workflow_fields_to_stage(
    incoming: Dict[str, Any], allowed_keys: Set[str]
) -> Dict[str, Any]:
    """Route disallowed workflow keys to ``_kanban_stage`` for kanban boards."""
    out = dict(incoming)
    for key in list(out.keys()):
        if _normalize_workflow_field_alias(key) not in _KANBAN_WORKFLOW_FIELD_ALIASES:
            continue
        if key in allowed_keys:
            continue
        val = out.pop(key)
        if val is not None and str(val).strip() != "":
            out["_kanban_stage"] = val
    return out


def build_entry_index_document(
    custom_fields: Dict[str, Any],
    entry_type_spec: Dict[str, Any],
) -> Dict[str, Any]:
    """Extract profile-guided lightweight search/index payload."""
    fields = _as_list(entry_type_spec.get("fields"), where="entry_type.fields")
    indexed: Dict[str, Any] = {}
    facets: Dict[str, Any] = {}
    for f in fields:
        fd = _as_dict(f, where="field")
        key = str(fd.get("key") or "")
        if not key:
            continue
        value = custom_fields.get(key)
        if value is None:
            continue
        if bool(fd.get("index", False)):
            indexed[key] = value
            if fd.get("type") in {"select", "multi_select", "boolean"}:
                facets[key] = value
    return {
        "entry_type_key": str(entry_type_spec.get("key") or ""),
        "indexed_fields": indexed,
        "facets": facets,
    }


def _entry_type_match_key(entry_type: EntryType) -> str:
    return _slug(str(entry_type.name or ""))


def resolve_entry_type_spec(
    entry_type: EntryType, runtime_tier: Dict[str, Any]
) -> Dict[str, Any]:
    """Return merged entry-type spec from runtime tier, or build one from the EntryType model."""
    specs = _as_list(runtime_tier.get("entry_types"), where="entry_types")
    by_key = {
        _slug(str(_as_dict(s, where="entry_type_spec").get("key") or "")): s
        for s in specs
    }
    by_name = {
        _slug(str(_as_dict(s, where="entry_type_spec").get("name") or "")): s
        for s in specs
    }
    key = _entry_type_match_key(entry_type)
    spec = by_key.get(key) or by_name.get(key)
    form_schema = _as_dict(entry_type.form_schema, where="entry_type.form_schema")
    if spec:
        # The attached runtime tier can lag the EntryType form_schema after a
        # package update (invoice lines gained net_amount, tax_code, …).
        # Prefer the tier, but keep form_schema fields it does not declare.
        # Otherwise an operation write of those fields is rejected and the
        # document header is saved with no lines.
        merged = dict(_as_dict(spec, where="entry_type_spec"))
        spec_fields = [
            _as_dict(f, where="entry_type_spec field")
            for f in _as_list(merged.get("fields"), where="entry_type_spec.fields")
        ]
        known = {str(f.get("key") or "") for f in spec_fields}
        extras: List[Dict[str, Any]] = []
        for raw in _as_list(
            form_schema.get("fields"), where="entry_type.form_schema.fields"
        ):
            field = _normalize_field_spec(
                _as_dict(raw, where="entry_type.form_schema field")
            )
            field_key = str(field.get("key") or "")
            if field_key and field_key not in known:
                extras.append(field)
                known.add(field_key)
        if extras:
            merged["fields"] = [*spec_fields, *extras]
        return merged

    fields = [
        _normalize_field_spec(_as_dict(f, where="entry_type.form_schema field"))
        for f in _as_list(
            form_schema.get("fields"), where="entry_type.form_schema.fields"
        )
    ]
    bind_computed_fields(fields)
    return {
        "key": key,
        "name": entry_type.name,
        "fields": fields,
        "base_fields": _normalize_entry_type_base_fields(
            form_schema.get("base_fields")
        ),
        "required_tag_groups": _as_list(
            form_schema.get("required_tag_groups"),
            where="entry_type.form_schema.required_tag_groups",
        ),
        "singleton": bool(form_schema.get("singleton", False)),
    }


def _coerce_scalar(ftype: str, value: Any, field_key: str) -> Any:
    if value is None:
        return None
    if ftype in {"text", "markdown", "date", "datetime"}:
        if isinstance(value, str):
            return value
        raise BadRequestError(message=f"Field '{field_key}' must be a string")
    if ftype == "number":
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            return value
        raise BadRequestError(message=f"Field '{field_key}' must be a number")
    if ftype == "boolean":
        if isinstance(value, bool):
            return value
        raise BadRequestError(message=f"Field '{field_key}' must be a boolean")
    if ftype == "json":
        if isinstance(value, (dict, list, str, int, float, bool)):
            return value
        raise BadRequestError(message=f"Field '{field_key}' must be a JSON value")
    return value


def _mime_accepts(mime: str, accept_list: List[str]) -> bool:
    """Return True when ``mime`` is permitted by ``accept_list``.

    Empty list means "accept anything". Wildcards are supported in the
    family form (``image/*``) — the standard MIME accept convention.
    """
    if not accept_list:
        return True
    mime = (mime or "").lower()
    for pattern in accept_list:
        pat = pattern.lower().strip()
        if not pat:
            continue
        if pat.endswith("/*"):
            if mime.startswith(pat[:-1]):  # keeps trailing "/"
                return True
        elif mime == pat:
            return True
    return False


async def _validate_file_field_value(
    *,
    field: Dict[str, Any],
    ftype: str,
    value: Any,
    entry: Optional[Entry],
    field_key: str,
) -> Any:
    """Validate the value of a ``file`` / ``files`` custom field.

    The persisted form is either:
        - ftype == "file":  a single attachment id string (or None).
        - ftype == "files": a list of attachment ids.

    Validation enforced:
        1. ``max_count`` from the field's ``config``.
        2. Each id resolves to an existing Attachment node.
        3. The Attachment's MIME is permitted by ``config.accept`` (if
           non-empty).
        4. When ``entry`` is provided, the attachment must already be
           bound to that entry via ``HAS_ATTACHMENT`` (sets the
           ownership constraint on update).
    """
    cfg = _as_dict(field.get("config"), where=f"field '{field_key}' config")
    max_count = int(cfg.get("max_count") or (1 if ftype == "file" else 10))
    accept_list = [str(x) for x in _as_list(cfg.get("accept"), where="")]

    if ftype == "file":
        if value in (None, ""):
            return None
        if not isinstance(value, str):
            raise BadRequestError(
                message=f"Field '{field_key}' expects a single attachment id"
            )
        ids = [value]
    else:  # files
        if value in (None, []):
            return []
        if not isinstance(value, list):
            raise BadRequestError(
                message=f"Field '{field_key}' expects a list of attachment ids"
            )
        ids = [str(x) for x in value if str(x).strip()]

    if len(ids) > max_count:
        raise BadRequestError(
            message=(
                f"Field '{field_key}' exceeds max_count " f"({len(ids)} > {max_count})"
            )
        )

    bound_ids = set(entry.attachment_ids or []) if entry is not None else None

    validated: List[str] = []
    for aid in ids:
        att = await Attachment.get(aid)
        if att is None:
            raise BadRequestError(
                message=(
                    f"Field '{field_key}' references unknown attachment " f"'{aid}'"
                )
            )
        if not _mime_accepts(att.mime_type or "", accept_list):
            raise BadRequestError(
                message=(
                    f"Field '{field_key}' attachment '{aid}' has type "
                    f"'{att.mime_type}' which is not allowed by accept "
                    f"list {accept_list}"
                )
            )
        if bound_ids is not None and aid not in bound_ids:
            raise BadRequestError(
                message=(
                    f"Field '{field_key}' attachment '{aid}' is not "
                    "attached to this entry. Upload via "
                    "/entries/{id}/attachments first."
                )
            )
        validated.append(aid)

    return validated[0] if ftype == "file" else validated


async def _validate_relation_values(
    *,
    relation: Dict[str, Any],
    value: Any,
    source_track: Track,
    field_key: str,
) -> List[str]:
    """Validate relation field values; return the canonical id list.

    Phase 3.1 (ANC-02 + ANC-08 defense-in-depth):
      - target='entry' (default): validate against Entry.get(rid) — existing
        behavior preserved verbatim.
      - target='track': validate against Track.get(rid); assert same-workspace
        constraint (cross-workspace anchoring is out of scope for v1; auto-
        provision path in Plan 03.1-02 fills ``rel["targets"]`` with newly-
        created Track ids that satisfy this constraint by construction —
        this validator is the deep-defense layer).
    """
    target_kind = str(relation.get("target") or "entry").strip().lower()
    many = bool(relation.get("many", False))
    if many:
        if not isinstance(value, list):
            raise BadRequestError(
                message=f"Relation field '{field_key}' must be a list"
            )
        raw_ids = [str(v) for v in value]
    else:
        raw_ids = [str(value)] if value is not None else []

    # Phase 3.1 ANC-02: target='track' branch — Entry → Track anchor validation.
    if target_kind == "track":
        source_ws = getattr(source_track, "workspace_id", "") or ""
        validated: List[str] = []
        for rid in raw_ids:
            tnode = await Track.get(rid)
            if tnode is None:
                raise BadRequestError(
                    message=(
                        f"Relation field '{field_key}' references unknown Track '{rid}'"
                    )
                )
            target_ws = getattr(tnode, "workspace_id", "") or ""
            if source_ws and target_ws and target_ws != source_ws:
                raise BadRequestError(
                    message=(
                        f"Cross-workspace anchoring is not supported in v1 "
                        f"(field '{field_key}': target Track '{rid}' workspace "
                        f"'{target_ws}' != source workspace '{source_ws}')"
                    )
                )
            validated.append(rid)
        return validated

    # target == "entry" — existing behavior preserved verbatim.
    target_entry_types = {str(x) for x in relation.get("target_entry_types") or []}
    target_track_types = {str(x) for x in relation.get("target_track_types") or []}
    allow_cross_track = relation_allows_cross_track(relation)
    validated = []
    for rid in raw_ids:
        target = await Entry.get(rid)
        if not target:
            raise BadRequestError(
                message=f"Relation field '{field_key}' references unknown entry '{rid}'"
            )
        if not allow_cross_track and target.track_id != source_track.id:
            raise BadRequestError(
                message=f"Relation field '{field_key}' cannot reference entries across tracks"
            )
        if target_entry_types:
            target_et = await EntryType.get(target.type_id) if target.type_id else None
            if not target_et or _slug(str(target_et.name)) not in {
                _slug(x) for x in target_entry_types
            }:
                raise BadRequestError(
                    message=f"Relation field '{field_key}' target entry type is not allowed"
                )
        if target_track_types and target.track_id != source_track.id:
            t = await Track.get(target.track_id)
            tkey = _slug(str(getattr(t, "template_id", "") or getattr(t, "title", "")))
            if tkey not in {_slug(x) for x in target_track_types}:
                raise BadRequestError(
                    message=f"Relation field '{field_key}' target track type is not allowed"
                )
        validated.append(rid)
    return validated


async def validate_and_materialize_entry_custom_fields(
    *,
    track: Track,
    entry_type: EntryType,
    custom_fields: Dict[str, Any],
    runtime_tier: Dict[str, Any],
    entry: Optional[Entry] = None,
    actor_user_id: str = "",
    actor_kind: str = "human",
    source_entry_title: str = "",
    materialize: bool = True,
) -> Tuple[Dict[str, Any], List[Dict[str, Any]]]:
    """Validate custom_fields against entry type spec and extract relation refs.

    When ``entry`` is supplied (update flow), ``file``/``files`` fields
    additionally require the referenced attachment ids to already be
    bound to that entry via ``HAS_ATTACHMENT``. On create the entry
    doesn't exist yet, so only existence + MIME + max_count are
    enforced; the caller is responsible for uploading and binding the
    attachment first (the standard frontend flow).
    """
    spec = resolve_entry_type_spec(entry_type, runtime_tier)
    fields = _as_list(spec.get("fields"), where="entry_type.fields")
    incoming = dict(custom_fields or {})
    out = {
        k: v
        for k, v in incoming.items()
        if k in SYSTEM_CUSTOM_FIELD_KEYS or k.startswith("_")
    }
    relation_refs: List[Dict[str, Any]] = []

    allowed_keys = {str(_as_dict(f, where="field").get("key") or "") for f in fields}
    incoming = remap_kanban_workflow_fields_to_stage(incoming, allowed_keys)
    for key in incoming.keys():
        if key.startswith("_"):
            continue
        if key not in allowed_keys:
            raise BadRequestError(
                message=f"Field '{key}' is not allowed for entry type '{entry_type.name}'"
            )

    for f in fields:
        fd = _as_dict(f, where="field")
        key = str(fd.get("key") or "")
        ftype = str(fd.get("type") or "text")
        if ftype == "computed":
            continue
        # Composite field types (declared in manifest ``field_types[]``)
        # carry a ``composite.base`` pointer to the underlying primitive
        # so the validator can dispatch primitive-side rules without
        # re-resolving the manifest registry on every entry write.
        composite = fd.get("composite") if isinstance(fd, dict) else None
        if isinstance(composite, dict) and composite.get("base"):
            ftype = str(composite.get("base") or ftype)
        required = bool(fd.get("required", False))
        value = incoming.get(key, _resolve_field_default(fd.get("default")))
        validation = _as_dict(
            fd.get("validation") or {}, where=f"field '{key}' validation"
        )

        # Agent/API create often supplies the denormalized label
        # (``write_label_to``) without the relation id. When the field opts
        # in via ``create_from_label``, resolve or mint the related entry
        # before the required check — mirrors Finance create_invoice.
        if (
            ftype == "relation"
            and entry is None
            and value in (None, "")
            and bool(validation.get("create_from_label"))
        ):
            label_key = str(validation.get("write_label_to") or "").strip()
            label = str(incoming.get(label_key) or "").strip() if label_key else ""
            if label and isinstance(fd.get("relation"), dict):
                try:
                    resolved = await _resolve_or_create_labeled_relation(
                        source_track=track,
                        relation=_as_dict(
                            fd.get("relation"), where=f"field '{key}' relation"
                        ),
                        label=label,
                        actor_user_id=actor_user_id,
                        actor_kind=actor_kind,
                    )
                except Exception:  # noqa: BLE001 — fall through to required check
                    logger.exception(
                        "create_from_label failed for field %r label %r",
                        key,
                        label,
                    )
                    resolved = None
                if resolved:
                    value = resolved

        # Prefill from parent App settings when the OM declares
        # ``default_from_setting`` (create only — updates keep explicit clears).
        if (
            entry is None
            and (value is None or (isinstance(value, str) and value.strip() == ""))
        ):
            setting_key = str(fd.get("default_from_setting") or "").strip()
            if setting_key:
                resolved_setting = await _resolve_setting_default(track, setting_key)
                if resolved_setting is not None:
                    value = resolved_setting

        # Auto-generate employee_id if left blank on employee creation
        if (
            key == "employee_id"
            and value in (None, "")
            and entry_type.name == "Employee"
        ):
            existing = await Entry.find(
                {"context.track_id": track.id, "context.type_id": entry_type.id}
            )
            max_num = 0
            for e in existing:
                emp_id = (e.custom_fields or {}).get("employee_id")
                if emp_id and isinstance(emp_id, str) and emp_id.strip().isdigit():
                    max_num = max(max_num, int(emp_id.strip()))
            value = f"{max_num + 1:04d}"

        # Validate email fields
        if key in ("work_email", "personal_email") and value:
            import re

            if not re.match(r"^[^@\s]+@[^@\s]+\.[^@\s]+$", str(value).strip()):
                raise BadRequestError(
                    message=f"Field '{key}' must be a valid email address"
                )

        # Validate phone fields
        if key in ("phone", "emergency_contact_phone") and value:
            import re

            val_str = str(value).strip()
            cleaned = re.sub(r"[^\d]", "", val_str)
            if len(cleaned) < 7 or not re.match(r"^[0-9+\-\(\)\s]+$", val_str):
                raise BadRequestError(
                    message=f"Field '{key}' must be a valid phone number"
                )

        # Phase 3.1 ANC-04 auto-provision / opt-in create hook. Applies BEFORE
        # the value-is-None early-return so a relation field with
        # ``target: track`` materializes its anchor track when:
        #   * ``auto_provision: true`` and the caller omits a value, OR
        #   * the caller sends CREATE_ANCHOR_SENTINEL (even when
        #     ``auto_provision: false`` — opt-in Financials / Contracts).
        # A real track id takes the regular target='track' validator path.
        _is_create_sentinel = (
            isinstance(value, str) and value.strip() == CREATE_ANCHOR_SENTINEL
        )
        if (
            ftype == "relation"
            and isinstance(fd.get("relation"), dict)
            and str(fd["relation"].get("target") or "entry") == "track"
            and materialize
            and (
                _is_create_sentinel
                or (value is None and bool(fd["relation"].get("auto_provision", False)))
            )
        ):
            relation = _as_dict(fd.get("relation"), where=f"field '{key}' relation")
            template_key = str(relation.get("target_track_template") or "").strip()
            if not template_key:
                # Provisioning requires a template key to resolve against the
                # app_node's track_templates registry. Silently no-op (field stays
                # None) for auto_provision — manifests are validated at compile
                # time. Explicit sentinel without a template is a client error.
                if _is_create_sentinel:
                    raise BadRequestError(
                        message=(
                            f"Field '{key}' cannot create an anchor track "
                            "(missing target_track_template)"
                        )
                    )
                if required:
                    raise BadRequestError(message=f"Field '{key}' is required")
                out[key] = None
                continue
            many = bool(relation.get("many", False))
            from app.services.operational_model_graph import (
                _maybe_reuse_existing_anchor,
                materialize_anchor_track,
            )

            anchor_id = await _maybe_reuse_existing_anchor(
                source_entry=entry, field_key=key
            )
            if anchor_id is None:
                anchor_track = await materialize_anchor_track(
                    source_track=track,
                    template_key=template_key,
                    field_key=key,
                    actor_user_id=actor_user_id,
                    actor_kind=actor_kind,
                    source_entry_title=source_entry_title,
                )
                anchor_id = anchor_track.id
            out[key] = [anchor_id] if many else anchor_id
            relation_refs.append(
                {
                    "field_key": key,
                    "targets": [anchor_id],
                    "many": many,
                    "allow_cross_track": False,
                    "target": "track",
                    "target_track_template": template_key,
                    "auto_provision": bool(relation.get("auto_provision", False))
                    or _is_create_sentinel,
                }
            )
            continue

        if value is None or (isinstance(value, str) and value.strip() == ""):
            if required:
                raise BadRequestError(message=f"Field '{key}' is required")
            out[key] = None
            continue
        if ftype == "select":
            enum = [
                str(x) for x in _as_list(fd.get("enum"), where=f"field '{key}' enum")
            ]
            sval = str(value)
            if enum and sval not in enum:
                raise BadRequestError(
                    message=f"Field '{key}' must be one of: {', '.join(enum)}"
                )
            out[key] = sval
            continue
        if ftype == "multi_select":
            enum = [
                str(x) for x in _as_list(fd.get("enum"), where=f"field '{key}' enum")
            ]
            values = [str(x) for x in _as_list(value, where=f"field '{key}' value")]
            if enum and any(v not in enum for v in values):
                raise BadRequestError(
                    message=f"Field '{key}' contains values outside allowed enum"
                )
            out[key] = values
            continue
        if ftype == "relation":
            relation = _as_dict(fd.get("relation"), where=f"field '{key}' relation")
            rel_ids = await _validate_relation_values(
                relation=relation,
                value=value,
                source_track=track,
                field_key=key,
            )
            out[key] = (
                rel_ids if relation.get("many") else (rel_ids[0] if rel_ids else None)
            )
            relation_refs.append(
                {
                    "field_key": key,
                    "targets": rel_ids,
                    "many": bool(relation.get("many", False)),
                    "allow_cross_track": relation_allows_cross_track(relation),
                    # Phase 3.1 ANC-02: routing keys consumed by sync_relation_edges
                    # (target='track' → ANCHORS via _sync_anchor_edges; default
                    # 'entry' → REFERENCES via _sync_reference_edges).
                    "target": str(relation.get("target") or "entry"),
                    "target_track_template": str(
                        relation.get("target_track_template") or ""
                    ),
                    "auto_provision": bool(relation.get("auto_provision", False)),
                }
            )
            continue
        # Member field type. Workspace-gated User reference;
        # materializes HAS_MEMBER_REF via _sync_member_ref_edges in
        # operational_model_graph.py.
        if ftype == "member":
            from app.services.operational_model_member_field import (
                validate_member_value,
            )

            relation = _as_dict(
                fd.get("relation") or {}, where=f"field '{key}' relation"
            )
            many = bool(relation.get("many", False))

            if many:
                if not isinstance(value, list):
                    raise BadRequestError(
                        message=f"Member field '{key}' must be a list"
                    )
                user_ids = []
                for val in value:
                    if val is not None and str(val).strip():
                        u_id = await validate_member_value(
                            val,
                            field_key=key,
                            source_track=track,
                            source_entry=entry,
                        )
                        user_ids.append(u_id)
                out[key] = user_ids
                relation_refs.append(
                    {
                        "field_key": key,
                        "targets": user_ids,
                        "many": True,
                        "target": "user",
                    }
                )
            else:
                user_id = await validate_member_value(
                    value,
                    field_key=key,
                    source_track=track,
                    source_entry=entry,
                )
                out[key] = user_id
                relation_refs.append(
                    {
                        "field_key": key,
                        "targets": [user_id],
                        "many": False,
                        "target": "user",
                    }
                )
            continue
        if ftype in {"file", "files"}:
            out[key] = await _validate_file_field_value(
                field=fd,
                ftype=ftype,
                value=value,
                entry=entry,
                field_key=key,
            )
            continue
        out[key] = _coerce_scalar(ftype, value, key)

    # Client clears stored Open Graph snapshot with explicit null; omit from persisted shape.
    if out.get("_link_preview") is None:
        out.pop("_link_preview", None)
    if out.get("_card_preview_attachment_id") is None:
        out.pop("_card_preview_attachment_id", None)

    out["_cp_index"] = build_entry_index_document(out, spec)
    return out, relation_refs


async def validate_taxonomy_constraints(
    *,
    track_id: str,
    tag_ids: List[str],
    entry_type_spec: Dict[str, Any],
    runtime_tier: Dict[str, Any],
) -> None:
    """Ensure assigned tags satisfy entry-type required_tag_groups against runtime taxonomy."""
    required_groups = [str(x) for x in entry_type_spec.get("required_tag_groups") or []]
    if not required_groups:
        return
    taxonomy = _as_dict(runtime_tier.get("taxonomy"), where="taxonomy")
    groups = _as_list(taxonomy.get("tag_groups"), where="taxonomy.tag_groups")
    group_to_names: Dict[str, Set[str]] = {}
    for g in groups:
        gd = _as_dict(g, where="taxonomy.group")
        gkey = str(gd.get("key") or "")
        names = {
            str(_as_dict(t, where="taxonomy.group.tag").get("name") or "").lower()
            for t in _as_list(gd.get("tags"), where="taxonomy.group.tags")
        }
        group_to_names[gkey] = {n for n in names if n}
    assigned_names: Set[str] = set()
    for tid in tag_ids or []:
        t = await Tag.get(tid)
        if t and t.track_id == track_id:
            assigned_names.add(str(t.name or "").lower())
    for gkey in required_groups:
        allowed = group_to_names.get(gkey) or set()
        if not allowed:
            raise BadRequestError(
                message=f"Required taxonomy group '{gkey}' is not defined in profile"
            )
        if not (assigned_names & allowed):
            raise BadRequestError(
                message=f"Entry must include at least one tag from required group '{gkey}'"
            )


async def validate_tags_apply_to_entry_type(
    *,
    track_id: str,
    tag_ids: Optional[List[str]],
    entry_type: Optional[EntryType],
) -> None:
    """Reject tags whose ``applies_to_entry_types`` omits the entry type (manifest taxonomy)."""
    if not entry_type:
        return
    want = _slug(str(entry_type.name or ""))
    for tid in tag_ids or []:
        tag = await Tag.get(str(tid))
        if not tag or tag.track_id != track_id:
            continue
        raw_apply = getattr(tag, "applies_to_entry_types", None) or []
        restrict = [str(x) for x in raw_apply] if isinstance(raw_apply, list) else []
        if not restrict:
            continue
        allowed = {_slug(x) for x in restrict}
        if want not in allowed:
            raise BadRequestError(
                message=(
                    f"Tag {tag.name!r} cannot be used with entry type "
                    f"{entry_type.name!r} per the operational model taxonomy"
                )
            )
