"""Canonical manifest compilation — pure helpers (no graph writes).

Split from ``content_profile_runtime`` per ``.planning/refactors/content_profile_runtime_split_plan.md``.
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
from contextvars import ContextVar
from typing import Any, Dict, List, Optional, Set, Tuple

from app.exceptions import (
    BadRequestError,
    ContentProfileV1RejectedError,
    ContentProfileValidationError,
)
from app.services import content_profile_field_types as field_type_registry
from app.services import content_profile_wizard_steps as wizard_step_registry
from app.services.content_profile_field_types import FieldTypeSpec
from app.views import content_profile_view_types as view_type_registry
from app.views.content_profile_view_types import ViewTypeSpec

try:
    import yaml  # type: ignore
except Exception:  # pragma: no cover
    yaml = None  # type: ignore  # pragma: no cover

logger = logging.getLogger(__name__)

SCHEMA_VERSION = 2
VALID_PROFILE_SCOPES = {"track", "app", "workspace"}

# Phase 10 Plan 10-03 — caps for v2 operational-layer section parsers. These
# guardrails mitigate T-10-03-04 (DoS via pathological manifests). Caps are
# generous defaults; v2.1 may move them to per-publisher config.
MAX_SKILLS_PER_APP = 256
MAX_AGENTS_PER_APP = 32
MAX_SETTINGS_SCHEMA_PROPERTIES = 1024
MAX_SEEDS_PER_APP = 4096
MAX_REQUIRES_APPS_PER_APP = 64
MAX_UI_COMPLEMENTS_PER_PROFILE = 64

# Phase 10 Plan 10-03 — allowed values for normalized v2 section fields.
_VALID_SKILL_KINDS = {"declarative", "custom"}
_VALID_AGENT_SCOPES = {"app", "workspace"}
_VALID_AGENT_STAGING = {"required", "optional", "disabled"}
_VALID_WORKSPACE_ROLES = {"admin", "member", "guest"}
_VALID_APP_ROLES = {"owner", "editor", "commenter", "viewer", "member"}
_VALID_TRUST_TIERS = {"trusted", "untrusted", "audited"}
_VALID_SETTINGS_WIDGETS = {
    "text",
    "textarea",
    "select",
    "multi_select",
    "boolean",
    "entry_picker",
    "tag_picker",
    "number",
    "date",
}

# Default upper bound for ``files`` fields when the manifest omits
# ``config.max_count``. ``file`` (singular) is implicitly capped at 1.
DEFAULT_FILES_MAX_COUNT = 10
SYSTEM_CUSTOM_FIELD_KEYS = {
    "_entry_type_slug",
    "_cp_index",
    "_kanban_order",
    "_kanban_stage",
    "_type_field_cache",
}


# ``VALID_FIELD_TYPES`` and ``VALID_VIEW_TYPES`` were exported as plain sets
# before the registry refactor; tests + downstream callers still import them.
# Resolve via module-level ``__getattr__`` so callers always see the live
# registry contents (including plugin-registered types).
def __getattr__(name: str) -> Any:
    if name == "VALID_FIELD_TYPES":
        return frozenset(field_type_registry.allowed_keys())
    if name == "VALID_VIEW_TYPES":
        return frozenset(view_type_registry.allowed_keys())
    raise AttributeError(name)


# ---------------------------------------------------------------------------
# Per-compile composite resolution
# ---------------------------------------------------------------------------
#
# When ``compile_canonical_manifest`` is processing a manifest that declares
# top-level ``field_types[]`` or ``view_types[]`` composites, those composite
# specs are stashed in these contextvars for the duration of the compile so
# that the downstream normalizers (``_normalize_field_spec``,
# ``_normalize_view_spec``, ``_normalize_package_meta``) can resolve composite
# references without explicit signature changes.
_active_field_composites: ContextVar[Optional[Dict[str, FieldTypeSpec]]] = ContextVar(
    "_cp_active_field_composites", default=None
)
_active_view_composites: ContextVar[Optional[Dict[str, ViewTypeSpec]]] = ContextVar(
    "_cp_active_view_composites", default=None
)


def _current_field_composites() -> Dict[str, FieldTypeSpec]:
    """Return the active per-compile field composites map (empty if none)."""
    val = _active_field_composites.get()
    return val if val is not None else {}


def _current_view_composites() -> Dict[str, ViewTypeSpec]:
    """Return the active per-compile view composites map (empty if none)."""
    val = _active_view_composites.get()
    return val if val is not None else {}


def _field_type_known(type_: str) -> bool:
    return field_type_registry.is_known(type_) or type_ in _current_field_composites()


def _view_type_known(type_: str) -> bool:
    return view_type_registry.is_known(type_) or type_ in _current_view_composites()


def _field_type_primitive(type_: str) -> str:
    """Return the underlying primitive type for a composite or primitive."""
    composites = _current_field_composites()
    spec = field_type_registry.resolve(type_, profile_composites=composites)
    if spec is None:
        return type_
    if spec.base is None:
        return spec.type
    # Walk composite chain to a primitive.
    seen: Set[str] = set()
    cur = spec
    while cur.base:
        if cur.type in seen:
            return cur.type
        seen.add(cur.type)
        nxt = field_type_registry.resolve(cur.base, profile_composites=composites)
        if nxt is None:
            return cur.base
        cur = nxt
    return cur.type


def _view_type_primitive(type_: str) -> str:
    """Return the underlying primitive view type for a composite or primitive."""
    composites = _current_view_composites()
    spec = view_type_registry.resolve(type_, profile_composites=composites)
    if spec is None:
        return type_
    if spec.base is None:
        return spec.type
    seen: Set[str] = set()
    cur = spec
    while cur.base:
        if cur.type in seen:
            return cur.type
        seen.add(cur.type)
        nxt = view_type_registry.resolve(cur.base, profile_composites=composites)
        if nxt is None:
            return cur.base
        cur = nxt
    return cur.type


def _slug(value: str) -> str:
    s = re.sub(r"[^a-zA-Z0-9]+", "_", (value or "").strip().lower()).strip("_")
    return s or "item"


def slug_manifest_key(value: str) -> str:
    """Normalize manifest entry-type / view keys for comparisons."""
    if not str(value or "").strip():
        return ""
    return _slug(value)


# Kanban column placement for tracks whose EntryTypes lack a workflow
# ``status`` field. Underscore prefix = system custom_fields slot (I-GRAPH
# passthrough), same contract as ``_kanban_order``.
KANBAN_STAGE_GROUP_BY = "custom_fields._kanban_stage"


def _as_dict(value: Any, *, where: str) -> Dict[str, Any]:
    if value is None:
        return {}
    if not isinstance(value, dict):
        raise BadRequestError(message=f"{where} must be an object")
    return value


def _as_list(value: Any, *, where: str) -> List[Any]:
    if value is None:
        return []
    if not isinstance(value, list):
        raise BadRequestError(message=f"{where} must be a list")
    return value


def _optional_int(value: Any) -> Any:
    """Coerce manifest/order scalars to int when possible; pass through otherwise."""
    if value is None or isinstance(value, bool):
        return value
    try:
        return int(value)
    except (TypeError, ValueError):
        return value


def _normalize_field_spec(field: Dict[str, Any]) -> Dict[str, Any]:
    key = str(field.get("key") or "").strip()
    name = str(field.get("name") or key).strip()
    ftype = str(field.get("type") or "text").strip().lower()
    if not key:
        raise BadRequestError(message="field.key is required")
    if not _field_type_known(ftype):
        raise BadRequestError(
            message=f"Unsupported field type '{ftype}' for field '{key}'"
        )
    composites = _current_field_composites()
    composite_meta: Optional[Dict[str, Any]] = None
    if ftype in composites:
        cspec = composites[ftype]
        composite_meta = {
            "base": _field_type_primitive(ftype),
            "config": dict(cspec.composite_config or {}),
            "label": cspec.label or ftype,
            "description": cspec.description or "",
        }
    out = {
        "key": key,
        "name": name or key,
        "type": ftype,
        "required": bool(field.get("required", False)),
        "readonly": bool(field.get("readonly", False)),
        # Excludes this field from the CREATE-entry form only (EntryFormExpanded's
        # dynamicFields); still renders normally once the entry exists (edit
        # mode, detail page, related_views). For fields a server-side hook
        # fills in right after creation (mirrored/carried-forward values) —
        # showing them on the create form as fillable fields is misleading
        # (they'll be overwritten/filled a moment after save) and, when the
        # field is also `required: true` elsewhere, can block creation
        # entirely before the hook ever gets a chance to run.
        "hide_on_create": bool(field.get("hide_on_create", False)),
        "default": field.get("default"),
        "enum": _as_list(field.get("enum"), where=f"field '{key}' enum"),
        "widget": field.get("widget"),
        "placeholder": field.get("placeholder"),
        "help": field.get("help"),
        "group": field.get("group"),
        "order": _optional_int(field.get("order")),
        "validation": _as_dict(
            field.get("validation"), where=f"field '{key}' validation"
        ),
        "index": bool(field.get("index", False)),
    }
    if composite_meta is not None:
        out["composite"] = composite_meta
    relation = _as_dict(field.get("relation"), where=f"field '{key}' relation")
    if ftype == "relation":
        # Phase 3.1 ANC-02: relation.target ∈ {"entry", "track"} routes to
        # REFERENCES (entry→entry value lookup) or ANCHORS (entry→track
        # expansion). Default is 'entry' for back-compat with every existing
        # manifest. Unknown values raise a deterministic BadRequestError.
        target_kind = str(relation.get("target") or "entry").strip().lower()
        if target_kind not in ("entry", "track"):
            raise BadRequestError(
                message=(
                    f"Unsupported relation.target '{target_kind}' for field '{key}'"
                )
            )
        many = bool(relation.get("many", False))

        # Phase 3.1 Plan 03.1-03 ANC-03 — optional per-anchor-field governance
        # block. Captures the three governance dimensions:
        #   cardinality: 'one' | 'many'   (defaults from many flag)
        #   cascade:     'hard' | 'preserve'   (default 'hard')
        #   acl_inheritance: 'inherit' | 'independent'   (default 'inherit')
        # Governance Policies are materialized at content-profile publish time
        # with scope=f"anchor_field:{cp_id}:{entry_type_key}:{field_key}". The
        # block is accepted on any relation field (defaults applied uniformly),
        # but is only consulted by policy_engine.evaluate for target='track'
        # anchor fields.
        raw_gov = relation.get("governance")
        if raw_gov is None:
            raw_gov = {}
        if not isinstance(raw_gov, dict):
            raise BadRequestError(
                message=(f"relation.governance for field '{key}' must be an object")
            )
        cardinality = (
            str(raw_gov.get("cardinality") or ("many" if many else "one"))
            .strip()
            .lower()
        )
        if cardinality not in ("one", "many"):
            raise BadRequestError(
                message=(
                    f"relation.governance.cardinality must be 'one' or 'many' "
                    f"for field '{key}'"
                )
            )
        cascade = str(raw_gov.get("cascade") or "hard").strip().lower()
        if cascade not in ("hard", "preserve"):
            raise BadRequestError(
                message=(
                    f"relation.governance.cascade must be 'hard' or 'preserve' "
                    f"for field '{key}'"
                )
            )
        acl_inheritance = (
            str(raw_gov.get("acl_inheritance") or "inherit").strip().lower()
        )
        if acl_inheritance not in ("inherit", "independent"):
            raise BadRequestError(
                message=(
                    f"relation.governance.acl_inheritance must be 'inherit' or "
                    f"'independent' for field '{key}'"
                )
            )

        # Phase 10 Plan 10-06 (APP-CROSS-RELATIONS-01) — cross-App relation
        # field declarations. Fields are additive — manifests that do not set
        # ``target_app`` see no behavior change.
        #
        #   target_app: Optional[str]             — library package key of the
        #                                            target App (must be installed
        #                                            in the workspace OR declared
        #                                            in this manifest's
        #                                            ``app.requires_apps[]``).
        #   allow_cross_app: bool = False         — explicit opt-in for the
        #                                            cross-App leg. Setting
        #                                            ``target_app`` without
        #                                            ``allow_cross_app=True`` is
        #                                            a validation error.
        #   label_field: Optional[str] = None     — dotted path resolved by
        #                                            ``read_cross_app_label`` (the
        #                                            ONLY safe reader; Risk 4
        #                                            / Pitfall 5 grep-gate).
        #   on_target_uninstall:                  — cascade policy when the
        #     Literal["block","null",                target App is uninstalled.
        #             "archive_self"]              Default "block" — uninstall
        #                                          raises 409 when any inbound
        #                                          edge exists.
        #   resolution: "workspace" | "instance:<app_id>"
        #                                          — multi-install disambiguation.
        #                                          "workspace" errors on ambiguity;
        #                                          "instance:<id>" pins a specific
        #                                          install.
        cross_app_target = relation.get("target_app")
        cross_app_target_str = str(cross_app_target).strip() if cross_app_target else ""
        allow_cross_app = bool(relation.get("allow_cross_app", False))
        # target_app + allow_cross_app interlock — declaring target_app without
        # opting in via allow_cross_app=True is a compile error (T-10-06-04
        # belt-and-suspenders: makes cross-App traversal explicit on every
        # manifest, even when target_app is set to the same App's key).
        if cross_app_target_str and not allow_cross_app:
            raise BadRequestError(
                message=(
                    f"relation.target_app set on field '{key}' requires "
                    f"relation.allow_cross_app: true"
                )
            )
        label_field_raw = relation.get("label_field")
        label_field_str = str(label_field_raw).strip() if label_field_raw else ""
        on_target_uninstall = (
            str(relation.get("on_target_uninstall") or "block").strip().lower()
        )
        if on_target_uninstall not in ("block", "null", "archive_self"):
            raise BadRequestError(
                message=(
                    f"relation.on_target_uninstall for field '{key}' must be "
                    f"one of 'block' | 'null' | 'archive_self' (got "
                    f"{on_target_uninstall!r})"
                )
            )
        resolution_raw = str(relation.get("resolution") or "workspace").strip()
        # resolution is either the literal "workspace" OR "instance:<app_id>".
        if resolution_raw != "workspace" and not resolution_raw.startswith("instance:"):
            raise BadRequestError(
                message=(
                    f"relation.resolution for field '{key}' must be "
                    f"'workspace' or 'instance:<app_id>' (got "
                    f"{resolution_raw!r})"
                )
            )
        if (
            resolution_raw.startswith("instance:")
            and not resolution_raw[len("instance:") :].strip()
        ):
            raise BadRequestError(
                message=(
                    f"relation.resolution 'instance:' requires a non-empty "
                    f"app id on field '{key}'"
                )
            )

        out["relation"] = {
            "target": target_kind,  # NEW (ANC-02)
            "target_entry_types": [
                str(x) for x in _as_list(relation.get("target_entry_types"), where="")
            ],
            "target_track_types": [
                str(x) for x in _as_list(relation.get("target_track_types"), where="")
            ],
            # Plan 03.1-02 consumes target_track_template + auto_provision on
            # the auto-provision hook; Plan 03.1-01 only normalizes + carries.
            "target_track_template": str(relation.get("target_track_template") or ""),
            "auto_provision": bool(relation.get("auto_provision", False)),
            "allow_cross_track": bool(relation.get("allow_cross_track", False)),
            "many": many,
            "inverse_field": relation.get("inverse_field"),
            # Phase 3.1 Plan 03.1-03 ANC-03 — governance block.
            "governance": {
                "cardinality": cardinality,
                "cascade": cascade,
                "acl_inheritance": acl_inheritance,
            },
            # Phase 10 Plan 10-06 — cross-App fields (additive).
            "target_app": cross_app_target_str,
            "allow_cross_app": allow_cross_app,
            "label_field": label_field_str,
            "on_target_uninstall": on_target_uninstall,
            "resolution": resolution_raw,
        }
    elif relation:
        out["relation"] = relation

    # Plan 03 — Phase 4: file / files field config.
    # Manifest shape:
    #   - type: file              # or "files"
    #     config:
    #       accept: [application/pdf, image/*]    # MIME allow-list
    #       max_count: 5                          # files only; defaults to 10
    #       expose_metadata:                      # surface attachment metadata
    #         - { from: type_specific.author, as: contract_author }
    #         - { from: common.page_count, as: contract_pages }
    if ftype in {"file", "files"}:
        file_cfg = _as_dict(field.get("config"), where=f"field '{key}' config")
        accept_raw = _as_list(
            file_cfg.get("accept"), where=f"field '{key}' config.accept"
        )
        expose_raw = _as_list(
            file_cfg.get("expose_metadata"),
            where=f"field '{key}' config.expose_metadata",
        )
        # Normalize expose_metadata entries to {from, as} pairs and
        # drop incomplete rows so downstream resolution doesn't have
        # to defensively check.
        expose_metadata: List[Dict[str, str]] = []
        for row in expose_raw:
            if not isinstance(row, dict):
                continue
            src = str(row.get("from") or "").strip()
            alias = str(row.get("as") or "").strip()
            if not src:
                continue
            expose_metadata.append({"from": src, "as": alias or src.split(".")[-1]})
        try:
            max_count = int(
                file_cfg.get(
                    "max_count",
                    1 if ftype == "file" else DEFAULT_FILES_MAX_COUNT,
                )
            )
        except (TypeError, ValueError):
            raise BadRequestError(
                message=(f"field '{key}' config.max_count must be an integer")
            )
        if max_count < 1:
            raise BadRequestError(
                message=(f"field '{key}' config.max_count must be >= 1")
            )
        # ``file`` (singular) is implicitly capped at 1 regardless of
        # what the manifest declares — collapse silently to avoid
        # surprising downstream branches.
        if ftype == "file":
            max_count = 1
        out["config"] = {
            "accept": [str(x).strip().lower() for x in accept_raw if str(x).strip()],
            "max_count": max_count,
            "expose_metadata": expose_metadata,
        }
    return out


def _normalize_entry_type_base_fields(base_fields: Any) -> Dict[str, Any]:
    base = _as_dict(base_fields, where="entry_type.base_fields")
    title_raw = _as_dict(base.get("title"), where="entry_type.base_fields.title")
    body_raw = _as_dict(base.get("body"), where="entry_type.base_fields.body")
    attachments_raw = _as_dict(
        base.get("attachments"), where="entry_type.base_fields.attachments"
    )
    title_out: Dict[str, Any] = {
        "enabled": bool(title_raw.get("enabled", True)),
        "label": str(title_raw.get("label") or "Title"),
        "placeholder": str(title_raw.get("placeholder") or ""),
        "help": str(title_raw.get("help") or ""),
    }
    if "order" in title_raw and title_raw.get("order") is not None:
        try:
            title_out["order"] = int(title_raw["order"])
        except (TypeError, ValueError):
            raise BadRequestError(
                message="entry_type.base_fields.title.order must be an integer"
            )

    body_out: Dict[str, Any] = {
        "enabled": bool(body_raw.get("enabled", True)),
        "label": str(body_raw.get("label") or "Body"),
        "placeholder": str(body_raw.get("placeholder") or ""),
        "help": str(body_raw.get("help") or ""),
    }
    if "order" in body_raw and body_raw.get("order") is not None:
        try:
            body_out["order"] = int(body_raw["order"])
        except (TypeError, ValueError):
            raise BadRequestError(
                message="entry_type.base_fields.body.order must be an integer"
            )

    attachments_out: Dict[str, Any] = {
        "enabled": bool(attachments_raw.get("enabled", True)),
        "label": str(attachments_raw.get("label") or "Attachments"),
        "help": str(attachments_raw.get("help") or ""),
        "allow_file_upload": bool(attachments_raw.get("allow_file_upload", True)),
        "allow_url_reference": bool(attachments_raw.get("allow_url_reference", True)),
    }
    if "order" in attachments_raw and attachments_raw.get("order") is not None:
        try:
            attachments_out["order"] = int(attachments_raw["order"])
        except (TypeError, ValueError):
            raise BadRequestError(
                message="entry_type.base_fields.attachments.order must be an integer"
            )

    return {"title": title_out, "body": body_out, "attachments": attachments_out}


def _normalize_entry_type_spec(spec: Dict[str, Any]) -> Dict[str, Any]:
    name = str(spec.get("name") or "").strip()
    key = str(spec.get("key") or _slug(name)).strip()
    if not name:
        raise BadRequestError(message="entry type name is required")
    fields = [
        _normalize_field_spec(_as_dict(f, where=f"entry type '{name}' field"))
        for f in _as_list(spec.get("fields"), where=f"entry type '{name}' fields")
    ]

    # Phase 3.1 Plan 03.1-04 (ANC-06) — ``related_views`` additive slot.
    # Each related view: ``{view: <view_ref>, bind: <Dict[str, Any]>}``.
    # ``view_ref`` is either a bare view key (resolved within the current
    # track's CP) or a resolver-prefixed reference of form
    # ``:<resolver_token>/<view_key>`` (the resolver returns a track id; the
    # view_key then resolves within THAT track's CP). Validation here is
    # additive — empty/missing related_views is fine (back-compat).
    related_views_raw = _as_list(
        spec.get("related_views"), where=f"entry type '{name}' related_views"
    )
    related_views: List[Dict[str, Any]] = []
    for idx, rv in enumerate(related_views_raw):
        rd = _as_dict(rv, where=f"entry type '{name}' related_views[{idx}]")
        view_ref = str(rd.get("view") or "").strip()
        if not view_ref:
            raise BadRequestError(
                message=(f"entry type '{name}' related_views[{idx}].view is required")
            )
        bind = _as_dict(
            rd.get("bind"),
            where=f"entry type '{name}' related_views[{idx}].bind",
        )
        position = str(rd.get("position") or "related").strip().lower()
        if position not in ("primary", "related"):
            position = "related"
        related_views.append({"view": view_ref, "bind": bind, "position": position})

    return {
        "key": key or _slug(name),
        "name": name,
        "icon": str(spec.get("icon") or "document"),
        "fields": fields,
        "base_fields": _normalize_entry_type_base_fields(spec.get("base_fields")),
        "required_tag_groups": [
            str(x)
            for x in _as_list(
                spec.get("required_tag_groups"),
                where=f"entry type '{name}' required_tag_groups",
            )
        ],
        "related_views": related_views,
        # Opt-in: entries of this type open as a dedicated full page
        # (EntryPage.tsx) instead of the default modal overlay. Defaults to
        # False so every existing entry type's behavior is unchanged.
        "open_as_page": bool(spec.get("open_as_page", False)),
        # Opt-in: at most one entry of this type may exist per track (e.g.
        # a "settings-shaped" employer-identity record a bundle wants to
        # exist exactly once). Enforced generically at create time in
        # app/api/entries.py — no domain-specific string in substrate
        # scope; the bundle just sets this flag on the entry type. Defaults
        # to False so every existing entry type's behavior is unchanged.
        # Only prevents NEW duplicates going forward — pre-existing data
        # from before this flag was set is left alone (see the create-time
        # check's own comment for how readers of an already-non-singleton
        # track should degrade gracefully).
        "singleton": bool(spec.get("singleton", False)),
        # Opt-in: a multi-step create flow (region_system's create_wizard
        # primitive) replaces the default single-form create dialog for
        # this entry type. None when unset — every existing entry type's
        # create behavior is unchanged. See _normalize_create_wizard.
        "create_wizard": _normalize_create_wizard(
            spec.get("create_wizard"), where=f"entry type '{name}' create_wizard"
        ),
    }


def _normalize_create_wizard(raw: Any, *, where: str) -> Optional[Dict[str, Any]]:
    """A declarative multi-step create flow — region_system's generic
    ``create_wizard`` primitive. Replaces the default single-form create
    dialog for any entry type that declares it, same opt-in shape as
    ``open_as_page``.

    Deliberately lenient/shallow validation (unlike related_views' strict
    per-field checks): this is a new, still-settling primitive with only
    one real consumer so far — over-specifying its schema now would just
    mean re-relaxing it for the next consumer's shape. Steps are one of
    three kinds, each reusing an EXISTING rendering convention rather than
    inventing new ones:
      - ``form``: reuses form_region's own `fields` (ordered field KEYS,
        resolved against this entry type's own `fields[]` at render time)
        + an optional `prefill_tool` (a workspace tool key called with no
        args whose JSON keys are merged into the form's initial values).
      - ``entry_checklist``: NEW, genuinely reusable step kind — pick N of
        M entries from `source_track_type` (a track title), optionally
        filtered by `active_field` (a boolean/status field name), shown
        with `display_columns` ([{key, label, source_field?, join?}] —
        `join: {track_type, on_field, show_field}` resolves a value from a
        RELATED track, e.g. an employee's compensation rate — the same
        "resolve through a relation" idea a grouped register-style view
        would use for its own identity columns). Optional `filter` narrows the
        candidate rows to those whose related record matches an
        earlier-step form value — `{join: {track_type, on_field,
        match_field}, against_form_field}` (e.g. only employees whose
        Compensation Record `pay_frequency` equals the `frequency` picked
        in an earlier `form` step).
      - ``period_picker``: a labeled dropdown of candidate values computed
        by a workspace tool (``periods_tool``, called with an optional
        `input_fields` subset of the currently-collected form values as
        its args, returning ``{periods: [{label, ...field values...}]}``)
        — selecting one fills the listed `fields` all at once. First item
        pre-selected (the tool's own job to order "soonest first").
        `input_fields` render INLINE in this same step as normal field
        controls (not just forwarded from an earlier step) — e.g. a
        Frequency select sits above the Period dropdown, and changing it
        re-fetches periods for the new value. Reusable for any recurring-
        schedule scenario (payroll cadences, invoicing cycles, subscription
        renewals, …) — not tied to any one app's domain.
      - ``summary``: read-only review step, no config.
    ``on_create_tool`` (a workspace tool key) is called after the entry is
    created, with `entry_id` plus the checked entry_checklist step's ids
    under `on_create_ids_param` (default "employee_ids").
    """
    if not raw:
        return None
    d = _as_dict(raw, where=where)
    steps_raw = _as_list(d.get("steps"), where=f"{where}.steps")
    steps: List[Dict[str, Any]] = []
    for idx, s in enumerate(steps_raw):
        sd = _as_dict(s, where=f"{where}.steps[{idx}]")
        kind = str(sd.get("kind") or "").strip().lower()
        # Registry-driven, not a hardcoded tuple — step kinds are registered
        # by app/plugins/region_system/__init__.py at plugin discovery, the
        # same way view types are (see content_profile_wizard_steps.py's
        # own docstring for why). A known-kinds list is still surfaced in
        # the error for a readable message.
        if not wizard_step_registry.is_known(kind):
            known = (
                ", ".join(sorted(wizard_step_registry.list_step_kinds()))
                or "(none registered)"
            )
            raise BadRequestError(
                message=(f"{where}.steps[{idx}].kind must be one of: {known}")
            )
        step: Dict[str, Any] = {
            "kind": kind,
            "key": str(sd.get("key") or kind),
            "title": str(sd.get("title") or ""),
        }
        if kind == "form":
            step["fields"] = [
                str(f)
                for f in _as_list(
                    sd.get("fields"), where=f"{where}.steps[{idx}].fields"
                )
            ]
            if sd.get("prefill_tool"):
                step["prefill_tool"] = str(sd["prefill_tool"])
        elif kind == "period_picker":
            step["periods_tool"] = str(sd.get("periods_tool") or "")
            step["fields"] = [
                str(f)
                for f in _as_list(
                    sd.get("fields"), where=f"{where}.steps[{idx}].fields"
                )
            ]
            # Optional — forward the named earlier-step form values as the
            # periods_tool's call args (e.g. a prior 'form' step collected
            # `frequency`; the periods tool needs it to pick the right
            # cadence). Generic: any step kind that ends up in formValues
            # can be named here, not payroll-specific.
            if sd.get("input_fields"):
                step["input_fields"] = [
                    str(f)
                    for f in _as_list(
                        sd.get("input_fields"),
                        where=f"{where}.steps[{idx}].input_fields",
                    )
                ]
        elif kind == "entry_checklist":
            step["source_track_type"] = str(sd.get("source_track_type") or "")
            if sd.get("active_field"):
                step["active_field"] = str(sd["active_field"])
            # Optional — block Next/Create while nothing is checked. Default
            # false: a checklist step may legitimately allow zero selected
            # (e.g. an optional "link related records" step). Opt in where
            # zero selected makes the entry meaningless — an empty
            # candidate list (every row filtered out by an earlier step's
            # choice) would otherwise silently let the entry be created
            # with nothing behind it. Generic — reusable by any
            # entry_checklist step; domain manifests opt in per step.
            if sd.get("required"):
                step["required"] = bool(sd["required"])
            display_columns_raw = _as_list(
                sd.get("display_columns"), where=f"{where}.steps[{idx}].display_columns"
            )
            display_columns = []
            for col in display_columns_raw:
                cd = _as_dict(col, where=f"{where}.steps[{idx}].display_columns[]")
                col_out: Dict[str, Any] = {
                    "key": str(cd.get("key") or ""),
                    "label": str(cd.get("label") or cd.get("key") or ""),
                }
                if cd.get("source_field"):
                    col_out["source_field"] = str(cd["source_field"])
                if cd.get("join"):
                    jd = _as_dict(
                        cd.get("join"),
                        where=f"{where}.steps[{idx}].display_columns[].join",
                    )
                    col_out["join"] = {
                        "track_type": str(jd.get("track_type") or ""),
                        "on_field": str(jd.get("on_field") or ""),
                        "show_field": str(jd.get("show_field") or ""),
                    }
                display_columns.append(col_out)
            step["display_columns"] = display_columns
            # Optional — narrow the checklist to rows whose RELATED record
            # matches an earlier-step form value (e.g. only employees whose
            # Compensation Record pay_frequency equals the frequency picked
            # in step 1). Same join shape as display_columns' join, plus
            # which of that joined record's fields to compare and which
            # collected form value to compare it against. Generic — reusable
            # for any "pick from M, but only the ones matching an earlier
            # choice" scenario, not payroll-specific.
            if sd.get("filter"):
                fd = _as_dict(sd.get("filter"), where=f"{where}.steps[{idx}].filter")
                jd = _as_dict(fd.get("join"), where=f"{where}.steps[{idx}].filter.join")
                step["filter"] = {
                    "join": {
                        "track_type": str(jd.get("track_type") or ""),
                        "on_field": str(jd.get("on_field") or ""),
                        "match_field": str(jd.get("match_field") or ""),
                    },
                    "against_form_field": str(fd.get("against_form_field") or ""),
                }
        steps.append(step)
    return {
        "steps": steps,
        "on_create_tool": str(d.get("on_create_tool") or ""),
        "on_create_ids_param": str(d.get("on_create_ids_param") or "employee_ids"),
    }


def _normalize_package_meta(package: Dict[str, Any]) -> Dict[str, Any]:
    pkg = _as_dict(package, where="package")
    out = dict(pkg)
    raw_caps = _as_list(pkg.get("capabilities"), where="package.capabilities")
    caps_out: List[Dict[str, Any]] = []
    for cap in raw_caps:
        if isinstance(cap, str):
            cap_type = str(cap).strip().lower()
            if not cap_type:
                continue
            caps_out.append({"type": cap_type, "config": {}})
            continue
        cd = _as_dict(cap, where="package.capabilities[]")
        cap_type = str(cd.get("type") or "").strip().lower()
        if not cap_type:
            raise BadRequestError(message="package.capabilities[] requires type")
        caps_out.append(
            {
                "type": cap_type,
                "config": _as_dict(
                    cd.get("config"), where=f"package.capability '{cap_type}' config"
                ),
            }
        )
    for c in caps_out:
        ctype = str(c.get("type") or "")
        if ctype and not _view_type_known(ctype):
            raise BadRequestError(
                message=(
                    f"Unknown capability '{ctype}'. "
                    f"Available: {', '.join(sorted(view_type_registry.allowed_keys()))}"
                )
            )
    raw_deps = _as_list(pkg.get("dependencies"), where="package.dependencies")
    deps_out: List[Dict[str, Any]] = []
    seen_ids: Set[str] = set()
    for dep in raw_deps:
        dd = _as_dict(dep, where="package.dependencies[]")
        dep_id = str(dd.get("id") or "").strip()
        dep_version = str(dd.get("version") or "").strip()
        if not dep_id:
            raise BadRequestError(message="package.dependencies[] requires id")
        if not dep_version:
            raise BadRequestError(
                message=f"package dependency '{dep_id}' requires version"
            )
        if dep_id in seen_ids:
            continue
        seen_ids.add(dep_id)
        deps_out.append({"id": dep_id, "version": dep_version})
    out["capabilities"] = caps_out
    out["dependencies"] = deps_out
    return out


def _validate_entry_type_relation_targets(
    entry_type_specs: List[Dict[str, Any]], *, where: str
) -> None:
    entry_keys = {
        _slug(str(spec.get("key") or spec.get("name") or ""))
        for spec in entry_type_specs
        if isinstance(spec, dict)
    }
    for spec in entry_type_specs:
        sd = _as_dict(spec, where=f"{where}.entry_types[]")
        sname = str(sd.get("name") or sd.get("key") or "entry_type")
        for f in _as_list(
            sd.get("fields"), where=f"{where}.entry_types[{sname}].fields"
        ):
            fd = _as_dict(f, where=f"{where}.entry_types[{sname}].fields[]")
            if str(fd.get("type") or "") != "relation":
                continue
            relation = _as_dict(
                fd.get("relation"), where=f"{where}.entry_types[{sname}].relation"
            )
            target_entry_types = _as_list(
                relation.get("target_entry_types"),
                where=f"{where}.entry_types[{sname}].relation.target_entry_types",
            )
            allow_cross_track = bool(relation.get("allow_cross_track", False))
            has_target_track_types = bool(
                _as_list(
                    relation.get("target_track_types"),
                    where=(f"{where}.entry_types[{sname}].relation.target_track_types"),
                )
            )
            for t in target_entry_types:
                tslug = _slug(str(t))
                if tslug not in entry_keys and not (
                    allow_cross_track and has_target_track_types
                ):
                    raise BadRequestError(
                        message=(
                            f"Relation field '{fd.get('key')}' references entry "
                            f"type '{t}', which is not defined in {where}. Either "
                            f"add a '{t}' entry type to this track, or — for a "
                            f"cross-track lookup — set relation.allow_cross_track: "
                            f"true and name the target track in "
                            f"relation.target_track_types."
                        )
                    )


def _validate_related_view_scope_placement(tier: Dict[str, Any], *, where: str) -> None:
    """Reject a ``related_views[]`` entry that resolves (same-tier, bare key
    only) to a view whose registered ``ViewTypeSpec.scope`` is ``"track"``
    (the default — see ``content_profile_view_types.ViewTypeSpec.scope``).

    ``related_views[].view`` is a reference to a ``SavedView`` declared in
    this SAME tier's ``views[]`` by key (or a resolver-prefixed
    ``:token/key`` reference into another, not-yet-compiled track's CP —
    see ``RelatedViewsSection.tsx``'s docstring). Only the bare, same-tier
    form is resolvable at this point in the compile, so a resolver-prefixed
    reference is skipped (fail-soft, consistent with the frontend's own
    fail-soft handling of an unresolved resolver token). An unresolved bare
    key is also skipped — that is a different, pre-existing failure mode
    (dangling reference) this check does not own.

    Deliberately NOT the inverse direction (an entry-scoped view type
    declared in ``views[]``): ``views[]`` is the tier's full view registry,
    not a "track tabs" list — every existing entry-scoped widget (action
    bars, summary tiles, form regions, …) is declared there today
    specifically so ``related_views[]`` can reference it by key (confirmed
    against real installed app-bundle library manifests under
    ``backend/app/profiles/``). Track-tab candidacy is filtered
    client-side by scope (``TrackDetailPage.tsx`` excludes
    ``scope: 'entry'`` — see ``frontend/src/views/types.ts``); rejecting
    the declaration itself here would break every one of those existing
    profiles.
    """
    views_by_key: Dict[str, Dict[str, Any]] = {
        str(_as_dict(v, where=f"{where}.views[]").get("key") or ""): _as_dict(
            v, where=f"{where}.views[]"
        )
        for v in _as_list(tier.get("views"), where=f"{where}.views")
    }
    for spec in _as_list(tier.get("entry_types"), where=f"{where}.entry_types"):
        sd = _as_dict(spec, where=f"{where}.entry_types[]")
        sname = str(sd.get("name") or sd.get("key") or "entry_type")
        for idx, rv in enumerate(
            _as_list(
                sd.get("related_views"),
                where=f"{where}.entry_types[{sname}].related_views",
            )
        ):
            rd = _as_dict(
                rv, where=f"{where}.entry_types[{sname}].related_views[{idx}]"
            )
            view_ref = str(rd.get("view") or "")
            if not view_ref or view_ref.startswith(":"):
                # Resolver-prefixed (cross-track) reference — the target
                # track's CP is not available at this compile.
                continue
            view_spec = views_by_key.get(view_ref)
            if view_spec is None:
                # Dangling same-tier reference — a different failure mode,
                # not this check's concern.
                continue
            view_type = str(view_spec.get("view_type") or "")
            vt_spec = view_type_registry.resolve(view_type)
            if vt_spec is not None and vt_spec.scope not in ("entry", "both"):
                raise BadRequestError(
                    message=(
                        f"{where}.entry_types[{sname}].related_views[{idx}] "
                        f"references view '{view_ref}' of type '{view_type}', "
                        f"which is track-scoped and cannot be placed in "
                        f"related_views (entry-scoped or scope='both' view "
                        f"types only)"
                    )
                )


def _normalize_track_template_spec(
    spec: Dict[str, Any], *, where: str
) -> Dict[str, Any]:
    """Normalize a single ``app.track_templates[]`` entry.

    Phase 3.1 ANC-04 registry: named track-template catalogue under a
    App-attached ContentProfile. Targeted by ``relation.target_track_template``
    on entry-type relation fields with ``target: track`` + ``auto_provision: true``.

    Distinct from ``app.tracks[]`` (the prescribed-tracks list which the
    App-attached profile auto-provisions on App create). A track template
    is NEVER auto-provisioned on App create — only on entry create, lazily,
    by the auto-provision hook (Plan 03.1-02 ``materialize_anchor_track``).

    Mirrors the per-track normalization in ``app.tracks[]`` for entry_types
    / views / taxonomy / defaults so the template spec can be consumed by the
    same downstream machinery (``ensure_track_attached_content_profile`` +
    materialize hook + ``resolve_track_runtime_profile`` etc.).
    """
    td = _as_dict(spec, where=where)
    name = str(td.get("name") or "").strip()
    if not name:
        raise BadRequestError(message=f"{where} requires name")
    key = str(td.get("key") or _slug(name))
    # Track templates are a registry catalogue — not live track tiers. Feed
    # fallback runs when a template is materialized onto a Track (ANC-04).
    return _sync_kanban_column_enums_in_tier(
        {
            "key": key,
            "name": name,
            "description": str(td.get("description") or "").strip(),
            "entry_types": [
                _normalize_entry_type_spec(
                    _as_dict(e, where=f"{where}[{key!r}].entry_types[]")
                )
                for e in _as_list(td.get("entry_types"), where=f"{where}.entry_types")
            ],
            "views": [
                _normalize_view_spec(_as_dict(v, where=f"{where}[{key!r}].views[]"))
                for v in _as_list(td.get("views"), where=f"{where}.views")
            ],
            "taxonomy": _normalize_taxonomy(
                _as_dict(td.get("taxonomy"), where=f"{where}.taxonomy")
            ),
            "defaults": _as_dict(td.get("defaults"), where=f"{where}.defaults"),
        }
    )


def _validate_app_relation_graph(app_spec: Dict[str, Any]) -> None:
    tracks = _as_list(app_spec.get("tracks"), where="app.tracks")
    track_keys = {
        str(_as_dict(t, where="app.tracks[]").get("key") or "")
        for t in tracks
        if str(_as_dict(t, where="app.tracks[]").get("key") or "")
    }
    relations = _as_list(app_spec.get("relations"), where="app.relations")
    graph: Dict[str, Set[str]] = {}
    for rel in relations:
        rd = _as_dict(rel, where="app.relations[]")
        src = str(rd.get("source_track_type") or "").strip()
        dst = str(rd.get("target_track_type") or "").strip()
        if not src or not dst:
            continue
        if src and src not in track_keys:
            raise BadRequestError(
                message=f"app_node.relations source_track_type '{src}' not found in app_node.tracks"
            )
        if dst and dst not in track_keys:
            raise BadRequestError(
                message=f"app_node.relations target_track_type '{dst}' not found in app_node.tracks"
            )
        graph.setdefault(src, set()).add(dst)

    visited: Set[str] = set()
    active: Set[str] = set()

    def _walk(node: str) -> bool:
        if node in active:
            return True
        if node in visited:
            return False
        visited.add(node)
        active.add(node)
        for nxt in graph.get(node, set()):
            if _walk(nxt):
                return True
        active.remove(node)
        return False

    for n in graph.keys():
        if _walk(n):
            raise BadRequestError(
                message="Circular relation detected in app_node.relations"
            )


def _normalize_view_spec(spec: Dict[str, Any]) -> Dict[str, Any]:
    view_type = str(spec.get("view_type") or spec.get("type") or "feed").lower()
    if not _view_type_known(view_type):
        raise BadRequestError(message=f"Unsupported view type '{view_type}'")
    composites = _current_view_composites()
    composite_meta: Optional[Dict[str, Any]] = None
    if view_type in composites:
        cspec = composites[view_type]
        composite_meta = {
            "base": _view_type_primitive(view_type),
            "config": dict(cspec.composite_config or {}),
            "label": cspec.label or view_type,
            "description": cspec.description or "",
        }
    out = {
        "key": str(spec.get("key") or _slug(str(spec.get("name") or view_type))),
        "name": str(spec.get("name") or view_type.title()),
        "view_type": view_type,
        "is_default": bool(spec.get("is_default", False)),
        # Builtin view types (table, feed, kanban, ...) only ever got
        # ``hidden: true`` preserved through the plugin passthrough loop
        # below (``vt_spec.source != "builtin"``) — action_bar/
        # layout_container happen to be plugin-registered so their
        # ``hidden`` survived, while table/feed/kanban's did not: any
        # manifest declaring a hidden implementation-only table tab
        # surfaced it as a normal, visible tab on every install. Explicit
        # key here fixes it for every builtin view type, not just the
        # ones a passthrough happened to cover.
        "hidden": bool(spec.get("hidden", False)),
        "filters": _as_list(spec.get("filters"), where="view.filters"),
        "sort": _as_list(spec.get("sort"), where="view.sort"),
        "group_by": spec.get("group_by"),
        "layout": _as_dict(spec.get("layout"), where="view.layout"),
        "field_visibility": _as_list(
            spec.get("field_visibility"), where="view.field_visibility"
        ),
        "kanban_columns": _as_list(
            spec.get("kanban_columns"), where="view.kanban_columns"
        ),
        "calendar_mapping": _as_dict(
            spec.get("calendar_mapping"), where="view.calendar_mapping"
        ),
        "columns": _as_list(spec.get("columns"), where="view.columns"),
        # ``entry_types`` is the legacy alias used by some bundle manifests
        # (and ContentProfile authoring docs) for view-scoped slice projection.
        # Treat it as a fallback for ``entry_type_keys`` so authors who follow
        # the older convention still get a constrained view rather than an
        # empty all-entries fall-through. Explicit ``entry_type_keys`` wins
        # when both are present.
        "entry_type_keys": _as_list(
            spec.get("entry_type_keys") or spec.get("entry_types"),
            where="view.entry_type_keys",
        ),
        "config": _as_dict(spec.get("config"), where="view.config"),
    }
    # Wiki view fields — declared inline on the view spec, not nested under
    # ``config:``. The runtime view-config materializer (`materialize_view_config_from_spec`)
    # mirrors these top-level keys into the persisted ``config`` blob; carry
    # them through the canonical-compile output so downstream consumers can
    # see the wiki layout knobs.
    for wiki_key in (
        "parent_field",
        "body_field",
        "title_field",
        "sort_siblings",
        "default_page_id",
        "default_entry_type",
    ):
        if wiki_key in spec:
            out[wiki_key] = spec[wiki_key]
    # Non-builtin view types (plugin-registered or manifest composites) own
    # their entire config shape — the named-key extraction above (plus the
    # wiki-specific keys) only covers the handful of built-in widgets. Without
    # this, any flat top-level key a plugin widget declares that isn't one of
    # those named keys (e.g. chart_region's ``chart_type``/``y_field``,
    # tree_region's ``label_field``) is silently dropped from the compiled
    # manifest output. Same fix as ``normalize_view_config``'s equivalent
    # passthrough; builtin behavior is untouched since this branch never runs
    # for them.
    vt_spec = view_type_registry.resolve(view_type)
    if vt_spec is not None and vt_spec.source != "builtin":
        for key, value in spec.items():
            if key not in out:
                out[key] = value
    if composite_meta is not None:
        out["composite"] = composite_meta
    return out


WORKFLOW_FIELD_ALIASES = ("status", "stage")
KANBAN_STAGE_FIELD_KEY = "_kanban_stage"


def _is_workflow_select_field(field: Dict[str, Any]) -> bool:
    ftype = str(field.get("type") or "").lower()
    return ftype in ("select", "multi_select")


def _resolve_kanban_group_field_key(group_by: str) -> str:
    g = str(group_by or "").strip()
    if g.startswith("custom_fields."):
        return g.split("custom_fields.", 1)[1].split(".")[-1] or g
    if "." in g:
        return g.split(".")[-1]
    return g


def _resolve_kanban_group_by(raw: Any) -> str:
    g = str(raw or "").strip()
    if not g or g == "status":
        return KANBAN_STAGE_GROUP_BY
    return g


def _find_workflow_select_field_key(fields: List[Dict[str, Any]]) -> Optional[str]:
    by_key = {str(f.get("key") or ""): f for f in fields}
    for alias in WORKFLOW_FIELD_ALIASES:
        spec = by_key.get(alias)
        if spec and _is_workflow_select_field(spec):
            return alias
    return None


def _resolve_kanban_write_field_key(group_by: Any, fields: List[Dict[str, Any]]) -> str:
    resolved = _resolve_kanban_group_by(group_by)
    field_key = _resolve_kanban_group_field_key(resolved)
    by_key = {str(f.get("key") or ""): f for f in fields}
    if field_key != KANBAN_STAGE_FIELD_KEY and _is_workflow_select_field(
        by_key.get(field_key) or {}
    ):
        return field_key
    if field_key == KANBAN_STAGE_FIELD_KEY:
        workflow = _find_workflow_select_field_key(fields)
        if workflow:
            return workflow
    return field_key


def _entry_type_matches_slug(name: str, slug: str) -> bool:
    name_slug = _slug(name)
    want = _slug(slug)
    if not name_slug or not want:
        return False
    if name_slug == want:
        return True
    return name_slug.replace("_", "") == want.replace("_", "")


def _sync_kanban_column_enums_in_tier(tier: Dict[str, Any]) -> Dict[str, Any]:
    """Merge kanban column keys into the grouped select field enum at compile time."""
    entry_types = list(_as_list(tier.get("entry_types"), where="entry_types"))
    views = list(_as_list(tier.get("views"), where="views"))
    if not entry_types or not views:
        return tier
    defaults = _as_dict(tier.get("defaults"), where="defaults")
    default_entry_type = str(defaults.get("default_entry_type") or "").strip()

    for view in views:
        vd = _as_dict(view, where="views[]")
        if str(vd.get("view_type") or "").lower() != "kanban":
            continue
        config = _as_dict(vd.get("config"), where="view.config")
        group_by = config.get("group_by") or vd.get("group_by")
        kanban_columns = _as_list(
            config.get("kanban_columns") or vd.get("kanban_columns"),
            where="kanban_columns",
        )
        column_keys = [
            str(_as_dict(c, where="kanban_columns[]").get("key") or "").strip()
            for c in kanban_columns
        ]
        column_keys = [k for k in column_keys if k]
        if not column_keys:
            continue

        view_type_keys = [
            str(x).strip()
            for x in _as_list(
                vd.get("entry_type_keys") or config.get("entry_type_keys"),
                where="entry_type_keys",
            )
            if str(x).strip()
        ]
        view_default = str(
            vd.get("default_entry_type_key")
            or vd.get("default_entry_type")
            or config.get("default_entry_type_key")
            or config.get("default_entry_type")
            or ""
        ).strip()

        if view_type_keys:
            target_slugs = view_type_keys
        elif view_default:
            target_slugs = [view_default]
        elif default_entry_type:
            target_slugs = [default_entry_type]
        else:
            target_slugs = [
                str(et.get("key") or _slug(str(et.get("name") or "")))
                for et in entry_types
            ]

        for et in entry_types:
            et_key = str(et.get("key") or _slug(str(et.get("name") or ""))).strip()
            if not any(_entry_type_matches_slug(et_key, t) for t in target_slugs):
                continue
            fields = list(_as_list(et.get("fields"), where="fields"))
            write_key = _resolve_kanban_write_field_key(group_by, fields)
            if not write_key or write_key.startswith("_"):
                continue
            updated = False
            new_fields: List[Dict[str, Any]] = []
            for f in fields:
                fd = dict(f)
                if str(fd.get("key") or "") == write_key and _is_workflow_select_field(
                    fd
                ):
                    enum_vals = [str(x) for x in _as_list(fd.get("enum"), where="enum")]
                    for ck in column_keys:
                        if ck not in enum_vals:
                            enum_vals.append(ck)
                            updated = True
                    fd["enum"] = enum_vals
                new_fields.append(fd)
            if updated:
                et["fields"] = new_fields

    tier["entry_types"] = entry_types
    return tier


def _ensure_feed_view_fallback(
    tier: Dict[str, Any], *, where: str, suppress: bool = False
) -> Dict[str, Any]:
    """Ensure feed exists, and becomes default only when no default is declared.

    ``suppress`` (from a track's manifest-declared ``suppress_feed_fallback:
    true``) skips adding the fallback Feed view entirely — for a track whose
    only meaningful entry point is a purpose-built landing view (e.g. a
    folders/directory view over config records), a bare chronological Feed
    tab is noise, not a safety net. Declaring at least one other view is
    still the caller's responsibility — this only removes the automatic
    Feed addition, it does not itself guarantee a track is non-empty.

    Stamps ``suppress_feed_fallback`` onto the OUTPUT tier (not just reads
    it off the input) — an app-track spec compiled here gets round-tripped
    through a second compile pass when its own track-attached ContentProfile
    is materialized (``apply_space_track_spec_to_track`` ->
    ``_track_spec_to_library_manifest_dict`` -> a fresh ``scope: "track"``
    compile). That second pass re-reads ``suppress_feed_fallback`` off
    whatever tier it's handed; since the compiled tier previously never
    carried the flag forward, an already-suppressed track silently grew a
    Feed view back on that second pass (found live: Guyana/Aruba/BVI/Curaçao
    payroll's consolidated Settings track, which declares
    ``suppress_feed_fallback: true``, still ended up with a visible Feed tab
    after install). Writing it here makes the suppression durable across any
    number of re-compiles instead of being consumed once and forgotten.
    """
    tier["suppress_feed_fallback"] = suppress
    if suppress:
        return tier
    views = list(_as_list(tier.get("views"), where=f"{where}.views"))
    defaults = dict(_as_dict(tier.get("defaults"), where=f"{where}.defaults"))
    explicit_default = bool(str(defaults.get("default_view") or "").strip())

    feed_indices = [
        idx
        for idx, v in enumerate(views)
        if str(_as_dict(v, where=f"{where}.views[]").get("view_type") or "")
        .strip()
        .lower()
        == "feed"
    ]
    if not feed_indices:
        views.append(
            _normalize_view_spec(
                {
                    "key": "feed",
                    "name": "Feed",
                    "view_type": "feed",
                    "is_default": not explicit_default,
                }
            )
        )
        feed_indices = [len(views) - 1]

    if not explicit_default:
        winner = feed_indices[0]
        winner_vd = _as_dict(views[winner], where=f"{where}.views[]")
        winner_key = str(winner_vd.get("key") or "feed").strip() or "feed"
        defaults["default_view"] = winner_key
        for idx, v in enumerate(views):
            vd = _as_dict(v, where=f"{where}.views[]")
            vd["is_default"] = idx == winner
            views[idx] = vd

    tier["views"] = views
    tier["defaults"] = defaults
    return _sync_kanban_column_enums_in_tier(tier)


def _dedupe_specs_by_key(
    items: List[Dict[str, Any]],
    *,
    key_field: str = "key",
    slug_keys: bool = False,
) -> List[Dict[str, Any]]:
    """Collapse duplicate keyed specs (later wins). Repairs merged manifests."""
    by_key: Dict[str, Dict[str, Any]] = {}
    for it in items:
        if not isinstance(it, dict):
            continue
        raw = str(it.get(key_field) or "").strip()
        if not raw:
            continue
        k = _slug(raw) if slug_keys else raw
        by_key[k] = it
    return list(by_key.values())


def repair_stored_manifest_for_compile(manifest: Dict[str, Any]) -> Dict[str, Any]:
    """Dedupe keyed lists on persisted manifests before runtime compile.

    Library/profile merges can append duplicate ``entry_types[]`` rows (same
    ``key``). Author-time ``compile_canonical_manifest`` still rejects those;
    this helper only runs when reading an attached ContentProfile from the DB.
    """
    import copy

    m = copy.deepcopy(manifest)
    scope = str(m.get("scope") or "").strip().lower()

    def _repair_track_tier(tier: Dict[str, Any]) -> None:
        ets = tier.get("entry_types")
        if isinstance(ets, list):
            tier["entry_types"] = _dedupe_specs_by_key(
                [e for e in ets if isinstance(e, dict)],
                key_field="key",
                slug_keys=True,
            )
        views = tier.get("views")
        if isinstance(views, list):
            tier["views"] = _dedupe_specs_by_key(
                [v for v in views if isinstance(v, dict)],
                key_field="key",
                slug_keys=True,
            )
        tax = tier.get("taxonomy")
        if isinstance(tax, dict):
            groups = tax.get("tag_groups")
            if isinstance(groups, list):
                tax["tag_groups"] = _dedupe_specs_by_key(
                    [g for g in groups if isinstance(g, dict)],
                    key_field="key",
                    slug_keys=True,
                )

    if scope == "track":
        track = m.get("track")
        if isinstance(track, dict):
            _repair_track_tier(track)
    elif scope == "app":
        app = m.get("app")
        if isinstance(app, dict):
            tracks = app.get("tracks")
            if isinstance(tracks, list):
                app["tracks"] = _dedupe_specs_by_key(
                    [t for t in tracks if isinstance(t, dict)],
                    key_field="key",
                    slug_keys=True,
                )
                for t in app["tracks"]:
                    if isinstance(t, dict):
                        _repair_track_tier(t)
            templates = app.get("track_templates")
            if isinstance(templates, list):
                app["track_templates"] = _dedupe_specs_by_key(
                    [t for t in templates if isinstance(t, dict)],
                    key_field="key",
                    slug_keys=True,
                )
                for t in app["track_templates"]:
                    if isinstance(t, dict):
                        _repair_track_tier(t)
    return m


def _assert_unique_keys(
    items: List[Dict[str, Any]],
    *,
    key_field: str = "key",
    where: str,
) -> None:
    """Raise BadRequestError if any two items share the same ``key_field`` value.

    Used inside manifest compilation to refuse duplicate ``entry_types[].key``,
    ``views[].key``, ``tag_groups[].key``, or ``app_node.tracks[].key`` declarations.
    """
    seen: Set[str] = set()
    for it in items:
        k = str(it.get(key_field) or "").strip()
        if not k:
            continue
        if k in seen:
            raise BadRequestError(message=f"duplicate {key_field} '{k}' in {where}")
        seen.add(k)


#: The complete set of permissions a public share link can carry. Declaring a
#: key outside this set is a hard error rather than a silent drop — these gate
#: anonymous access, so a typo must not quietly widen or narrow the surface.
PUBLIC_SHARE_PERMISSION_KEYS = (
    "read_entries",
    "create_entries",
    "update_entries",
    "read_comments",
    "create_comments",
)


def _normalize_public_share_spec(spec: Any, *, where: str) -> Optional[Dict[str, Any]]:
    """Validate a track spec's ``public_share`` block.

    Returns ``None`` when absent or explicitly disabled, so the compiled spec
    carries the key only when a track genuinely asks to be publicly shared.

    This declares INTENT only. Provisioning never mints a link from it — see
    ``services/content_profile_merge.provision_prescribed_tracks_from_app_manifest``
    and ``tests/test_provisioned_public_share.py`` for why auto-minting a
    show-once token with no recipient is unsafe.
    """
    if spec is None:
        return None
    block = _as_dict(spec, where=where)

    enabled_raw = block.get("enabled", False)
    if not isinstance(enabled_raw, bool):
        raise BadRequestError(message=f"{where}.enabled must be boolean")
    if not enabled_raw:
        return None

    perms_raw = _as_dict(block.get("permissions"), where=f"{where}.permissions")
    unknown = sorted(set(perms_raw) - set(PUBLIC_SHARE_PERMISSION_KEYS))
    if unknown:
        raise BadRequestError(
            message=(
                f"{where}.permissions has unknown key(s) {unknown!r}; "
                f"allowed: {list(PUBLIC_SHARE_PERMISSION_KEYS)!r}"
            )
        )
    permissions: Dict[str, bool] = {}
    for key in PUBLIC_SHARE_PERMISSION_KEYS:
        value = perms_raw.get(key, False)
        if not isinstance(value, bool):
            raise BadRequestError(message=f"{where}.permissions.{key} must be boolean")
        permissions[key] = value

    return {"enabled": True, "permissions": permissions}


def _normalize_taxonomy(taxonomy: Dict[str, Any]) -> Dict[str, Any]:
    groups_out: List[Dict[str, Any]] = []
    for g in _as_list(taxonomy.get("tag_groups"), where="taxonomy.tag_groups"):
        gd = _as_dict(g, where="taxonomy.tag_groups[]")
        gkey = str(gd.get("key") or _slug(str(gd.get("name") or "group")))
        gname = str(gd.get("name") or gkey)
        tags_out: List[Dict[str, Any]] = []
        for t in _as_list(gd.get("tags"), where=f"taxonomy group '{gname}' tags"):
            td = _as_dict(t, where=f"taxonomy group '{gname}' tag")
            tname = str(td.get("name") or "").strip()
            if not tname:
                raise BadRequestError(
                    message=f"taxonomy tag in group '{gname}' requires name"
                )
            tags_out.append(
                {
                    "key": str(td.get("key") or _slug(tname)),
                    "name": tname,
                    "color": str(td.get("color") or "#6B7280"),
                    "aliases": [str(a) for a in _as_list(td.get("aliases"), where="")],
                    "parent_key": td.get("parent_key"),
                    "applies_to": [
                        str(a) for a in _as_list(td.get("applies_to"), where="")
                    ],
                }
            )
        groups_out.append(
            {
                "key": gkey,
                "name": gname,
                "tags": tags_out,
            }
        )
    _assert_unique_keys(groups_out, where="taxonomy.tag_groups")
    return {"tag_groups": groups_out}


def _normalize_calendar_date_field_key(raw: Any) -> str:
    s = str(raw or "").strip()
    if s.startswith("custom_fields."):
        return s[len("custom_fields.") :]
    return s


def _validate_calendar_view_mappings(tier: Dict[str, Any], *, where: str) -> None:
    """Calendar views must map to a date field declared on at least one entry type."""
    readonly = {"created_at", "updated_at"}
    all_field_keys: Set[str] = set()
    for e in _as_list(tier.get("entry_types"), where=f"{where}.entry_types"):
        ed = _as_dict(e, where=f"{where}.entry_types[]")
        for f in _as_list(ed.get("fields"), where="fields"):
            fd = _as_dict(f, where="fields[]")
            k = str(fd.get("key") or "").strip()
            if k:
                all_field_keys.add(k)
    for v in _as_list(tier.get("views"), where=f"{where}.views"):
        vd = _as_dict(v, where=f"{where}.views[]")
        if str(vd.get("view_type") or "").lower() != "calendar":
            continue
        cm = _as_dict(vd.get("calendar_mapping"), where="calendar_mapping")
        date_field = _normalize_calendar_date_field_key(
            cm.get("date_field") or cm.get("dateField")
        )
        if not date_field or date_field in readonly:
            continue
        if date_field not in all_field_keys:
            view_key = str(vd.get("key") or "calendar")
            raise ContentProfileValidationError(
                message=(
                    f"{where}.views[{view_key!r}].calendar_mapping date field "
                    f"{date_field!r} is not declared on any entry type in this tier"
                )
            )


def _validate_track_tier_defaults(tier: Dict[str, Any], *, where: str) -> None:
    """Ensure optional ``defaults.default_entry_type`` / ``defaults.default_view`` reference tier keys."""
    defaults = _as_dict(tier.get("defaults"), where=f"{where}.defaults")
    entry_keys: Set[str] = set()
    for e in _as_list(tier.get("entry_types"), where=f"{where}.entry_types"):
        ed = _as_dict(e, where=f"{where}.entry_types[]")
        k = str(ed.get("key") or "").strip()
        if k:
            entry_keys.add(_slug(k))
    view_keys: Set[str] = set()
    for v in _as_list(tier.get("views"), where=f"{where}.views"):
        vd = _as_dict(v, where=f"{where}.views[]")
        k = str(vd.get("key") or "").strip()
        if k:
            view_keys.add(_slug(k))
    det = defaults.get("default_entry_type")
    if det is not None and str(det).strip():
        want = _slug(str(det))
        if want not in entry_keys:
            raise BadRequestError(
                message=(
                    f"{where}.defaults.default_entry_type must match an entry "
                    f"type key in this tier (got {det!r})"
                )
            )
    dv = defaults.get("default_view")
    if dv is not None and str(dv).strip():
        want = _slug(str(dv))
        if want not in view_keys:
            raise BadRequestError(
                message=(
                    f"{where}.defaults.default_view must match a view key in "
                    f"this tier (got {dv!r})"
                )
            )


def _canonical_manifest_base(raw: Dict[str, Any]) -> Dict[str, Any]:
    """Require content profile schema v2; empty input becomes a minimal track-scope shell.

    Phase 10 Plan 10-03 (MANIFEST-V2-01): v1 manifests are explicitly REJECTED
    via ``ContentProfileV1RejectedError`` carrying the canonical upgrade-path
    message (docs/app_bundles_v1.md §13.1 + migrate script reference). v2 is
    the only accepted shape — there is no in-memory upgrade shim.
    """
    if not raw:
        return {
            "content_profile_schema_version": SCHEMA_VERSION,
            "scope": "track",
            "track": {
                "entry_types": [],
                "views": [],
                "taxonomy": {"tag_groups": []},
            },
        }
    submitted = raw.get("content_profile_schema_version")
    if submitted != SCHEMA_VERSION:
        # Distinguish "v1 explicit" from "missing/unknown" — UX clarity for the
        # install flow (Plan 10-05). Both still 422.
        if submitted == 1:
            raise ContentProfileV1RejectedError(submitted_version=submitted)
        raise ContentProfileValidationError(
            message=(
                f"manifest must set content_profile_schema_version to "
                f"{SCHEMA_VERSION} (got {submitted!r})"
            ),
            details={"submitted_version": submitted},
        )
    if raw.get("scope") not in VALID_PROFILE_SCOPES:
        raise ContentProfileValidationError(
            message="manifest scope must be 'track', 'app', or 'workspace'"
        )
    return raw


def _manifest_cache_key(
    manifest: Optional[Dict[str, Any]],
    manifest_yaml: Optional[str],
    scope_hint: Optional[str],
) -> Optional[str]:
    """Stable hash key for manifest caching (returns None if unhashable).

    Salted with the field/view-type registry versions so plugin or composite
    registration invalidates cached compiles automatically.
    """
    try:
        raw = (
            manifest_yaml
            if manifest_yaml is not None
            else json.dumps(manifest, sort_keys=True)
        )
        salt = (
            f"{field_type_registry.registry_version()}"
            f":{view_type_registry.registry_version()}"
        )
        parts = f"{raw}|{scope_hint or ''}|{salt}"
        return hashlib.md5(parts.encode(), usedforsecurity=False).hexdigest()
    except (TypeError, ValueError):
        return None


def _parse_manifest_field_composites(
    base: Dict[str, Any],
) -> Tuple[Dict[str, FieldTypeSpec], List[Dict[str, Any]]]:
    """Parse manifest ``field_types[]`` into per-compile composite specs.

    Returns ``(composites, normalized_entries)`` where ``composites`` is the
    map fed to the contextvar resolver, and ``normalized_entries`` is the
    canonical list preserved on the output manifest for round-trip.
    """
    raw = _as_list(base.get("field_types"), where="field_types")
    if not raw:
        return {}, []
    composites: Dict[str, FieldTypeSpec] = {}
    normalized: List[Dict[str, Any]] = []
    for entry in raw:
        ed = _as_dict(entry, where="field_types[]")
        key = str(ed.get("key") or "").strip()
        base_type = str(ed.get("base") or "").strip()
        if not key:
            raise BadRequestError(message="field_types[] requires key")
        if not base_type:
            raise BadRequestError(message=f"field_types[{key!r}] requires base")
        if not field_type_registry.is_known(base_type):
            raise BadRequestError(
                message=(
                    f"field_types[{key!r}].base '{base_type}' is not a known "
                    f"primitive (available: "
                    f"{', '.join(field_type_registry.allowed_keys())})"
                )
            )
        if key in composites:
            raise BadRequestError(message=f"field_types[] duplicate key '{key}'")
        config = _as_dict(ed.get("config"), where=f"field_types[{key!r}].config")
        composites[key] = field_type_registry.make_composite_spec(
            key=key,
            base=base_type,
            config=config,
            label=str(ed.get("label") or key),
            description=str(ed.get("description") or ""),
        )
        normalized.append(
            {
                "key": key,
                "base": base_type,
                "config": config,
                "label": str(ed.get("label") or key),
                "description": str(ed.get("description") or ""),
            }
        )
    return composites, normalized


def _parse_manifest_view_composites(
    base: Dict[str, Any],
) -> Tuple[Dict[str, ViewTypeSpec], List[Dict[str, Any]]]:
    """Parse manifest ``view_types[]`` into per-compile composite specs."""
    raw = _as_list(base.get("view_types"), where="view_types")
    if not raw:
        return {}, []
    composites: Dict[str, ViewTypeSpec] = {}
    normalized: List[Dict[str, Any]] = []
    for entry in raw:
        ed = _as_dict(entry, where="view_types[]")
        key = str(ed.get("key") or "").strip()
        base_type = str(ed.get("base") or "").strip()
        if not key:
            raise BadRequestError(message="view_types[] requires key")
        if not base_type:
            raise BadRequestError(message=f"view_types[{key!r}] requires base")
        if not view_type_registry.is_known(base_type):
            raise BadRequestError(
                message=(
                    f"view_types[{key!r}].base '{base_type}' is not a known "
                    f"primitive (available: "
                    f"{', '.join(view_type_registry.allowed_keys())})"
                )
            )
        if key in composites:
            raise BadRequestError(message=f"view_types[] duplicate key '{key}'")
        config = _as_dict(ed.get("config"), where=f"view_types[{key!r}].config")
        composites[key] = view_type_registry.make_composite_spec(
            key=key,
            base=base_type,
            config=config,
            label=str(ed.get("label") or key),
            description=str(ed.get("description") or ""),
        )
        normalized.append(
            {
                "key": key,
                "base": base_type,
                "config": config,
                "label": str(ed.get("label") or key),
                "description": str(ed.get("description") or ""),
            }
        )
    return composites, normalized


# ---------------------------------------------------------------------------
# Phase 10 Plan 10-03 — manifest v2 operational-layer section parsers.
#
# Pattern follows the v1.1 ``_parse_manifest_field_composites()`` /
# ``_parse_manifest_view_composites()`` precedent above: pure-function
# normalizers that take a raw section payload, validate shape, fill in
# defaults, and return a canonical dict/list. The compiler invokes them
# inside the ``scope == "app"`` branch (with a special-case for
# ``skills`` on track scope per ``app_bundles_v1.md §4.3``).
#
# Each parser raises ``ContentProfileValidationError`` on shape failures,
# carrying section-context info in ``details`` so the install / merge
# surface (Plan 10-05) can render a tailored UX prompt.
# ---------------------------------------------------------------------------


def _parse_manifest_skills(
    raw_skills: Any,
    *,
    is_public_catalog: bool = False,
    where: str = "app.skills",
) -> List[Dict[str, Any]]:
    """Normalize the ``skills[]`` section under ``scope: app`` (or track in v2).

    Per ``app_bundles_v1.md §4.2``. Each skill normalizes to:
      - ``key`` (required), ``name`` (defaults to key)
      - ``kind`` (default ``declarative``; validated ``declarative|custom``)
      - ``description`` (str)
      - ``tools_required`` (list of MCP tool name strings)
      - ``prompt_template`` (required when ``kind == declarative``)
      - ``handler_ref`` (required when ``kind == custom``)
      - ``parameters`` (JSON Schema fragment; pass-through dict)
      - ``outputs`` (list of dict; pass-through)
      - ``private`` (bool default False)
      - ``trust_tier`` (default ``untrusted``)

    Phase 10 Plan 10-04 will wire these specs into Skill Node creation;
    this parser only validates + normalizes the in-memory shape.

    Architectural Decision 6 — Public catalog rejects ``kind: custom``:
    When ``is_public_catalog`` is True, any custom-kind skill raises
    ``ContentProfileValidationError`` at compile time. The merge-time
    mirror check lands in Plan 10-04.
    """
    raw = _as_list(raw_skills, where=where)
    if not raw:
        return []
    if len(raw) > MAX_SKILLS_PER_APP:
        raise ContentProfileValidationError(
            message=(
                f"{where} declares {len(raw)} skills (cap is {MAX_SKILLS_PER_APP})"
            )
        )
    seen_keys: Set[str] = set()
    out: List[Dict[str, Any]] = []
    for idx, entry in enumerate(raw):
        ed = _as_dict(entry, where=f"{where}[{idx}]")
        key = str(ed.get("key") or "").strip()
        if not key:
            raise ContentProfileValidationError(
                message=f"{where}[{idx}].key is required"
            )
        if key in seen_keys:
            raise ContentProfileValidationError(
                message=f"{where} duplicate key {key!r}"
            )
        seen_keys.add(key)
        kind = str(ed.get("kind") or "declarative").strip().lower()
        if kind not in _VALID_SKILL_KINDS:
            raise ContentProfileValidationError(
                message=(
                    f"{where}[{key!r}].kind must be one of "
                    f"{sorted(_VALID_SKILL_KINDS)} (got {kind!r})"
                )
            )
        if is_public_catalog and kind == "custom":
            raise ContentProfileValidationError(
                message=(
                    f"{where}[{key!r}].kind == 'custom' is not permitted in "
                    "the public catalog (App publishers must use declarative "
                    "skills or an MCP uplink). See app_bundles_v1.md §11."
                ),
                details={"skill_key": key, "kind": kind},
            )
        prompt_template = str(ed.get("prompt_template") or "").strip()
        handler_ref = str(ed.get("handler_ref") or "").strip()
        if kind == "declarative" and not prompt_template:
            raise ContentProfileValidationError(
                message=(
                    f"{where}[{key!r}] kind=declarative requires " "prompt_template"
                )
            )
        if kind == "custom" and not handler_ref:
            raise ContentProfileValidationError(
                message=f"{where}[{key!r}] kind=custom requires handler_ref"
            )
        trust_tier = str(ed.get("trust_tier") or "untrusted").strip().lower()
        if trust_tier not in _VALID_TRUST_TIERS:
            raise ContentProfileValidationError(
                message=(
                    f"{where}[{key!r}].trust_tier must be one of "
                    f"{sorted(_VALID_TRUST_TIERS)}"
                )
            )
        spec: Dict[str, Any] = {
            "key": key,
            "name": str(ed.get("name") or key),
            "kind": kind,
            "description": str(ed.get("description") or ""),
            "tools_required": [
                str(t)
                for t in _as_list(
                    ed.get("tools_required"),
                    where=f"{where}[{key!r}].tools_required",
                )
            ],
            "parameters": _as_dict(
                ed.get("parameters"), where=f"{where}[{key!r}].parameters"
            ),
            "outputs": [
                _as_dict(o, where=f"{where}[{key!r}].outputs[]")
                for o in _as_list(ed.get("outputs"), where=f"{where}[{key!r}].outputs")
            ],
            "private": bool(ed.get("private", False)),
            "trust_tier": trust_tier,
        }
        if prompt_template:
            spec["prompt_template"] = prompt_template
        if handler_ref:
            spec["handler_ref"] = handler_ref
        out.append(spec)
    return out


def _parse_manifest_agents(
    raw_agents: Any,
    declared_skill_keys: Set[str],
    *,
    where: str = "app.agents",
) -> List[Dict[str, Any]]:
    """Normalize the ``agents[]`` section under ``scope: app``.

    Per ``app_bundles_v1.md §4.2``. Each agent normalizes to:
      - ``key`` (required), ``name``
      - ``persona_ref`` (str, required) — path to persona file in the bundle
      - ``skills`` (list of skill keys — all must exist in
        ``declared_skill_keys``; raises ContentProfileValidationError if any
        reference is dangling)
      - ``scope`` (``app|workspace``; default ``app``)
      - ``default_schedules`` (list of ``{name, cron, skill, params}``)
      - ``staging`` (``required|optional|disabled``; default ``required``)

    Skill-reference validation = T-10-03-06 mitigation: agents cannot
    reference skills that weren't declared in the same manifest, blocking
    one class of capability-escalation tampering at compile time.
    """
    raw = _as_list(raw_agents, where=where)
    if not raw:
        return []
    if len(raw) > MAX_AGENTS_PER_APP:
        raise ContentProfileValidationError(
            message=(
                f"{where} declares {len(raw)} agents (cap is {MAX_AGENTS_PER_APP})"
            )
        )
    seen_keys: Set[str] = set()
    out: List[Dict[str, Any]] = []
    for idx, entry in enumerate(raw):
        ed = _as_dict(entry, where=f"{where}[{idx}]")
        key = str(ed.get("key") or "").strip()
        if not key:
            raise ContentProfileValidationError(
                message=f"{where}[{idx}].key is required"
            )
        if key in seen_keys:
            raise ContentProfileValidationError(
                message=f"{where} duplicate key {key!r}"
            )
        seen_keys.add(key)
        persona_ref = str(ed.get("persona_ref") or "").strip()
        if not persona_ref:
            raise ContentProfileValidationError(
                message=f"{where}[{key!r}].persona_ref is required"
            )
        scope_val = str(ed.get("scope") or "app").strip().lower()
        if scope_val not in _VALID_AGENT_SCOPES:
            raise ContentProfileValidationError(
                message=(
                    f"{where}[{key!r}].scope must be one of "
                    f"{sorted(_VALID_AGENT_SCOPES)}"
                )
            )
        staging = str(ed.get("staging") or "required").strip().lower()
        if staging not in _VALID_AGENT_STAGING:
            raise ContentProfileValidationError(
                message=(
                    f"{where}[{key!r}].staging must be one of "
                    f"{sorted(_VALID_AGENT_STAGING)}"
                )
            )
        skills_list: List[str] = []
        for sk in _as_list(ed.get("skills"), where=f"{where}[{key!r}].skills"):
            sk_key = str(sk or "").strip()
            if not sk_key:
                continue
            if sk_key not in declared_skill_keys:
                raise ContentProfileValidationError(
                    message=(
                        f"{where}[{key!r}].skills references undeclared skill "
                        f"{sk_key!r} (declared skills: "
                        f"{sorted(declared_skill_keys) or 'none'})"
                    ),
                    details={
                        "agent_key": key,
                        "missing_skill_key": sk_key,
                    },
                )
            skills_list.append(sk_key)
        schedules_out: List[Dict[str, Any]] = []
        for sidx, sched in enumerate(
            _as_list(
                ed.get("default_schedules"),
                where=f"{where}[{key!r}].default_schedules",
            )
        ):
            sd = _as_dict(sched, where=f"{where}[{key!r}].default_schedules[{sidx}]")
            sched_skill = str(sd.get("skill") or "").strip()
            if sched_skill and sched_skill not in declared_skill_keys:
                raise ContentProfileValidationError(
                    message=(
                        f"{where}[{key!r}].default_schedules[{sidx}].skill "
                        f"references undeclared skill {sched_skill!r}"
                    )
                )
            schedules_out.append(
                {
                    "name": str(sd.get("name") or ""),
                    "cron": str(sd.get("cron") or ""),
                    "skill": sched_skill,
                    "params": _as_dict(
                        sd.get("params"),
                        where=(f"{where}[{key!r}].default_schedules[{sidx}].params"),
                    ),
                }
            )
        out.append(
            {
                "key": key,
                "name": str(ed.get("name") or key),
                "persona_ref": persona_ref,
                "skills": skills_list,
                "scope": scope_val,
                "default_schedules": schedules_out,
                "staging": staging,
            }
        )
    return out


# Phase 30 (DR-30-01 + DR-30-02) — tools[] + hooks[] manifest sections.
MAX_TOOLS_PER_APP = 64
MAX_HOOKS_PER_APP = 128
_VALID_HOOK_POINTS = frozenset(
    {
        "entry.transform",
        "entry.public_share",
        "entry.precompute",
        "entry.create",  # DR-32-01
        "entry.validate",  # pre-write bundle validation
        "entry.update",  # DR-32-01
        "connector.dedup",
        "connector.auto_link",
    }
)
_VALID_HOOK_MODES = frozenset({"declarative", "tool"})

# F0 — optional app.operations[] operational contract (advisory enforcement).
_VALID_OPERATION_KINDS = frozenset({"read", "propose", "execute", "destructive"})
MAX_OPERATIONS_PER_APP = 128


def _parse_manifest_operations(
    raw_ops: Any,
    *,
    where: str = "app.operations",
) -> List[Dict[str, Any]]:
    """Normalize optional ``app.operations[]`` (F0 extension contract).

    Shape is validated; policy enforcement remains advisory until F2.
    """
    raw = _as_list(raw_ops, where=where)
    if not raw:
        return []
    if len(raw) > MAX_OPERATIONS_PER_APP:
        raise ContentProfileValidationError(
            message=(
                f"{where} declares {len(raw)} operations "
                f"(cap is {MAX_OPERATIONS_PER_APP})"
            )
        )
    seen: Set[str] = set()
    out: List[Dict[str, Any]] = []
    for idx, entry in enumerate(raw):
        ed = _as_dict(entry, where=f"{where}[{idx}]")
        key = str(ed.get("key") or "").strip()
        if not key:
            raise ContentProfileValidationError(
                message=f"{where}[{idx}].key is required"
            )
        if key in seen:
            raise ContentProfileValidationError(
                message=f"{where} duplicate key {key!r}"
            )
        seen.add(key)
        kind = str(ed.get("kind") or "execute").strip().lower()
        if kind not in _VALID_OPERATION_KINDS:
            raise ContentProfileValidationError(
                message=(
                    f"{where}[{key!r}].kind must be one of "
                    f"{sorted(_VALID_OPERATION_KINDS)}"
                )
            )
        out.append(
            {
                "key": key,
                "kind": kind,
                "name": str(ed.get("name") or key),
                "description": str(ed.get("description") or ""),
                "policy_action": str(ed.get("policy_action") or "").strip() or None,
                "capability": str(ed.get("capability") or "").strip() or None,
                "staging_level": str(ed.get("staging_level") or "").strip() or None,
                "idempotency_key": str(ed.get("idempotency_key") or "").strip()
                or None,
                "timeout_seconds": ed.get("timeout_seconds"),
            }
        )
    return out


def _parse_manifest_tools(
    raw_tools: Any,
    *,
    bundle_slug: str,
    trust_tier: str,
    where: str = "app.tools",
) -> List[Dict[str, Any]]:
    """Normalize the ``tools[]`` section (DR-30-01).

    Trust gate: declaring any tool with trust_tier != trusted/audited
    raises ContentProfileValidationError.
    """
    raw = _as_list(raw_tools, where=where)
    if not raw:
        return []
    if (trust_tier or "").lower() not in {"trusted", "audited"}:
        raise ContentProfileValidationError(
            message=(
                f"{where} declares {len(raw)} tools but bundle "
                f"trust_tier={trust_tier!r}. Tools require trust_tier=trusted."
            ),
            details={"bundle_slug": bundle_slug, "trust_tier": trust_tier},
        )
    if len(raw) > MAX_TOOLS_PER_APP:
        raise ContentProfileValidationError(
            message=f"{where} declares {len(raw)} tools (cap is {MAX_TOOLS_PER_APP})"
        )
    seen_keys: Set[str] = set()
    out: List[Dict[str, Any]] = []
    for idx, entry in enumerate(raw):
        ed = _as_dict(entry, where=f"{where}[{idx}]")
        key = str(ed.get("key") or "").strip()
        if not key:
            raise ContentProfileValidationError(
                message=f"{where}[{idx}].key is required"
            )
        if key in seen_keys:
            raise ContentProfileValidationError(
                message=f"{where} duplicate key {key!r}"
            )
        seen_keys.add(key)
        handler_ref = str(ed.get("handler_ref") or "").strip()
        if not handler_ref or ":" not in handler_ref:
            raise ContentProfileValidationError(
                message=f"{where}[{key!r}].handler_ref required as 'module:callable'"
            )
        spec: Dict[str, Any] = {
            "key": key,
            "name": str(ed.get("name") or key),
            "description": str(ed.get("description") or ""),
            "handler_ref": handler_ref,
            "parameters_schema": _as_dict(
                ed.get("parameters_schema") or {},
                where=f"{where}[{key!r}].parameters_schema",
            ),
            "output_schema": _as_dict(
                ed.get("output_schema") or {},
                where=f"{where}[{key!r}].output_schema",
            ),
            "privileged": bool(ed.get("privileged", False)),
            "side_effects": str(ed.get("side_effects") or "read_only"),
            # Default True: existing tools stay on the agent surface.
            # Bundles set ``agent_callable: false`` for human-only action-bar
            # tools (finalize / generate payslips) so chat cannot invoke them
            # even via the generic ``integral_call_workspace_tool`` path.
            "agent_callable": bool(ed.get("agent_callable", True)),
        }
        out.append(spec)
    return out


def _parse_manifest_hooks(
    raw_hooks: Any,
    *,
    declared_tool_keys: Set[str],
    where: str = "app.hooks",
) -> List[Dict[str, Any]]:
    """Normalize the ``hooks[]`` section (DR-30-02).

    Validates: point is in frozen catalog; mode is declarative or tool;
    declarative blocks present when mode=declarative; tool ref resolves
    to a declared tools[].key when mode=tool.
    """
    raw = _as_list(raw_hooks, where=where)
    if not raw:
        return []
    if len(raw) > MAX_HOOKS_PER_APP:
        raise ContentProfileValidationError(
            message=f"{where} declares {len(raw)} hooks (cap is {MAX_HOOKS_PER_APP})"
        )
    seen_keys: Set[str] = set()
    out: List[Dict[str, Any]] = []
    for idx, entry in enumerate(raw):
        ed = _as_dict(entry, where=f"{where}[{idx}]")
        key = str(ed.get("key") or "").strip()
        if not key:
            raise ContentProfileValidationError(
                message=f"{where}[{idx}].key is required"
            )
        if key in seen_keys:
            raise ContentProfileValidationError(
                message=f"{where} duplicate key {key!r}"
            )
        seen_keys.add(key)
        point = str(ed.get("point") or "").strip()
        if point not in _VALID_HOOK_POINTS:
            raise ContentProfileValidationError(
                message=(
                    f"{where}[{key!r}].point {point!r} not in frozen catalog "
                    f"{sorted(_VALID_HOOK_POINTS)}"
                )
            )
        mode = str(ed.get("mode") or "declarative").strip().lower()
        if mode not in _VALID_HOOK_MODES:
            raise ContentProfileValidationError(
                message=(
                    f"{where}[{key!r}].mode must be one of "
                    f"{sorted(_VALID_HOOK_MODES)}"
                )
            )
        binding: Dict[str, Any] = {
            "point": point,
            "key": key,
            "match": _as_dict(ed.get("match") or {}, where=f"{where}[{key!r}].match"),
            "mode": mode,
            "privileged": bool(ed.get("privileged", False)),
        }
        if mode == "declarative":
            decl = ed.get("declarative")
            if decl is None:
                raise ContentProfileValidationError(
                    message=f"{where}[{key!r}].declarative required when mode=declarative"
                )
            binding["declarative"] = _as_dict(
                decl, where=f"{where}[{key!r}].declarative"
            )
        else:  # mode == tool
            tool_ref = str(ed.get("tool") or "").strip()
            if not tool_ref:
                raise ContentProfileValidationError(
                    message=f"{where}[{key!r}].tool required when mode=tool"
                )
            if tool_ref not in declared_tool_keys:
                raise ContentProfileValidationError(
                    message=(
                        f"{where}[{key!r}].tool {tool_ref!r} not in declared "
                        f"tools[] (declared: {sorted(declared_tool_keys)})"
                    )
                )
            binding["tool"] = tool_ref
            binding["tool_input"] = _as_dict(
                ed.get("tool_input") or {},
                where=f"{where}[{key!r}].tool_input",
            )
        out.append(binding)
    return out


def _parse_manifest_settings_schema(
    raw_schema: Any,
    *,
    where: str = "app.settings_schema",
) -> Dict[str, Any]:
    """Normalize the ``settings_schema`` section under ``scope: app``.

    Per ``app_bundles_v1.md §4.2``. This is a JSON Schema fragment (Draft
    2020-12-compatible subset — we don't require full Draft 2020-12 here,
    only that ``type == "object"``, ``properties`` is a dict, and each
    property's optional ``ui:widget`` is in the allowed widget set).

    Author-side validation only — runtime form rendering + settings
    persistence is Plan 10-05.

    T-10-03-05 (accepted): permissive JSON Schema validation is a v1
    design limit. v2.1 may layer a stricter linter.
    """
    if raw_schema is None or raw_schema == {}:
        return {}
    schema = _as_dict(raw_schema, where=where)
    schema_type = str(schema.get("type") or "object").strip().lower()
    if schema_type != "object":
        raise ContentProfileValidationError(
            message=(
                f"{where}.type must be 'object' (got {schema_type!r}; only "
                "object-type schemas are supported for App settings)"
            )
        )
    props = _as_dict(schema.get("properties"), where=f"{where}.properties")
    if len(props) > MAX_SETTINGS_SCHEMA_PROPERTIES:
        raise ContentProfileValidationError(
            message=(
                f"{where}.properties declares {len(props)} keys (cap is "
                f"{MAX_SETTINGS_SCHEMA_PROPERTIES})"
            )
        )
    for pname, pspec in props.items():
        pd = _as_dict(pspec, where=f"{where}.properties[{pname!r}]")
        widget = pd.get("ui:widget")
        if widget is not None:
            widget_val = str(widget).strip().lower()
            if widget_val not in _VALID_SETTINGS_WIDGETS:
                raise ContentProfileValidationError(
                    message=(
                        f"{where}.properties[{pname!r}].ui:widget must be one "
                        f"of {sorted(_VALID_SETTINGS_WIDGETS)} (got "
                        f"{widget_val!r})"
                    )
                )
    # Round-trip the raw schema dict — UI form renderer (Plan 10-05) consumes
    # it as-is and supports the full JSON Schema vocabulary.
    return schema


def _parse_manifest_seeds(
    raw_seeds: Any,
    *,
    where: str = "app.seeds",
) -> List[Dict[str, Any]]:
    """Normalize the ``seeds[]`` section under ``scope: app``.

    Per ``app_bundles_v1.md §4.2``. Each seed entry normalizes to:
      - ``track`` (str, required) — track key in this App's manifest
      - ``entries`` (list of
        ``{title?, body?, tags?, custom_fields?, id?, entry_type?}``)

    Per Plan 10-05 contract: when ``id`` is missing the lifecycle service
    generates a deterministic seed.id at plant time (we don't fabricate
    here).

    ``entry_type`` (optional) names which of the target track's entry types
    this seed row belongs to — required for any track with more than one
    entry type, since ``resolve_seed_entry_type_id`` (app/services/
    entry_type_resolver.py) already reads this key and otherwise silently
    falls back to the track's ``default_entry_type`` for every row. Without
    it here, that fallback previously mistyped every non-default seed row
    on a multi-entry-type track — a row for the second entry type got
    silently planted as an instance of the default one instead, because
    this key was being dropped during compile, before
    ``resolve_seed_entry_type_id`` ever saw it.
    """
    raw = _as_list(raw_seeds, where=where)
    if not raw:
        return []
    if len(raw) > MAX_SEEDS_PER_APP:
        raise ContentProfileValidationError(
            message=(
                f"{where} declares {len(raw)} seed groups (cap is "
                f"{MAX_SEEDS_PER_APP})"
            )
        )
    out: List[Dict[str, Any]] = []
    for idx, entry in enumerate(raw):
        ed = _as_dict(entry, where=f"{where}[{idx}]")
        track_key = str(ed.get("track") or "").strip()
        if not track_key:
            raise ContentProfileValidationError(
                message=f"{where}[{idx}].track is required"
            )
        entries_list: List[Dict[str, Any]] = []
        for eidx, ent in enumerate(
            _as_list(ed.get("entries"), where=f"{where}[{idx}].entries")
        ):
            entd = _as_dict(ent, where=f"{where}[{idx}].entries[{eidx}]")
            entry_spec: Dict[str, Any] = {
                "title": str(entd.get("title") or ""),
                "body": str(entd.get("body") or ""),
                "tags": [
                    str(t)
                    for t in _as_list(
                        entd.get("tags"),
                        where=f"{where}[{idx}].entries[{eidx}].tags",
                    )
                ],
                "custom_fields": _as_dict(
                    entd.get("custom_fields"),
                    where=f"{where}[{idx}].entries[{eidx}].custom_fields",
                ),
            }
            seed_id = entd.get("id")
            if seed_id is not None:
                entry_spec["id"] = str(seed_id)
            entry_type = entd.get("entry_type")
            if entry_type is not None and str(entry_type).strip():
                entry_spec["entry_type"] = str(entry_type).strip()
            entries_list.append(entry_spec)
        out.append({"track": track_key, "entries": entries_list})
    return out


# ADR-006 (I-PC-01) — unstaged write targets.
MAX_UNSTAGED_TRACKS_PER_APP = 4


def _parse_manifest_unstaged_tracks(
    raw: Any,
    *,
    declared_track_keys: Set[str],
    bundle_slug: str,
    trust_tier: str,
    where: str = "app.unstaged_tracks",
) -> List[str]:
    """Normalize ``unstaged_tracks[]`` — the App's exemption from staging.

    A track key listed here accepts agent writes without minting a
    StagedChange. That is the substrate's only unstaged write path, so the
    parse is deliberately unforgiving:

    - trust gate, same bar as ``tools[]`` — trusted or audited only;
    - the key must name a track this manifest declares, so a typo is a
      validation error rather than a silently ineffective exemption;
    - a hard cap, because a bundle exempting its whole surface has not
      declared an exception, it has opted out.

    Declaring the exemption is necessary but never sufficient. The runtime
    gate (``app.agentive.unstaged_targets``) additionally requires the App
    to be installed in the acting principal's own personal workspace, with
    that principal as its owner. See ADR-006.
    """
    keys = _as_list(raw, where=where)
    if not keys:
        return []
    if (trust_tier or "").lower() not in {"trusted", "audited"}:
        raise ContentProfileValidationError(
            message=(
                f"{where} exempts {len(keys)} track(s) from staging but bundle "
                f"trust_tier={trust_tier!r}. Unstaged writes require "
                f"trust_tier=trusted."
            ),
            details={"bundle_slug": bundle_slug, "trust_tier": trust_tier},
        )
    if len(keys) > MAX_UNSTAGED_TRACKS_PER_APP:
        raise ContentProfileValidationError(
            message=(
                f"{where} exempts {len(keys)} tracks (cap is "
                f"{MAX_UNSTAGED_TRACKS_PER_APP})"
            )
        )
    out: List[str] = []
    for idx, raw_key in enumerate(keys):
        key = str(raw_key or "").strip()
        if not key:
            raise ContentProfileValidationError(message=f"{where}[{idx}] is empty")
        if key not in declared_track_keys:
            raise ContentProfileValidationError(
                message=(
                    f"{where}[{idx}] names {key!r}, which is not a track this "
                    f"manifest declares (declared: "
                    f"{sorted(declared_track_keys) or 'none'})"
                ),
                details={"bundle_slug": bundle_slug, "unknown_track_key": key},
            )
        if key in out:
            raise ContentProfileValidationError(
                message=f"{where} duplicate key {key!r}"
            )
        out.append(key)
    return out


def _parse_manifest_permissions(
    raw_permissions: Any,
    *,
    where: str = "app.permissions",
) -> Dict[str, Any]:
    """Normalize the ``permissions`` block under ``scope: app``.

    Per ``app_bundles_v1.md §4.2``. Normalizes to:
      - ``install_requires_workspace_role`` (default ``member``;
        validated ``admin|member|guest``)
      - ``default_app_role`` (default ``member``;
        validated ``owner|editor|commenter|viewer|member``)

    Plan 10-05 enforces these at install time; this is a normalization
    parser only.
    """
    perms = _as_dict(raw_permissions, where=where)
    install_role = (
        str(perms.get("install_requires_workspace_role") or "member").strip().lower()
    )
    if install_role not in _VALID_WORKSPACE_ROLES:
        raise ContentProfileValidationError(
            message=(
                f"{where}.install_requires_workspace_role must be one of "
                f"{sorted(_VALID_WORKSPACE_ROLES)} (got {install_role!r})"
            )
        )
    default_role = str(perms.get("default_app_role") or "member").strip().lower()
    if default_role not in _VALID_APP_ROLES:
        raise ContentProfileValidationError(
            message=(
                f"{where}.default_app_role must be one of "
                f"{sorted(_VALID_APP_ROLES)} (got {default_role!r})"
            )
        )
    return {
        "install_requires_workspace_role": install_role,
        "default_app_role": default_role,
    }


def _parse_manifest_requires_apps(
    raw_deps: Any,
    *,
    where: str = "app.requires_apps",
) -> List[Dict[str, Any]]:
    """Normalize the ``requires_apps[]`` (App-to-App dependency) section.

    Per ``app_bundles_v1.md §4.2``. Each dep normalizes to:
      - ``key`` (str, required) — App package name to depend on
      - ``min_version`` (str, default ``"0.0.0"``)
      - ``optional`` (bool, default False) — soft vs hard dependency
      - ``reason`` (str, free-form explanation for the install UX)

    Actual install-time resolution (hard-dep blocks install, soft-dep
    permits with degraded references) is Plan 10-05.
    """
    raw = _as_list(raw_deps, where=where)
    if not raw:
        return []
    if len(raw) > MAX_REQUIRES_APPS_PER_APP:
        raise ContentProfileValidationError(
            message=(
                f"{where} declares {len(raw)} dependencies (cap is "
                f"{MAX_REQUIRES_APPS_PER_APP})"
            )
        )
    seen_keys: Set[str] = set()
    out: List[Dict[str, Any]] = []
    for idx, entry in enumerate(raw):
        ed = _as_dict(entry, where=f"{where}[{idx}]")
        key = str(ed.get("key") or "").strip()
        if not key:
            raise ContentProfileValidationError(
                message=f"{where}[{idx}].key is required"
            )
        if key in seen_keys:
            raise ContentProfileValidationError(
                message=f"{where} duplicate key {key!r}"
            )
        seen_keys.add(key)
        out.append(
            {
                "key": key,
                "min_version": str(ed.get("min_version") or "0.0.0"),
                "optional": bool(ed.get("optional", False)),
                "reason": str(ed.get("reason") or ""),
            }
        )
    return out


def _normalize_ui_complements(
    raw_complements: Any,
    *,
    where: str = "ui_complements",
) -> List[Dict[str, Any]]:
    """Normalize declarative UI Complement Recipe declarations.

    This first compiler boundary preserves declarations for catalog and
    introspection consumers. A later expansion pass may translate a recipe
    into ordinary ``views``/``related_views``/``create_wizard`` data, but the
    existing Region System and wizard compiler remain the execution
    authorities.
    """
    raw = _as_list(raw_complements, where=where)
    if len(raw) > MAX_UI_COMPLEMENTS_PER_PROFILE:
        raise ContentProfileValidationError(
            message=(
                f"{where} declares {len(raw)} recipes (cap is "
                f"{MAX_UI_COMPLEMENTS_PER_PROFILE})"
            )
        )
    seen: Set[Tuple[str, str, str]] = set()
    out: List[Dict[str, Any]] = []
    for idx, item in enumerate(raw):
        d = _as_dict(item, where=f"{where}[{idx}]")
        recipe_id = str(d.get("id") or "").strip()
        if not recipe_id:
            raise ContentProfileValidationError(
                message=f"{where}[{idx}].id is required"
            )
        version = str(d.get("version") or ">=0.0.0").strip()
        track = str(d.get("track") or "").strip()
        entry_type = str(d.get("entry_type") or "").strip()
        if not track and not entry_type:
            raise ContentProfileValidationError(
                message=f"{where}[{idx}] must target a track or entry_type"
            )
        target = (recipe_id, track, entry_type)
        if target in seen:
            raise ContentProfileValidationError(
                message=f"{where} duplicate recipe target {target!r}"
            )
        seen.add(target)
        out.append(
            {
                "id": recipe_id,
                "version": version,
                "track": track,
                "entry_type": entry_type,
                "config": _as_dict(d.get("config"), where=f"{where}[{idx}].config"),
            }
        )
    return out


def _expand_ui_complement_recipes(base: Dict[str, Any]) -> Dict[str, Any]:
    """Expand built-in recipes into existing manifest primitives.

    The expansion is structural and domain-neutral: it only composes
    caller-supplied view keys and wizard configuration. The resulting data
    still passes through the ordinary Region System and wizard validators.
    """
    declarations = _normalize_ui_complements(base.get("ui_complements"))
    if not declarations:
        return base

    tracks: List[Dict[str, Any]] = []
    track = base.get("track")
    if isinstance(track, dict):
        tracks.append(track)
    app = base.get("app")
    if isinstance(app, dict):
        for collection_key in ("tracks", "track_templates"):
            collection = app.get(collection_key)
            if isinstance(collection, list):
                tracks.extend(item for item in collection if isinstance(item, dict))
    track_by_key = {
        str(item.get("key") or ""): item
        for item in tracks
        if str(item.get("key") or "")
    }

    for declaration in declarations:
        recipe_id = declaration["id"]
        track_key = declaration["track"]
        entry_key = declaration["entry_type"]
        target = track_by_key.get(track_key) if track_key else None
        if target is None:
            raise ContentProfileValidationError(
                message=f"ui_complements target track {track_key!r} was not found"
            )
        entry_types = target.setdefault("entry_types", [])
        entry = next(
            (
                item
                for item in entry_types
                if isinstance(item, dict) and str(item.get("key") or "") == entry_key
            ),
            None,
        )
        if entry is None:
            raise ContentProfileValidationError(
                message=(
                    f"ui_complements target entry_type {entry_key!r} was not "
                    f"found on track {track_key!r}"
                )
            )
        config = declaration["config"]
        if recipe_id == "operational-record":
            regions: List[Dict[str, Any]] = []
            for region_key in (
                "summary_view",
                "readiness_view",
                "actions_view",
                "documents_view",
                "history_view",
            ):
                view_key = config.get(region_key)
                if view_key:
                    regions.append(
                        {"key": region_key, "kind": "view", "view": view_key}
                    )
            for index, view_key in enumerate(config.get("primary_views") or []):
                regions.append(
                    {"key": f"primary_{index}", "kind": "view", "view": view_key}
                )
            if not regions:
                raise ContentProfileValidationError(
                    message=(
                        "ui_complements operational-record requires at least "
                        "one configured view"
                    )
                )
            generated_key = f"{entry_key}_operational_record"
            views = target.setdefault("views", [])
            if any(
                isinstance(view, dict) and view.get("key") == generated_key
                for view in views
            ):
                raise ContentProfileValidationError(
                    message=f"ui_complements generated duplicate view key {generated_key!r}"
                )
            views.append(
                {
                    "key": generated_key,
                    "name": str(config.get("title") or "Operational Record"),
                    "view_type": "layout_container",
                    "entry_type_keys": [entry_key],
                    "config": {
                        "mode": str(config.get("mode") or "stack"),
                        "regions": regions,
                    },
                }
            )
            # Put the operational surface first. Related views are rendered
            # in declaration order; appending this generated view made the
            # user encounter the register/filings before the readiness and
            # action controls that explain what to do next.
            entry.setdefault("related_views", []).insert(
                0, {"view": generated_key, "position": "primary", "bind": {}}
            )
        elif recipe_id == "guided-workflow":
            wizard = config.get("create_wizard") or config.get("wizard")
            if wizard is None and isinstance(config.get("steps"), list):
                wizard = {"steps": config["steps"]}
                for key in ("on_create_tool", "on_create_ids_param"):
                    if config.get(key) is not None:
                        wizard[key] = config[key]
            if not isinstance(wizard, dict):
                raise ContentProfileValidationError(
                    message="ui_complements guided-workflow requires config.steps"
                )
            if entry.get("create_wizard") is not None:
                raise ContentProfileValidationError(
                    message=(
                        "ui_complements guided-workflow cannot replace an "
                        f"existing create_wizard on {entry_key!r}"
                    )
                )
            entry["create_wizard"] = wizard
        else:
            raise ContentProfileValidationError(
                message=f"unknown UI Complement Recipe {recipe_id!r}"
            )
    return base


_manifest_compile_cache: Dict[str, Dict[str, Any]] = {}
_MANIFEST_CACHE_MAX = 256


def compile_canonical_manifest(
    *,
    manifest: Optional[Dict[str, Any]] = None,
    manifest_yaml: Optional[str] = None,
    scope_hint: Optional[str] = None,
    is_public_catalog: bool = False,
) -> Dict[str, Any]:
    """Compile user-supplied manifest YAML/JSON into canonical v2 schema.

    Input must use ``content_profile_schema_version: 2`` and ``scope`` of
    ``track`` or ``app``. An empty object is treated as a minimal empty
    track-scope manifest. v1 inputs raise ``ContentProfileV1RejectedError``
    carrying the upgrade-path message (Phase 10 Plan 10-03 / MANIFEST-V2-01).

    v1.1 additions (preserved in v2): optional top-level ``field_types[]``
    and ``view_types[]`` declare manifest-scoped composite types resolved
    during this compile only.

    v2 additions (App Bundles operational layer, app_bundles_v1.md §4.2):
    optional ``app.skills``, ``app.agents``, ``app.settings_schema``,
    ``app.seeds``, ``app.permissions``, ``app.requires_apps`` sections.
    Track scope also accepts an optional ``track.skills`` section per
    app_bundles_v1.md §4.3.

    Architectural Decision 6 — Public catalog rejects ``kind: custom``
    skills. Set ``is_public_catalog=True`` (only at the public-catalog
    submission boundary, e.g. Phase 13 PKG-01 endpoint) and the skill
    parser raises on any custom-kind skill. Default ``False`` preserves
    all existing internal call sites.
    """
    if manifest is None and not manifest_yaml:
        return {}

    # Cache key salted with the public-catalog flag so two compiles of the
    # same manifest with different flag values don't collide.
    ck_base = _manifest_cache_key(manifest, manifest_yaml, scope_hint)
    ck = (ck_base + f":pc={int(bool(is_public_catalog))}") if ck_base else None
    if ck is not None and ck in _manifest_compile_cache:
        return _manifest_compile_cache[ck]

    raw: Dict[str, Any]
    if manifest_yaml is not None:
        if yaml is None:
            raise BadRequestError(
                message="PyYAML is required for manifest_yaml support"
            )
        loaded = yaml.safe_load(manifest_yaml)
        if loaded is None:
            loaded = {}
        if not isinstance(loaded, dict):
            raise BadRequestError(message="manifest_yaml must deserialize to an object")
        raw = loaded
    else:
        raw = _as_dict(manifest, where="manifest")

    base = _expand_ui_complement_recipes(_canonical_manifest_base(raw))
    scope = str(scope_hint or base.get("scope") or "").strip().lower()
    if scope not in VALID_PROFILE_SCOPES:
        raise BadRequestError(
            message="manifest scope must be 'track', 'app', or 'workspace'"
        )

    field_composites, normalized_field_composites = _parse_manifest_field_composites(
        base
    )
    view_composites, normalized_view_composites = _parse_manifest_view_composites(base)

    field_token = _active_field_composites.set(field_composites)
    view_token = _active_view_composites.set(view_composites)
    try:
        out: Dict[str, Any] = {
            "content_profile_schema_version": SCHEMA_VERSION,
            "scope": scope,
            "package": _normalize_package_meta(
                _as_dict(base.get("package"), where="package")
            ),
            "migrations": _as_list(base.get("migrations"), where="migrations"),
        }
        if normalized_field_composites:
            out["field_types"] = normalized_field_composites
        if normalized_view_composites:
            out["view_types"] = normalized_view_composites
        ui_complements = _normalize_ui_complements(base.get("ui_complements"))
        if ui_complements:
            out["ui_complements"] = ui_complements
        # Plugins block (escape hatch) — preserved opaque for now; discovery
        # validates installation at startup, not at compile.
        plugins_in = _as_list(base.get("plugins"), where="plugins")
        if plugins_in:
            out["plugins"] = [_as_dict(p, where="plugins[]") for p in plugins_in]
        for mig in out["migrations"]:
            md = _as_dict(mig, where="migrations[]")
            if not md.get("from_version") or not md.get("to_version"):
                raise BadRequestError(
                    message="migrations[] requires from_version and to_version"
                )

        if scope == "track":
            track = _as_dict(base.get("track"), where="track")
            # Phase 10 Plan 10-03 — Track scope optionally accepts skills
            # per app_bundles_v1.md §4.3. No agents/settings/seeds in track
            # scope (those are App-only). Track-scope skill key set is
            # empty since agents only live in app scope, so the
            # is_public_catalog gate still applies.
            track_skills = _parse_manifest_skills(
                track.get("skills"),
                is_public_catalog=is_public_catalog,
                where="track.skills",
            )
            out["track"] = _ensure_feed_view_fallback(
                {
                    "entry_types": [
                        _normalize_entry_type_spec(
                            _as_dict(e, where="track.entry_types[]")
                        )
                        for e in _as_list(
                            track.get("entry_types"), where="track.entry_types"
                        )
                    ],
                    "views": [
                        _normalize_view_spec(_as_dict(v, where="track.views[]"))
                        for v in _as_list(track.get("views"), where="track.views")
                    ],
                    "taxonomy": _normalize_taxonomy(
                        _as_dict(track.get("taxonomy"), where="track.taxonomy")
                    ),
                    "defaults": _as_dict(track.get("defaults"), where="track.defaults"),
                    "skills": track_skills,
                },
                where="track",
                suppress=bool(track.get("suppress_feed_fallback", False)),
            )
            _assert_unique_keys(
                out["track"].get("entry_types", []),
                where="track.entry_types",
            )
            _assert_unique_keys(
                out["track"].get("views", []),
                where="track.views",
            )
            _validate_entry_type_relation_targets(
                _as_list(out["track"].get("entry_types"), where="track.entry_types"),
                where="track",
            )
            track_tier = _as_dict(out["track"], where="track")
            _validate_track_tier_defaults(track_tier, where="track")
            _validate_calendar_view_mappings(track_tier, where="track")
            _validate_related_view_scope_placement(track_tier, where="track")
        elif scope == "workspace":
            # Phase D2 — workspace-scope manifests bundle ≥1 app
            # sub-manifest (inline ``profile`` or library ``profile_ref``)
            # plus optional ``cross_app_relations`` spanning apps in the
            # bundle. This branch runs the pre-flight gate that D3's
            # workspace_init service executes BEFORE any DB writes — every
            # validation raises before a return value emerges.
            #
            # ``agent_roles``, ``workspace_settings``, ``seeds`` are passed
            # through unchanged here; D3 consumes them at provisioning time.
            ws = _as_dict(base.get("workspace"), where="workspace")
            apps_in = _as_list(ws.get("apps"), where="workspace.apps")
            if len(apps_in) < 1:
                raise ContentProfileValidationError(
                    message="workspace.apps must be a non-empty list"
                )
            app_slugs: set[str] = set()
            compiled_apps: List[Dict[str, Any]] = []
            for app_entry in apps_in:
                ae = _as_dict(app_entry, where="workspace.apps[]")
                slug = str(ae.get("slug") or "").strip()
                if not slug:
                    raise ContentProfileValidationError(
                        message="workspace.apps[].slug is required"
                    )
                if slug in app_slugs:
                    raise ContentProfileValidationError(
                        message=f"duplicate workspace.apps[].slug: {slug}"
                    )
                app_slugs.add(slug)
                inline = ae.get("profile")
                profile_ref = ae.get("profile_ref")
                if not inline and not profile_ref:
                    raise ContentProfileValidationError(
                        message=(
                            f"workspace.apps[{slug}]: one of profile_ref or "
                            "profile required"
                        )
                    )
                if inline and profile_ref:
                    raise ContentProfileValidationError(
                        message=(
                            f"workspace.apps[{slug}]: provide only one of "
                            "profile_ref or profile"
                        )
                    )
                if (
                    inline
                    and str(
                        _as_dict(inline, where=f"workspace.apps[{slug}].profile").get(
                            "scope"
                        )
                        or ""
                    )
                    .strip()
                    .lower()
                    == "workspace"
                ):
                    raise ContentProfileValidationError(
                        message=(
                            f"workspace.apps[{slug}]: nested workspace-scope "
                            "manifests forbidden"
                        )
                    )
                compiled_apps.append({**ae, "slug": slug})

            relations_in = _as_list(
                ws.get("cross_app_relations"), where="workspace.cross_app_relations"
            )
            compiled_relations: List[Dict[str, Any]] = []
            for rel in relations_in:
                rd = _as_dict(rel, where="workspace.cross_app_relations[]")
                src = _as_dict(
                    rd.get("source"), where="workspace.cross_app_relations[].source"
                ).get("app")
                tgt = _as_dict(
                    rd.get("target"), where="workspace.cross_app_relations[].target"
                ).get("app")
                if src not in app_slugs:
                    raise ContentProfileValidationError(
                        message=(
                            f"cross_app_relations source.app '{src}' not in "
                            "workspace.apps[]"
                        )
                    )
                if tgt not in app_slugs:
                    raise ContentProfileValidationError(
                        message=(
                            f"cross_app_relations target.app '{tgt}' not in "
                            "workspace.apps[]"
                        )
                    )
                compiled_relations.append(rd)

            # Pass through D3-consumed pre-provision sections unchanged.
            out["workspace"] = {
                **ws,
                "apps": compiled_apps,
                "cross_app_relations": compiled_relations,
            }
        else:
            app_node = _as_dict(base.get("app"), where="app")
            tracks_out: List[Dict[str, Any]] = []
            for t in _as_list(app_node.get("tracks"), where="app.tracks"):
                td = _as_dict(t, where="app.tracks[]")
                tname = str(td.get("name") or "").strip()
                if not tname:
                    raise BadRequestError(message="app_node.tracks[] requires name")
                if "provision_on_create" in td and not isinstance(
                    td.get("provision_on_create"), bool
                ):
                    raise BadRequestError(
                        message=(
                            f"app_node.tracks[{tname!r}].provision_on_create "
                            "must be boolean"
                        )
                    )
                public_share = _normalize_public_share_spec(
                    td.get("public_share"),
                    where=f"app.tracks[{tname!r}].public_share",
                )
                tracks_out.append(
                    _ensure_feed_view_fallback(
                        {
                            "key": str(td.get("key") or _slug(tname)),
                            "name": tname,
                            "description": str(td.get("description") or "").strip(),
                            "provision_on_create": bool(
                                td.get("provision_on_create", True)
                            ),
                            # Declared intent only — never auto-minted. The
                            # owner's explicit enable mints the token.
                            **({"public_share": public_share} if public_share else {}),
                            "entry_types": [
                                _normalize_entry_type_spec(
                                    _as_dict(
                                        e,
                                        where=f"app track '{tname}' entry_types[]",
                                    )
                                )
                                for e in _as_list(
                                    td.get("entry_types"), where="entry_types"
                                )
                            ],
                            "views": [
                                _normalize_view_spec(
                                    _as_dict(v, where=f"app track '{tname}' views[]")
                                )
                                for v in _as_list(td.get("views"), where="views")
                            ],
                            "taxonomy": _normalize_taxonomy(
                                _as_dict(td.get("taxonomy"), where="taxonomy")
                            ),
                            "defaults": _as_dict(td.get("defaults"), where="defaults"),
                        },
                        where=f"app.tracks[{_slug(tname)}]",
                        suppress=bool(td.get("suppress_feed_fallback", False)),
                    )
                )
            app_defaults = _as_dict(app_node.get("defaults"), where="app.defaults")
            if "provision_prescribed_tracks" in app_defaults and not isinstance(
                app_defaults.get("provision_prescribed_tracks"), (bool, int)
            ):
                raise BadRequestError(
                    message=(
                        "app_node.defaults.provision_prescribed_tracks must be boolean"
                    )
                )
            if tracks_out and "provision_prescribed_tracks" not in app_defaults:
                app_defaults = {
                    **app_defaults,
                    "provision_prescribed_tracks": True,
                }
            # Phase 3.1 ANC-04: ``app.track_templates[]`` is a registry of
            # named track templates referenced by ``relation.target_track_template``
            # on entry-type relation fields with ``target: track`` +
            # ``auto_provision: true``. Templates are NEVER auto-provisioned on
            # App create — only on entry create, lazily, by the
            # ``materialize_anchor_track`` hook. Track-template entries do not
            # carry ``provision_on_create`` (always false by definition).
            track_templates_out: List[Dict[str, Any]] = [
                _normalize_track_template_spec(t, where="app.track_templates[]")
                for t in _as_list(
                    app_node.get("track_templates"), where="app.track_templates"
                )
            ]
            # Phase 10 Plan 10-03 — manifest v2 operational-layer sections.
            # Parse order matters: skills must be parsed BEFORE agents so the
            # agent parser can validate cross-references against the
            # declared-skill-key set (T-10-03-06 mitigation).
            app_skills = _parse_manifest_skills(
                app_node.get("skills"),
                is_public_catalog=is_public_catalog,
                where="app.skills",
            )
            declared_skill_keys = {str(s.get("key") or "") for s in app_skills}
            app_agents = _parse_manifest_agents(
                app_node.get("agents"),
                declared_skill_keys=declared_skill_keys,
                where="app.agents",
            )
            app_settings_schema = _parse_manifest_settings_schema(
                app_node.get("settings_schema"),
                where="app.settings_schema",
            )
            app_seeds = _parse_manifest_seeds(app_node.get("seeds"), where="app.seeds")
            app_permissions = _parse_manifest_permissions(
                app_node.get("permissions"), where="app.permissions"
            )
            app_requires_apps = _parse_manifest_requires_apps(
                app_node.get("requires_apps"), where="app.requires_apps"
            )
            # Phase 30 (DR-30-01 + DR-30-02) — tools[] + hooks[]
            bundle_trust_tier = str(
                (manifest.get("package") or {}).get("trust_tier") or "untrusted"
            )
            bundle_slug = str((manifest.get("package") or {}).get("slug") or "")
            app_tools = _parse_manifest_tools(
                app_node.get("tools"),
                bundle_slug=bundle_slug,
                trust_tier=bundle_trust_tier,
                where="app.tools",
            )
            declared_tool_keys = {str(t.get("key") or "") for t in app_tools}
            app_hooks = _parse_manifest_hooks(
                app_node.get("hooks"),
                declared_tool_keys=declared_tool_keys,
                where="app.hooks",
            )
            app_operations = _parse_manifest_operations(
                app_node.get("operations"),
                where="app.operations",
            )
            # ADR-006 (I-PC-01) — tracks this App may write to unstaged.
            app_unstaged_tracks = _parse_manifest_unstaged_tracks(
                app_node.get("unstaged_tracks"),
                declared_track_keys={
                    str(_as_dict(t, where="app.tracks[]").get("key") or "")
                    for t in tracks_out
                },
                bundle_slug=bundle_slug,
                trust_tier=bundle_trust_tier,
                where="app.unstaged_tracks",
            )

            # F0 — pass through track_aliases (consumed at install for resolver).
            raw_aliases = app_node.get("track_aliases") or []
            app_track_aliases = (
                list(raw_aliases) if isinstance(raw_aliases, list) else []
            )

            out["app"] = {
                "tracks": tracks_out,
                "track_templates": track_templates_out,
                "relations": [
                    _as_dict(r, where="app.relations[]")
                    for r in _as_list(app_node.get("relations"), where="app.relations")
                ],
                "defaults": app_defaults,
                # v2 operational layer (Plan 10-03 MANIFEST-V2-01)
                "skills": app_skills,
                "agents": app_agents,
                "settings_schema": app_settings_schema,
                "seeds": app_seeds,
                "permissions": app_permissions,
                "requires_apps": app_requires_apps,
                # Phase 30 (DR-30-01 + DR-30-02) — pythonic execution + hook bindings
                "tools": app_tools,
                "hooks": app_hooks,
                # F0 extension contract
                "operations": app_operations,
                "track_aliases": app_track_aliases,
                # ADR-006 (I-PC-01)
                "unstaged_tracks": app_unstaged_tracks,
            }
            _assert_unique_keys(tracks_out, where="app.tracks")
            _assert_unique_keys(track_templates_out, where="app.track_templates")
            for tt_spec in track_templates_out:
                tt = _as_dict(tt_spec, where="app.track_templates[]")
                ttk = str(tt.get("key") or "track_template")
                _assert_unique_keys(
                    tt.get("entry_types", []),
                    where=f"app_node.track_templates[{ttk!r}].entry_types",
                )
                _assert_unique_keys(
                    tt.get("views", []),
                    where=f"app_node.track_templates[{ttk!r}].views",
                )
                _validate_entry_type_relation_targets(
                    _as_list(
                        tt.get("entry_types"),
                        where=f"app_node.track_templates[{ttk}].entry_types",
                    ),
                    where=f"app_node.track_templates[{ttk}]",
                )
                _validate_track_tier_defaults(
                    tt, where=f"app_node.track_templates[{ttk}]"
                )
                _validate_calendar_view_mappings(
                    tt, where=f"app_node.track_templates[{ttk}]"
                )
                _validate_related_view_scope_placement(
                    tt, where=f"app_node.track_templates[{ttk}]"
                )
            for track_spec in out["app"]["tracks"]:
                td = _as_dict(track_spec, where="app.tracks[]")
                tk = str(td.get("key") or "track")
                _assert_unique_keys(
                    td.get("entry_types", []),
                    where=f"app_node.tracks[{tk!r}].entry_types",
                )
                _assert_unique_keys(
                    td.get("views", []),
                    where=f"app_node.tracks[{tk!r}].views",
                )
                _validate_entry_type_relation_targets(
                    _as_list(
                        td.get("entry_types"),
                        where=f"app_node.track[{tk}].entry_types",
                    ),
                    where=f"app_node.track[{tk}]",
                )
            _validate_app_relation_graph(_as_dict(out.get("app"), where="app"))
            for track_spec in _as_list(
                _as_dict(out.get("app"), where="app").get("tracks"),
                where="app.tracks",
            ):
                td = _as_dict(track_spec, where="app.tracks[]")
                tk = str(td.get("key") or "track")
                _validate_track_tier_defaults(td, where=f"app_node.tracks[{tk}]")
                _validate_calendar_view_mappings(td, where=f"app_node.tracks[{tk}]")
                _validate_related_view_scope_placement(
                    td, where=f"app_node.tracks[{tk}]"
                )
        if ck is not None:
            if len(_manifest_compile_cache) >= _MANIFEST_CACHE_MAX:
                _manifest_compile_cache.pop(next(iter(_manifest_compile_cache)), None)
            _manifest_compile_cache[ck] = out
        return out
    finally:
        _active_field_composites.reset(field_token)
        _active_view_composites.reset(view_token)


def invalidate_manifest_cache() -> None:
    """Clear compiled manifest cache (call after profile PATCH/merge)."""
    _manifest_compile_cache.clear()


def app_manifest_provisioning_enabled(canonical: Dict[str, Any]) -> bool:
    """True when App-level manifest allows auto-creating tracks from ``app.tracks``.

    Used only when a library package is **applied to an App** (not on track merge).
    """
    if canonical.get("scope") != "app":
        return False
    app_node = canonical.get("app") or {}
    tracks = list(app_node.get("tracks") or [])
    if not tracks:
        return False
    defaults = app_node.get("defaults") or {}
    if "provision_prescribed_tracks" in defaults:
        return bool(defaults["provision_prescribed_tracks"])
    return True


def find_app_track_spec_by_key(
    canonical: Dict[str, Any], track_type_key: str
) -> Optional[Dict[str, Any]]:
    """Return the ``app.tracks[]`` spec with the given ``key``, or None."""
    if canonical.get("scope") != "app":
        return None
    want = str(track_type_key or "").strip()
    if not want:
        return None
    for spec in (canonical.get("app") or {}).get("tracks") or []:
        if str(spec.get("key") or "") == want:
            return spec if isinstance(spec, dict) else None
    return None


def find_app_track_template_spec_by_key(
    canonical: Dict[str, Any], template_key: str
) -> Optional[Dict[str, Any]]:
    """Return the ``app.track_templates[]`` spec with the given ``key``, or None.

    Phase 3.1 ANC-04: consumed by ``materialize_anchor_track`` (Plan 03.1-02)
    to resolve a relation field's ``target_track_template`` against the
    App-attached ContentProfile's compiled manifest.
    """
    if canonical.get("scope") != "app":
        return None
    want = str(template_key or "").strip()
    if not want:
        return None
    for spec in (canonical.get("app") or {}).get("track_templates") or []:
        if str(spec.get("key") or "") == want:
            return spec if isinstance(spec, dict) else None
    return None


def normalize_entry_type_form_schema(
    form_schema: Optional[Dict[str, Any]],
) -> Dict[str, Any]:
    """Normalize author input for an entry type schema.

    Phase 3.1 Plan 03.1-04 (ANC-06): ``related_views`` is round-tripped
    additively so the frontend's RelatedViewsSection can read it off the
    EntryType node directly without a separate manifest fetch.
    """
    raw = _as_dict(form_schema or {}, where="entry_type.form_schema")
    fields = [
        _normalize_field_spec(_as_dict(f, where="entry_type.form_schema.fields[]"))
        for f in _as_list(raw.get("fields"), where="entry_type.form_schema.fields")
    ]
    related_views_raw = _as_list(
        raw.get("related_views"), where="entry_type.form_schema.related_views"
    )
    related_views: List[Dict[str, Any]] = []
    for idx, rv in enumerate(related_views_raw):
        rd = _as_dict(rv, where=f"entry_type.form_schema.related_views[{idx}]")
        view_ref = str(rd.get("view") or "").strip()
        if not view_ref:
            raise BadRequestError(
                message=(
                    f"entry_type.form_schema.related_views[{idx}].view is required"
                )
            )
        bind = _as_dict(
            rd.get("bind"),
            where=f"entry_type.form_schema.related_views[{idx}].bind",
        )
        position = str(rd.get("position") or "related").strip().lower()
        if position not in ("primary", "related"):
            position = "related"
        related_views.append({"view": view_ref, "bind": bind, "position": position})
    out: Dict[str, Any] = {
        "fields": fields,
        "base_fields": _normalize_entry_type_base_fields(raw.get("base_fields")),
        "required_tag_groups": [
            str(x)
            for x in _as_list(
                raw.get("required_tag_groups"),
                where="entry_type.form_schema.required_tag_groups",
            )
        ],
        "related_views": related_views,
        "open_as_page": bool(raw.get("open_as_page", False)),
        "singleton": bool(raw.get("singleton", False)),
    }
    manifest_key = str(raw.get("_manifest_entry_type_key") or "").strip()
    if manifest_key:
        out["_manifest_entry_type_key"] = manifest_key
    # Round-tripped additively, same as related_views above — a stripped
    # create_wizard here would silently drop the multi-step create flow
    # off any entry type this function processes, including on the
    # reconciliation path that syncs an already-materialized EntryType
    # node's form_schema from an updated manifest spec (content_profile_
    # merge.py's _form_schema_from_entry_type_spec calls this).
    wizard = raw.get("create_wizard")
    if wizard is not None:
        out["create_wizard"] = _normalize_create_wizard(
            wizard, where="entry_type.form_schema.create_wizard"
        )
    return out


def normalize_view_config(
    view_type: str, config: Optional[Dict[str, Any]]
) -> Dict[str, Any]:
    """Validate and normalize persisted view config contract."""
    vt = str(view_type or "feed").lower()
    if not _view_type_known(vt):
        raise BadRequestError(
            message=(
                f"type must be one of: "
                f"{', '.join(sorted(view_type_registry.allowed_keys()))}"
            )
        )
    cfg = _as_dict(config or {}, where="view.config")
    normalized = {
        "view_type": vt,
        "filters": _as_list(cfg.get("filters"), where="view.config.filters"),
        "sort": _as_list(cfg.get("sort"), where="view.config.sort"),
        "group_by": cfg.get("group_by"),
        "layout": _as_dict(cfg.get("layout"), where="view.config.layout"),
        "field_visibility": _as_list(
            cfg.get("field_visibility"), where="view.config.field_visibility"
        ),
        "kanban_columns": _as_list(
            cfg.get("kanban_columns"), where="view.config.kanban_columns"
        ),
        "calendar_mapping": _as_dict(
            cfg.get("calendar_mapping"), where="view.config.calendar_mapping"
        ),
        "entry_type_keys": _as_list(
            cfg.get("entry_type_keys"), where="view.config.entry_type_keys"
        ),
        "parent_field": cfg.get("parent_field"),
        "body_field": cfg.get("body_field"),
        "title_field": cfg.get("title_field"),
        "sort_siblings": _as_dict(
            cfg.get("sort_siblings"), where="view.config.sort_siblings"
        ),
        "default_page_id": cfg.get("default_page_id"),
    }
    if vt == "wiki":
        parent_field = str(normalized.get("parent_field") or "").strip()
        if not parent_field:
            raise BadRequestError(
                message="Wiki view requires config.parent_field (relation field key)"
            )
        if not normalized.get("body_field"):
            normalized["body_field"] = "body"
        if not normalized.get("title_field"):
            normalized["title_field"] = "title"
    if vt == "kanban":
        if not normalized["kanban_columns"]:
            normalized["kanban_columns"] = [
                {"key": "todo", "label": "To Do"},
                {"key": "in_progress", "label": "In Progress"},
                {"key": "in_review", "label": "In Review"},
                {"key": "done", "label": "Done"},
            ]
        gb = str(cfg.get("group_by") or normalized.get("group_by") or "").strip()
        # Bare ``status`` is Entry lifecycle — default to system ``_kanban_stage``.
        # Explicit ``custom_fields.status`` is a profile workflow field; preserve it.
        if not gb or gb == "status":
            normalized["group_by"] = KANBAN_STAGE_GROUP_BY
        elif gb:
            normalized["group_by"] = gb
    if vt == "table":
        cols = _as_list(cfg.get("columns"), where="view.config.columns")
        if cols:
            normalized["columns"] = cols
    mk = cfg.get("_manifest_view_key")
    if mk is not None and str(mk).strip():
        normalized["_manifest_view_key"] = str(mk).strip()
    ct = cfg.get("card_template")
    if ct is not None:
        normalized["card_template"] = _as_dict(ct, where="view.config.card_template")
    if "density" in cfg:
        density = str(cfg.get("density") or "").strip().lower()
        if density == "compact":
            normalized["density"] = "compact"
        elif density in ("comfy", "cover"):
            # ``cover`` was a short-lived kanban preset identical to comfy unless
            # the card had a link-preview image; normalize legacy saved views.
            normalized["density"] = "comfy"
    if "card_fields" in cfg:
        normalized["card_fields"] = [
            str(item).strip()
            for item in _as_list(
                cfg.get("card_fields"), where="view.config.card_fields"
            )
            if str(item).strip()
        ]
    # Non-builtin view types (plugin-registered or manifest composites) own
    # their entire config shape — the named-key extraction above only covers
    # the handful of built-in widgets (feed/kanban/table/calendar/gallery/
    # wiki/composable_*). Without this, any config key a plugin widget
    # declares that isn't one of those named keys (e.g. layout_container's
    # ``regions``, form_region's ``fields``) is silently dropped on every
    # save, which only ever manifested as a broken/empty widget client-side.
    # Builtin behavior is untouched: this branch never runs for them.
    spec = view_type_registry.resolve(vt)
    if spec is not None and spec.source != "builtin":
        for key, value in cfg.items():
            if key not in normalized:
                normalized[key] = value
    return normalized


def materialize_view_config_from_spec(spec: Dict[str, Any]) -> Dict[str, Any]:
    """Build normalized persisted config from a declarative manifest view spec."""
    sd = _as_dict(spec, where="view spec")
    view_type = str(sd.get("view_type") or sd.get("type") or "feed").lower()
    inline_config: Dict[str, Any] = {}
    if "filters" in sd:
        inline_config["filters"] = sd.get("filters")
    if "sort" in sd:
        inline_config["sort"] = sd.get("sort")
    if "group_by" in sd:
        inline_config["group_by"] = sd.get("group_by")
    if "layout" in sd:
        inline_config["layout"] = sd.get("layout")
    if "field_visibility" in sd:
        inline_config["field_visibility"] = sd.get("field_visibility")
    if "kanban_columns" in sd:
        inline_config["kanban_columns"] = sd.get("kanban_columns")
    if "calendar_mapping" in sd:
        inline_config["calendar_mapping"] = sd.get("calendar_mapping")
    if "columns" in sd:
        inline_config["columns"] = sd.get("columns")
    if "entry_type_keys" in sd:
        inline_config["entry_type_keys"] = sd.get("entry_type_keys")
    if "card_template" in sd:
        inline_config["card_template"] = sd.get("card_template")
    if "parent_field" in sd:
        inline_config["parent_field"] = sd.get("parent_field")
    if "body_field" in sd:
        inline_config["body_field"] = sd.get("body_field")
    if "title_field" in sd:
        inline_config["title_field"] = sd.get("title_field")
    if "sort_siblings" in sd:
        inline_config["sort_siblings"] = sd.get("sort_siblings")
    if "default_page_id" in sd:
        inline_config["default_page_id"] = sd.get("default_page_id")
    # Non-builtin view types (plugin-registered or manifest composites) own
    # their entire config shape — the named-key extraction above only covers
    # built-in widgets. Without this, a plugin widget's flat top-level config
    # keys (e.g. chart_region's ``chart_type``, tree_region's
    # ``label_field``) never reach ``inline_config``, so they're lost before
    # ``normalize_view_config``'s own passthrough ever sees them. Reserved
    # structural spec keys (identity/entry-type-projection fields handled
    # elsewhere) are excluded. Builtin behavior is untouched.
    _RESERVED_SPEC_KEYS = {
        "key",
        "name",
        "view_type",
        "type",
        "is_default",
        "config",
        "default_entry_type",
        "entry_types",
        "entry_type_keys",
    }
    spec_view_type_spec = view_type_registry.resolve(view_type)
    if spec_view_type_spec is not None and spec_view_type_spec.source != "builtin":
        for key, value in sd.items():
            if key not in inline_config and key not in _RESERVED_SPEC_KEYS:
                inline_config[key] = value
    declared_config = _as_dict(sd.get("config"), where="view.config")
    merged = {**inline_config, **declared_config}
    vk = str(sd.get("key") or "").strip()
    if vk:
        merged["_manifest_view_key"] = vk
    return normalize_view_config(view_type, merged)


def _scan_seeded_libraries_for_view_key(
    manifest_view_key: str,
) -> Optional[Dict[str, Any]]:
    """Find a view spec across registered seeded libraries by manifest key.

    Used as fallback when the persisted profile manifest was written before
    ``entry_types`` existed on view specs. The seeded library source is the
    authoritative declaration for built-in templates.
    """
    if not manifest_view_key:
        return None
    try:
        from app.services.content_profile_loader import load_library_profiles
    except Exception:
        return None

    target = _slug(str(manifest_view_key))

    def _walk_tier(tier: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        for v in _as_list(tier.get("views"), where="seed.views") or []:
            if not isinstance(v, dict):
                continue
            k = _slug(str(v.get("key") or ""))
            if k == target:
                return v
        return None

    try:
        specs = load_library_profiles()
    except Exception:
        return None

    for pkg in specs:
        manifest = pkg.manifest if isinstance(pkg.manifest, dict) else {}
        scope = str(manifest.get("scope") or "")
        if scope == "track":
            tier = manifest.get("track") or {}
            hit = _walk_tier(tier if isinstance(tier, dict) else {})
            if hit:
                return hit
        elif scope == "app":
            app_node = manifest.get("app") or {}
            tracks_list = (
                _as_list(
                    (app_node if isinstance(app_node, dict) else {}).get("tracks"),
                    where="seed.app_node.tracks",
                )
                or []
            )
            for t in tracks_list:
                if not isinstance(t, dict):
                    continue
                hit = _walk_tier(t)
                if hit:
                    return hit
    return None
