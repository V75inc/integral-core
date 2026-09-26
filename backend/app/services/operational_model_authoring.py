"""OperationalModel helper functions exposed to the embedded agent.

These functions package the inline logic that lives today in
``backend/app/agentive/services/agent_tools.py::execute_tool`` for the
Operational Model-related MCP tools (``integral_author_model``,
``integral_modify_model``, ``integral_list_models``). They are
lifted here so the in-process bridge action (``EmbeddedIntegralAction``)
can call them directly without going through the MCP HTTP surface, and
so a future Phase-0c refactor can switch the MCP path to call into the
same helpers (DRYing the two surfaces).

For now ``agent_tools.py`` is intentionally left untouched — these
helpers run alongside the existing inline implementations. The two
copies must stay in functional lockstep until Phase 0c collapses them.

Return shapes mirror the existing MCP tool envelopes one-for-one:

* success → ``{...result fields..., "message": "..."}``
* failure → ``{"error": "...code...", "detail": "..."}`` (skill-friendly
  envelope; not the FastAPI 5-key error shape — these are called from
  in-process skills, not HTTP handlers)
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Reads
# ---------------------------------------------------------------------------


async def list_library_operational_models(
    *,
    keyword: Optional[str] = None,
    scope: Optional[str] = None,
) -> Dict[str, Any]:
    """List library Operational Model packages with optional filters.

    Supports keyword filtering and scope filtering ('track' or 'app').

    Mirrors ``integral_list_models`` from the MCP tool surface.
    """
    from app.models.nodes import OPERATIONAL_MODELS_REGISTRY_ID, OperationalModels
    from app.utils.text_matching import casefold_match

    cps_reg = await OperationalModels.get(OPERATIONAL_MODELS_REGISTRY_ID)
    if not cps_reg:
        return {"operational_models": [], "total": 0}

    lib_profiles = await cps_reg.nodes(edge=["CATALOGS"], node=["OperationalModel"])
    results: List[Dict[str, Any]] = []
    for lp in lib_profiles:
        if not getattr(lp, "library_package", False):
            continue
        if (getattr(lp, "metadata", None) or {}).get("seed_status") == "inactive":
            continue
        manifest = getattr(lp, "manifest", {}) or {}
        lp_scope = str(manifest.get("scope", ""))

        if scope and lp_scope != scope:
            continue
        if keyword:
            lp_name = getattr(lp, "name", "") or ""
            if casefold_match(keyword.casefold(), lp_name.casefold()) < 0.3:
                continue

        results.append(
            {
                "id": lp.id,
                "name": getattr(lp, "name", ""),
                "description": getattr(lp, "description", ""),
                "scope": lp_scope,
                "version": getattr(lp, "version", ""),
            }
        )
    return {"operational_models": results, "total": len(results)}


async def get_attached_operational_model(
    *,
    user_id: str,
    track_id: Optional[str] = None,
    app_id: Optional[str] = None,
) -> Dict[str, Any]:
    """Describe the OperationalModel attached to a track or app_node.

    Includes its EntryTypes, Views, and Tags.

    Either ``track_id`` or ``app_id`` is required (not both).
    """
    from app.models.nodes import App, Track
    from app.services.app_graph import (
        get_app_attached_operational_model,
        get_track_attached_operational_model,
    )

    if not track_id and not app_id:
        return {
            "error": "missing_argument",
            "detail": "track_id or app_id is required",
        }
    if track_id and app_id:
        return {
            "error": "invalid_argument",
            "detail": "Provide track_id OR app_id, not both",
        }

    from app.services.permissions import can_view_app, can_view_track

    cp = None
    container_label = ""
    if track_id:
        if not await can_view_track(user_id, track_id):
            return {"error": "forbidden", "detail": "no read access to track"}
        track = await Track.get(track_id)
        if not track:
            return {"error": "not_found", "detail": "Track not found"}
        cp = await get_track_attached_operational_model(track)
        container_label = getattr(track, "title", "") or track_id
    else:
        if not await can_view_app(user_id, app_id):  # type: ignore[arg-type]
            return {"error": "forbidden", "detail": "no read access to app"}
        app_node = await App.get(app_id)
        if not app_node:
            return {"error": "not_found", "detail": "App not found"}
        cp = await get_app_attached_operational_model(app_node)
        container_label = getattr(app_node, "name", "") or (app_id or "")

    if not cp:
        return {
            "track_id": track_id,
            "app_id": app_id,
            "container_label": container_label,
            "attached_profile": None,
            "message": "No OperationalModel attached.",
        }

    entry_types = await cp.nodes(edge=["CONTAINS"], node=["EntryType"])
    tags = await cp.nodes(edge=["CONTAINS"], node=["Tag"])

    return {
        "track_id": track_id,
        "app_id": app_id,
        "container_label": container_label,
        "attached_profile": {
            "id": cp.id,
            "name": getattr(cp, "name", ""),
            "scope": getattr(cp, "scope", ""),
            "library_package": getattr(cp, "library_package", False),
            "library_merge_source_id": getattr(cp, "library_merge_source_id", None),
            "entry_types": [
                {
                    "id": et.id,
                    "name": getattr(et, "name", ""),
                    "icon": getattr(et, "icon", ""),
                }
                for et in entry_types
            ],
            "tags": [
                {
                    "id": t.id,
                    "name": getattr(t, "name", ""),
                    "color": getattr(t, "color", ""),
                    "group_key": getattr(t, "group_key", None),
                }
                for t in tags
            ],
        },
    }


# ---------------------------------------------------------------------------
# Writes
# ---------------------------------------------------------------------------


def _short_operational_model_name(name: Optional[str], description: str) -> str:
    """A clean, short label for a operational model / its starter entry type.

    Uses an explicit ``name`` when given; otherwise derives one from the first
    sentence of ``description`` (capped to a few words / 48 chars) so a verbose
    authoring prompt never becomes the entry-type label. Falls back to
    ``"Untitled"``.
    """
    import re

    if name and name.strip():
        return name.strip()[:48]
    first_line = (description or "").strip().splitlines()[0] if description else ""
    first_sentence = re.split(r"[.!?]", first_line, maxsplit=1)[0].strip()
    short = " ".join(first_sentence.split()[:6]).strip()[:48].strip()
    return short or "Untitled"


_ALLOWED_MANIFEST_FIELD_KEYS = frozenset(
    {
        "key",
        "name",
        "type",
        "enum",
        "options",
        "target",
        "target_track",
        "relation",
        "required",
        "help",
        "placeholder",
        "default",
        "multiple",
        "expression",
        "config",
        "is_primary",
        "primary",
    }
)


def _normalize_manifest_field(raw: Dict[str, Any]) -> Dict[str, Any]:
    """Coerce one agent-supplied field dict into a manifest field.

    Slugs the key, fills a display ``name``/``type`` default, and passes the
    recognized field attributes (``enum`` for select, ``target`` for relation,
    …) through verbatim. ``compile_canonical_manifest`` does the authoritative
    type-vs-registry validation downstream, so this stays light.
    """
    from app.services.operational_model_runtime import slug_manifest_key

    src = dict(raw or {})
    display = (src.get("name") or src.get("key") or "").strip()
    key = slug_manifest_key(src.get("key") or display or "field")
    field = {k: v for k, v in src.items() if k in _ALLOWED_MANIFEST_FIELD_KEYS}
    field["key"] = key
    field["name"] = display or key.replace("_", " ").title()
    field["type"] = (src.get("type") or "text").strip() or "text"
    return field


def _build_manifest_entry_types(
    entry_types: Optional[List[Dict[str, Any]]], fallback_name: str
) -> List[Dict[str, Any]]:
    """Build the manifest ``track.entry_types`` list.

    When the caller supplies structured ``entry_types`` (each ``{name, fields}``)
    they are normalized into populated manifest entry types. When omitted, falls
    back to the single empty starter type named after the Operational Model (legacy shape).
    """
    from app.services.operational_model_runtime import slug_manifest_key

    if not entry_types:
        key = slug_manifest_key(fallback_name)
        return [
            {
                "key": key,
                "name": fallback_name,
                "icon": "document",
                "fields": [],
                "required_tag_groups": [],
            }
        ]

    built: List[Dict[str, Any]] = []
    for raw in entry_types:
        et = dict(raw or {})
        et_name = (et.get("name") or et.get("key") or "").strip() or fallback_name
        et_key = slug_manifest_key(et.get("key") or et_name)
        fields = [
            _normalize_manifest_field(f)
            for f in (et.get("fields") or [])
            if (f or {}).get("name") or (f or {}).get("key")
        ]
        built.append(
            {
                "key": et_key,
                "name": et_name,
                "icon": (et.get("icon") or "document"),
                "fields": fields,
                "required_tag_groups": et.get("required_tag_groups") or [],
            }
        )
    # All-empty input degrades to the starter shape.
    return built or _build_manifest_entry_types(None, fallback_name)


def validate_inline_entry_types(entry_types: List[Dict[str, Any]]) -> None:
    """Compile the complete inline schema before a track can be created."""
    from app.services.operational_model_runtime import compile_canonical_manifest

    if not entry_types or any(
        not isinstance(et, dict) or not (et.get("name") or et.get("key"))
        for et in entry_types
    ):
        raise ValueError("Every inline entry type requires a name or key")
    compile_canonical_manifest(
        manifest={
            "operational_model_schema_version": 2,
            "scope": "track",
            "track": {
                "entry_types": _build_manifest_entry_types(entry_types, "Record")
            },
        }
    )


def normalize_inline_taxonomy(taxonomy: Any) -> List[Dict[str, Any]]:
    """Canonical ``tag_groups`` for a Track created with an inline vocabulary.

    Accepts ``{tag_groups: [{name|key, tags: [name | {name, color?, parent?}]}]}``
    (or the bare group list) and returns ``[{key, name, tags: [{name, color?, parent?}]}]``. Tag names
    are unique per Track (the Tag uniqueness scope), and a ``parent`` must name
    an earlier tag of the same group so tags can be created in order.
    """
    import re

    if taxonomy is None:
        return []
    raw_groups = taxonomy.get("tag_groups") if isinstance(taxonomy, dict) else taxonomy
    if not isinstance(raw_groups, list):
        raise ValueError("taxonomy must be {tag_groups: [{name, tags: [...]}]}")
    groups: List[Dict[str, Any]] = []
    seen_tags: set = set()
    seen_groups: set = set()
    for raw in raw_groups:
        if not isinstance(raw, dict):
            raise ValueError("Each tag group must be an object with name and tags")
        name = str(raw.get("name") or raw.get("key") or "").strip()
        key = re.sub(r"[^a-z0-9]+", "_", name.casefold()).strip("_")
        if not key or key in seen_groups:
            raise ValueError(f"Tag group {name!r} needs a unique name")
        seen_groups.add(key)
        raw_tags = raw.get("tags")
        if not isinstance(raw_tags, list) or not raw_tags:
            raise ValueError(f"Tag group {name!r} needs at least one tag")
        group_tags: List[Dict[str, Any]] = []
        for tag in raw_tags:
            spec = {"name": tag} if isinstance(tag, str) else tag
            if not isinstance(spec, dict):
                raise ValueError(f"Tag group {name!r} has an invalid tag")
            tag_name = str(spec.get("name") or "").strip()
            if not tag_name or len(tag_name) > 80:
                raise ValueError(f"Tag group {name!r} has a tag without a valid name")
            if tag_name.casefold() in seen_tags:
                raise ValueError(f"Tag {tag_name!r} is declared twice on this Track")
            parent = str(spec.get("parent") or "").strip()
            if parent and parent.casefold() not in {
                t["name"].casefold() for t in group_tags
            }:
                raise ValueError(
                    f"Tag {tag_name!r} names parent {parent!r}, which must be an "
                    "earlier tag in the same group"
                )
            seen_tags.add(tag_name.casefold())
            out: Dict[str, Any] = {"name": tag_name}
            if spec.get("color"):
                out["color"] = str(spec["color"])
            if parent:
                out["parent"] = parent
            group_tags.append(out)
        groups.append({"key": key, "name": name, "tags": group_tags})
    return groups


async def register_app_track_template(
    *,
    user_id: str,
    app_id: str,
    name: str,
    entry_types: List[Dict[str, Any]],
    description: str = "",
) -> Dict[str, Any]:
    """Add a named ``app.track_templates[]`` entry to an App's attached model.

    Registration only: no Track is created here. A relation field with
    ``target: track``, ``target_track_template: <key>`` and ``auto_provision``
    provisions one detail Track per parent Entry through
    ``materialize_anchor_track``.
    """
    import copy

    from app.api.errors import (
        BadRequestError,
        InsufficientPermissionsError,
        ResourceNotFoundError,
    )
    from app.models.nodes import App
    from app.schemas.policy import Resource, Subject
    from app.services.app_graph import ensure_app_attached_operational_model
    from app.services.operational_model_runtime import (
        compile_canonical_manifest,
        slug_manifest_key,
    )
    from app.services.policy_engine import evaluate as policy_evaluate

    template_name = (name or "").strip()
    if not template_name:
        raise BadRequestError(message="A track template needs a name")
    try:
        validate_inline_entry_types(entry_types)
    except ValueError as exc:
        raise BadRequestError(message=str(exc)) from exc
    app_node = await App.get(app_id)
    if app_node is None:
        raise ResourceNotFoundError(message="App not found")
    decision = await policy_evaluate(
        subject=Subject(kind="human", id=user_id),
        action="app.update",
        resource=Resource(kind="app", id=app_id, scope=f"app:{app_id}"),
    )
    if not decision.allowed:
        raise InsufficientPermissionsError(message="Access denied")

    cp = await ensure_app_attached_operational_model(app_node)
    manifest = copy.deepcopy(cp.manifest or {})
    templates = manifest.setdefault("app", {}).setdefault("track_templates", [])
    key = slug_manifest_key(template_name)
    if any(str(t.get("key")) == key for t in templates if isinstance(t, dict)):
        raise BadRequestError(
            message=f"This App already has a track template named {template_name!r}"
        )
    templates.append(
        {
            "key": key,
            "name": template_name,
            "description": (description or "").strip(),
            "entry_types": _build_manifest_entry_types(entry_types, template_name),
            "views": [],
            "taxonomy": {"tag_groups": []},
            "defaults": {},
        }
    )
    compile_canonical_manifest(manifest=manifest)
    cp.manifest = manifest
    cp.updated_at = datetime.now(timezone.utc).isoformat()
    await cp.save()
    return {"track_template": {"key": key, "name": template_name, "app_id": app_id}}


async def apply_entry_types_to_track(
    *,
    user_id: str,
    track_id: str,
    entry_types: List[Dict[str, Any]],
) -> Dict[str, Any]:
    """Materialize agent-supplied entry types (with fields) onto a track's
    attached OperationalModel, replacing the generic starter ``Post`` type.

    This is what makes a custom-built track's "+New" form render its declared
    fields (June 29 QA #4). The realistic agent scaffold path creates a track
    via ``create_app_track`` (which gets the default Post + Feed bootstrap) and
    authored a *separate library* profile — the two were never joined, so the
    track kept an empty ``Post`` type and the form showed only title/detail.
    Passing ``entry_types`` straight to track creation and running them through
    this helper lands the fields on the track itself, in one step.

    Each ``entry_type`` is ``{name, icon?, fields:[{key,name,type,enum?}, …]}`` —
    the exact shape the agent already produces for ``integral_author_model``.
    Fields go through ``normalize_entry_type_form_schema`` (the same path the
    UI/create_entry_type endpoint uses), so selects keep their options, etc.
    The auto-created ``Post`` starter type is removed when real types are
    supplied so it doesn't linger as a fields-less option.
    """
    from app.models.edges import CONTAINS
    from app.models.nodes import EntryType, Track
    from app.services.app_graph import (
        ensure_track_attached_operational_model,
        get_track_attached_operational_model,
    )
    from app.services.operational_model_runtime import (
        normalize_entry_type_form_schema,
        sync_attached_manifest,
    )

    validate_inline_entry_types(entry_types)

    specs = [
        et
        for et in (entry_types or [])
        if isinstance(et, dict) and ((et.get("name") or et.get("key")))
    ]
    if not specs:
        return {"created": [], "removed_starter": False}

    track = await Track.get(track_id)
    if not track:
        return {"error": "not_found", "detail": "Track not found"}
    cp = await get_track_attached_operational_model(track)
    if not cp:
        cp = await ensure_track_attached_operational_model(track)

    now = datetime.now(timezone.utc).isoformat()
    created: List[Dict[str, str]] = []
    for et in specs:
        et_name = (et.get("name") or et.get("key") or "").strip()
        if not et_name:
            continue
        normalized_fields = [
            _normalize_manifest_field(f)
            for f in (et.get("fields") or [])
            if (f or {}).get("name") or (f or {}).get("key")
        ]
        form_schema = normalize_entry_type_form_schema({"fields": normalized_fields})
        node = await EntryType.create(
            name=et_name,
            icon=et.get("icon") or "document",
            form_schema=form_schema,
            track_id=track_id,
            is_template=False,
            created_at=now,
            updated_at=now,
        )
        await cp.connect(node, edge=CONTAINS, added_at=now)
        created.append({"entry_type_id": node.id, "name": et_name})

    # Drop the generic starter ``Post`` type so it doesn't show up as an
    # empty, fields-less option alongside the real ones (only when we added
    # real types, and never remove a type the caller explicitly named).
    removed_starter = False
    if created:
        supplied_names = {
            (et.get("name") or et.get("key") or "").strip().casefold() for et in specs
        }
        existing = await cp.nodes(edge=[CONTAINS], node=["EntryType"])
        for node in existing:
            nm = str(getattr(node, "name", "") or "").strip()
            fields = (getattr(node, "form_schema", {}) or {}).get("fields") or []
            if nm.casefold() == "post" and "post" not in supplied_names and not fields:
                await node.delete()
                removed_starter = True

    await sync_attached_manifest(cp)
    return {"created": created, "removed_starter": removed_starter}


async def author_operational_model(
    *,
    user_id: str,
    description: str,
    scope: str = "track",
    name: Optional[str] = None,
    workspace_id: Optional[str] = None,
    entry_types: Optional[List[Dict[str, Any]]] = None,
) -> Dict[str, Any]:
    """Create a new library Operational Model from a description.

    When ``entry_types`` is supplied (each ``{name, fields:[{key,name,type,
    enum?}, …]}``) the manifest is populated with those types and their fields
    in one shot. When omitted, generates the minimal track-scoped manifest with
    one empty EntryType and a default Feed view (legacy starter behaviour).

    Returns the new OperationalModel id, name, and the compiled manifest.
    """
    from app.models.nodes import (
        OPERATIONAL_MODELS_REGISTRY_ID,
        OperationalModel,
        OperationalModels,
    )
    from app.services.app_graph import ensure_catalog_edge
    from app.services.operational_model_runtime import compile_canonical_manifest

    description = (description or "").strip()
    if not description:
        return {"error": "missing_argument", "detail": "description is required"}
    if not workspace_id:
        return {
            "error": "missing_argument",
            "detail": "workspace_id is required (no implicit derivation)",
        }

    from app.schemas.policy import Resource, Subject
    from app.services.permissions import can_publish_operational_models_under_workspace
    from app.services.policy_engine import evaluate as policy_evaluate

    decision = await policy_evaluate(
        subject=Subject(kind="human", id=user_id),
        action="operational_model.author",
        resource=Resource(
            kind="operational_model",
            id="*",
            scope=f"workspace:{workspace_id}",
        ),
    )
    if not decision.allowed:
        return {"error": "forbidden", "detail": "operational_model.author denied"}
    if not await can_publish_operational_models_under_workspace(user_id, workspace_id):
        return {
            "error": "forbidden",
            "detail": "No publish rights under workspace",
        }

    # ``description`` is free-form authoring intent (often several sentences).
    # Never use it verbatim as a NAME — it surfaces as the entry-type label on
    # the track's "New <type>" quick-add. Prefer an explicit ``name``; otherwise
    # derive a short, clean label from the first words of the description.
    profile_name = _short_operational_model_name(name, description)
    built_entry_types = _build_manifest_entry_types(entry_types, profile_name)
    type_key = built_entry_types[0]["key"]
    manifest = {
        "operational_model_schema_version": 2,
        "scope": scope,
        "track": {
            "entry_types": built_entry_types,
            "views": [
                {
                    "key": "feed",
                    "name": "Feed",
                    "view_type": "feed",
                    "is_default": True,
                    "filters": [],
                    "sort": [],
                    "group_by": None,
                    "layout": {},
                    "field_visibility": [],
                    "kanban_columns": [],
                    "calendar_mapping": {},
                    "config": {},
                }
            ],
            "taxonomy": {"tag_groups": []},
            "defaults": {"default_entry_type": type_key, "default_view": "feed"},
        },
        "package": {},
        "migrations": [],
    }

    try:
        compiled = compile_canonical_manifest(manifest=manifest, scope_hint=scope)
    except Exception as exc:  # noqa: BLE001 — surfaces validation message to agent
        return {"error": "manifest_invalid", "detail": str(exc)}

    now = datetime.now(timezone.utc).isoformat()
    cp = await OperationalModel.create(
        name=profile_name,
        version="1.0.0",
        manifest=compiled,
        scope=scope,
        workspace_id=workspace_id,
        library_package=True,
        description=description,
        created_at=now,
        updated_at=now,
    )

    cps_reg = await OperationalModels.get(OPERATIONAL_MODELS_REGISTRY_ID)
    if cps_reg:
        await ensure_catalog_edge(cps_reg, cp)

    return {
        "operational_model_id": cp.id,
        "name": cp.name,
        "manifest": compiled,
        "message": f"Authored library Operational Model '{profile_name}'",
    }


_VALID_MODIFY_ACTIONS = (
    "add_entry_type",
    "remove_entry_type",
    "add_view",
    "remove_view",
    "add_tag",
    "remove_tag",
)


async def modify_operational_model(
    *,
    user_id: str,
    track_id: Optional[str] = None,
    app_id: Optional[str] = None,
    action: str,
    name: Optional[str] = None,
    icon: Optional[str] = None,
    view_type: Optional[str] = None,
    config: Optional[Dict[str, Any]] = None,
    fields: Optional[List[Dict[str, Any]]] = None,
    color: Optional[str] = None,
    group_key: Optional[str] = None,
    entry_type_id: Optional[str] = None,
    view_id: Optional[str] = None,
    tag_id: Optional[str] = None,
    is_default: bool = False,
) -> Dict[str, Any]:
    """Add or remove an EntryType / View / Tag on an attached OperationalModel.

    Operates on the Operational Model attached to a Track or App.

    Mirrors ``integral_modify_model`` from the MCP tool surface
    one-for-one; kept in lockstep until Phase 0c DRY.
    """
    from app.models.edges import CONTAINS
    from app.models.nodes import App, EntryType, Tag, Track, View
    from app.services.app_graph import (
        ensure_catalog_edge,
        get_app_attached_operational_model,
        get_or_create_views_registry_for_operational_model,
        get_track_attached_operational_model,
    )
    from app.services.operational_model_runtime import (
        normalize_entry_type_form_schema,
        normalize_view_config,
        sync_attached_manifest,
    )

    if not track_id and not app_id:
        return {
            "error": "missing_argument",
            "detail": "track_id or app_id is required",
        }
    if track_id and app_id:
        return {
            "error": "invalid_argument",
            "detail": "Provide track_id OR app_id, not both",
        }
    if action not in _VALID_MODIFY_ACTIONS:
        return {
            "error": "invalid_argument",
            "detail": f"action must be one of: {', '.join(_VALID_MODIFY_ACTIONS)}",
        }

    cp = None
    track_obj: Optional[Track] = None
    if track_id:
        track_obj = await Track.get(track_id)
        if not track_obj:
            return {"error": "not_found", "detail": "Track not found"}
        cp = await get_track_attached_operational_model(track_obj)
        if not cp:
            return {
                "error": "not_found",
                "detail": "Track has no attached operational model",
            }
    else:
        app_node = await App.get(app_id)
        if not app_node:
            return {"error": "not_found", "detail": "App not found"}
        cp = await get_app_attached_operational_model(app_node)
        if not cp:
            return {
                "error": "not_found",
                "detail": "App has no attached operational model",
            }

    if not await _user_can_edit_cp(user_id=user_id, cp=cp):
        return {"error": "forbidden", "detail": "no edit access to operational model"}

    now = datetime.now(timezone.utc).isoformat()

    if action == "add_entry_type":
        et_name = (name or "").strip()
        if not et_name:
            return {
                "error": "missing_argument",
                "detail": "name is required for add_entry_type",
            }
        # Field specs may arrive as a dedicated ``fields`` list (the shape the
        # agent already knows from integral_author_model —
        # {key,name,type,enum?}) or, for back-compat, nested under
        # ``config.fields``. Without either, the EntryType is created with an
        # empty form_schema and the "+New" quick-add renders only the generic
        # title/detail slots — the agent-created-app "fields don't show up" bug
        # (June 29 QA #4). Normalizing ``{fields:[…]}`` here is the SAME path the
        # working author_operational_model→materialize flow uses, so structured fields
        # reach form_schema.fields and the form renders them.
        raw_fields = fields
        if not raw_fields and isinstance(config, dict):
            cfg_fields = config.get("fields")
            if isinstance(cfg_fields, list):
                raw_fields = cfg_fields
        if raw_fields:
            normalized_fields = [
                _normalize_manifest_field(f)
                for f in raw_fields
                if (f or {}).get("name") or (f or {}).get("key")
            ]
            form_schema = normalize_entry_type_form_schema(
                {"fields": normalized_fields}
            )
        else:
            form_schema = normalize_entry_type_form_schema(config)
        et = await EntryType.create(
            name=et_name,
            icon=icon or "document",
            form_schema=form_schema,
            track_id=track_id or "",
            is_template=False,
            created_at=now,
            updated_at=now,
        )
        await cp.connect(et, edge=CONTAINS, added_at=now)
        await sync_attached_manifest(cp)
        return {
            "action": action,
            "entry_type_id": et.id,
            "name": et.name,
            "message": f"Entry type '{et_name}' added",
        }

    if action == "remove_entry_type":
        if not entry_type_id:
            return {"error": "missing_argument", "detail": "entry_type_id is required"}
        et = await EntryType.get(entry_type_id)
        if not et:
            return {"error": "not_found", "detail": "Entry type not found"}
        await et.delete()
        await sync_attached_manifest(cp)
        return {
            "action": action,
            "entry_type_id": entry_type_id,
            "message": "Entry type removed",
        }

    if action == "add_view":
        view_name = (name or "Feed").strip()
        v_type = view_type or "feed"
        resolved_config = normalize_view_config(v_type, config or {})
        make_default = bool(is_default and track_obj is not None)
        if make_default:
            import copy

            from app.services.operational_model_runtime import slug_manifest_key

            # The manifest default is authoritative: re-syncs re-derive the
            # View.is_default flags from it, matched by _manifest_view_key.
            view_key = slug_manifest_key(view_name)
            resolved_config = {**resolved_config, "_manifest_view_key": view_key}
            manifest = copy.deepcopy(cp.manifest or {})
            manifest.setdefault("track", {}).setdefault("defaults", {})[
                "default_view"
            ] = view_key
            cp.manifest = manifest
        vreg = await get_or_create_views_registry_for_operational_model(
            cp, track=track_obj
        )
        view = await View.create(
            name=view_name,
            type=v_type,
            config=resolved_config,
            track_id=track_id or "",
            operational_model_id=cp.id,
            is_template=False,
            is_default=make_default,
            created_by=user_id,
            created_at=now,
            updated_at=now,
        )
        await ensure_catalog_edge(vreg, view)
        await sync_attached_manifest(cp)
        if make_default:
            from app.services.operational_model_runtime import (
                synchronize_track_view_default_flags,
            )

            await synchronize_track_view_default_flags(track_obj)
        return {
            "action": action,
            "view_id": view.id,
            "name": view.name,
            "message": f"View '{view_name}' added",
        }

    if action == "remove_view":
        if not view_id:
            return {"error": "missing_argument", "detail": "view_id is required"}
        view = await View.get(view_id)
        if not view:
            return {"error": "not_found", "detail": "View not found"}
        await view.delete()
        await sync_attached_manifest(cp)
        return {"action": action, "view_id": view_id, "message": "View removed"}

    if action == "add_tag":
        tag_name = (name or "").strip()
        if not tag_name:
            return {
                "error": "missing_argument",
                "detail": "name is required for add_tag",
            }
        tag = await Tag.create(
            name=tag_name,
            color=color or "#6B7280",
            track_id=track_id or "",
            app_id=app_id or "",
            group_key=group_key,
            is_template=False,
            created_at=now,
        )
        await cp.connect(tag, edge=CONTAINS, added_at=now)
        await sync_attached_manifest(cp)
        return {
            "action": action,
            "tag_id": tag.id,
            "name": tag.name,
            "message": f"Tag '{tag_name}' added",
        }

    if action == "remove_tag":
        if not tag_id:
            return {"error": "missing_argument", "detail": "tag_id is required"}
        tag = await Tag.get(tag_id)
        if not tag:
            return {"error": "not_found", "detail": "Tag not found"}
        await tag.delete()
        await sync_attached_manifest(cp)
        return {"action": action, "tag_id": tag_id, "message": "Tag removed"}

    # Defensive — should be unreachable after the validation above.
    return {"error": "invalid_argument", "detail": f"Unknown action: {action}"}


async def apply_library_operational_model(
    *,
    user_id: str,
    library_operational_model_id: str,
    track_id: Optional[str] = None,
    app_id: Optional[str] = None,
) -> Dict[str, Any]:
    """Merge a library Operational Model package into the attached operational model.

    Operates on the Operational Model attached to a track or app_node.

    This is the "extend what's there" path the agent's system prompt
    favours over inventing new shape. For brand-new tracks the
    ``type_hint`` parameter on ``create_track`` does this implicitly;
    for *existing* tracks/apps this is the only path.
    """
    from app.models.nodes import App, OperationalModel, Track
    from app.services.app_graph import (
        get_app_attached_operational_model,
        get_track_attached_operational_model,
    )
    from app.services.operational_model_merge import (
        merge_library_manifest_into_operational_model,
    )

    if not track_id and not app_id:
        return {
            "error": "missing_argument",
            "detail": "track_id or app_id is required",
        }
    if track_id and app_id:
        return {
            "error": "invalid_argument",
            "detail": "Provide track_id OR app_id, not both",
        }
    if not library_operational_model_id:
        return {
            "error": "missing_argument",
            "detail": "library_operational_model_id is required",
        }

    from app.schemas.policy import Resource, Subject
    from app.services.policy_engine import evaluate as policy_evaluate

    lib = await OperationalModel.get(library_operational_model_id)
    if not lib or not getattr(lib, "library_package", False):
        return {"error": "not_found", "detail": "Library operational model not found"}

    if track_id:
        decision = await policy_evaluate(
            subject=Subject(kind="human", id=user_id),
            action="track.update",
            resource=Resource(kind="track", id=track_id, scope=f"track:{track_id}"),
        )
        if not decision.allowed:
            return {"error": "forbidden", "detail": "no permission to update track"}
        track = await Track.get(track_id)
        if not track:
            return {"error": "not_found", "detail": "Track not found"}
        attached = await get_track_attached_operational_model(track)
        if not attached:
            return {
                "error": "not_found",
                "detail": "Track has no attached operational model to merge into",
            }
        await merge_library_manifest_into_operational_model(
            lib, attached, track, for_space=False
        )
        track.library_merge_source_id = library_operational_model_id
        await track.save()
        return {
            "track_id": track_id,
            "library_operational_model_id": library_operational_model_id,
            "library_profile_name": getattr(lib, "name", ""),
            "message": (
                f"Applied library Operational Model '{getattr(lib, 'name', library_operational_model_id)}' "
                f"to track."
            ),
        }

    decision = await policy_evaluate(
        subject=Subject(kind="human", id=user_id),
        action="app.update",
        resource=Resource(kind="app", id=app_id, scope=f"app:{app_id}"),  # type: ignore[arg-type]
    )
    if not decision.allowed:
        return {"error": "forbidden", "detail": "no permission to update app"}
    app_node = await App.get(app_id)
    if not app_node:
        return {"error": "not_found", "detail": "App not found"}
    attached = await get_app_attached_operational_model(app_node)
    if not attached:
        return {
            "error": "not_found",
            "detail": "App has no attached operational model to merge into",
        }
    await merge_library_manifest_into_operational_model(
        lib, attached, track=None, for_space=True
    )
    app_node.library_merge_source_id = library_operational_model_id
    await app_node.save()
    return {
        "app_id": app_id,
        "library_operational_model_id": library_operational_model_id,
        "library_profile_name": getattr(lib, "name", ""),
        "message": (
            f"Applied library Operational Model '{getattr(lib, 'name', library_operational_model_id)}' "
            f"to app_node."
        ),
    }


# ---------------------------------------------------------------------------
# Agent-authorable substrate (Pillar 3 — introspection-first authoring)
# ---------------------------------------------------------------------------
#
# These helpers pair with the new MCP tools added to ``agent_tools.py``
# (``integral_describe_substrate``, ``integral_describe_model``,
# ``integral_get_model_draft``, ``integral_propose_model_revision``,
# ``integral_diff_model_draft``, ``integral_sandbox_apply_to_draft``,
# ``integral_publish_model_draft``, ``integral_discard_model_draft``)
# and the staging executors registered for the corresponding kinds.


async def describe_substrate() -> Dict[str, Any]:
    """Return the full catalogue of registered field/view types + plugins.

    Mirrors ``GET /api/operational-model-substrate`` for in-process callers.
    Read-only; no permission gate (the catalogue is platform-scoped).
    """
    from typing import get_args

    from app.schemas.policy import PolicyAction
    from app.services import operational_model_field_types as ftr
    from app.services.operational_model_plugins import discovered_plugins
    from app.services.template_var_resolvers import get_registered_tokens
    from app.views import operational_model_view_types as vtr

    def _f(spec: Any) -> Dict[str, Any]:
        return {
            "type": spec.type,
            "base": spec.base,
            "label": spec.label,
            "description": spec.description,
            "config_schema": spec.config_schema,
            "source": spec.source,
            "signed": spec.signed,
        }

    def _v(spec: Any) -> Dict[str, Any]:
        return {
            "type": spec.type,
            "base": spec.base,
            "label": spec.label,
            "description": spec.description,
            "config_schema": spec.config_schema,
            "source": spec.source,
            "signed": spec.signed,
        }

    return {
        # ---- EXISTING keys preserved verbatim (Phase 3 + earlier) ----
        "field_types": [_f(s) for s in ftr.iter_specs()],
        "view_types": [_v(s) for s in vtr.iter_specs()],
        "plugins": discovered_plugins(),
        "registry_versions": {
            "field_types": ftr.registry_version(),
            "view_types": vtr.registry_version(),
        },
        # ---- Phase 3.1 Plan 03.1-04 additions (ANC-09) — additive only ----
        # ``relation_targets`` mirrors the locked Literal in
        # ``_normalize_field_spec`` (target ∈ {entry, track}); agents authoring
        # relation fields read this to learn the legal variants.
        "relation_targets": ["entry", "track"],
        # ``edges`` enumerates the new (and the existing REFERENCES) substrate
        # edges agents may need to reason about when composing manifests. The
        # source/target/via fields are descriptive — they orient the agent
        # towards the YAML key that materializes each edge at compile time.
        "edges": {
            "REFERENCES": {
                "source": "Entry",
                "target": "Entry",
                "via": "relation.target=entry",
            },
            "ANCHORS": {
                "source": "Entry",
                "target": "Track",
                "via": "relation.target=track",
            },
            "TEMPLATED_FROM": {
                "source": "Track",
                "target": "OperationalModel",
                "via": "auto_provision via app_node.track_templates",
            },
            # `member` field type materializes an additional pointer
            # from the source Entry to a Workspace
            # member's User account. The edge carries `field_key` so an
            # entry can declare multiple member-typed fields (assignee,
            # reviewer, etc.) without collision.
            "HAS_MEMBER_REF": {
                "source": "Entry",
                "target": "User",
                "via": "field type 'member'",
            },
        },
        # ``template_var_resolvers`` exposes the registered token vocabulary
        # so agents can author filter-rule values without guessing.
        "template_var_resolvers": get_registered_tokens(),
        # ``governance_actions`` filters the PolicyAction Literal for members
        # under the ``anchor.`` namespace; agents authoring governance Policies
        # consult this list (decision actions in the Plan 03.1-03 Policy
        # registry are exactly these names).
        "governance_actions": [
            a for a in get_args(PolicyAction) if a.startswith("anchor.")
        ],
    }


async def describe_operational_model(
    *,
    user_id: str,
    track_id: Optional[str] = None,
    app_id: Optional[str] = None,
) -> Dict[str, Any]:
    """Return the published + draft state for a track or app's CP.

    Permission filter: caller needs read access on the underlying
    track/app_node. Returns ``{ published, draft, has_draft }`` with both
    nodes serialised via ``export_node``.
    """
    from app.api.utils import export_node
    from app.models.nodes import App, OperationalModel, Track
    from app.services.app_graph import (
        get_app_attached_operational_model,
        get_track_attached_operational_model,
    )
    from app.services.permissions import can_view_app, can_view_track

    if not (track_id or app_id):
        return {"error": "bad_request", "detail": "track_id or app_id required"}
    if track_id and app_id:
        return {
            "error": "bad_request",
            "detail": "supply track_id OR app_id, not both",
        }

    if track_id:
        if not await can_view_track(user_id, track_id):
            return {"error": "forbidden", "detail": "no read access to track"}
        track = await Track.get(track_id)
        if not track:
            return {"error": "not_found", "detail": "track not found"}
        cp = await get_track_attached_operational_model(track)
    else:
        if not await can_view_app(user_id, app_id):  # type: ignore[arg-type]
            return {"error": "forbidden", "detail": "no read access to app"}
        app_node = await App.get(app_id)  # type: ignore[arg-type]
        if not app_node:
            return {"error": "not_found", "detail": "app not found"}
        cp = await get_app_attached_operational_model(app_node)

    if cp is None:
        return {
            "error": "not_found",
            "detail": "no attached operational model",
        }

    # Find sibling draft (if any).
    drafts = await OperationalModel.find(
        {
            "context.draft_of_id": cp.id,
            "context.status": "draft",
        }
    )
    return {
        "published": await export_node(cp),
        "draft": await export_node(drafts[0]) if drafts else None,
        "has_draft": bool(drafts),
    }


async def get_or_create_draft(
    *,
    user_id: str,
    operational_model_id: str,
) -> Dict[str, Any]:
    """Fetch or create a draft for ``operational_model_id``.

    If the CP is already a draft, returns it. If a sibling draft exists
    for a published CP, returns that instead of forking a second one.
    Otherwise forks a new draft via ``operational_model_atomic_swap``.

    Permission: caller must satisfy ``_resolve_cp_edit_permission`` (used
    by the REST endpoint of the same name) — replicated here so the
    in-process bridge action stays gated even when bypassing the HTTP
    surface.
    """
    from app.api.utils import export_node
    from app.models.nodes import OperationalModel

    cp = await OperationalModel.get(operational_model_id)
    if cp is None:
        return {"error": "not_found", "detail": "operational model not found"}

    if not await _user_can_edit_cp(user_id=user_id, cp=cp):
        return {"error": "forbidden", "detail": "no edit access to this profile"}

    if cp.status == "draft":
        return {
            "draft": await export_node(cp),
            "from_id": cp.draft_of_id,
            "reused": True,
        }

    drafts = await OperationalModel.find(
        {
            "context.draft_of_id": cp.id,
            "context.status": "draft",
        }
    )
    if drafts:
        return {
            "draft": await export_node(drafts[0]),
            "from_id": cp.id,
            "reused": True,
        }

    from app.services.operational_model_atomic_swap import fork_draft

    draft = await fork_draft(published=cp, actor_id=user_id)
    return {"draft": await export_node(draft), "from_id": cp.id, "reused": False}


async def apply_patch_to_draft(
    *,
    user_id: str,
    draft_id: str,
    operations: List[Dict[str, Any]],
) -> Dict[str, Any]:
    """Apply a patch DSL to a draft and persist the result.

    Pure-function ``apply_operations`` produces the new manifest, then
    ``compile_canonical_manifest`` validates the round-trip before the
    draft node is saved. On validation failure the draft is left
    untouched and an error envelope is returned.
    """
    from app.api.utils import export_node
    from app.exceptions import BadRequestError as _BadRequest
    from app.models.nodes import OperationalModel
    from app.services.agent_profile_patches import apply_operations
    from app.services.operational_model_runtime import compile_canonical_manifest

    draft = await OperationalModel.get(draft_id)
    if draft is None:
        return {"error": "not_found", "detail": "draft not found"}
    if draft.status != "draft":
        return {"error": "bad_request", "detail": "target is not a draft"}

    parent = (
        await OperationalModel.get(draft.draft_of_id) if draft.draft_of_id else None
    )
    if parent is None or not await _user_can_edit_cp(user_id=user_id, cp=parent):
        return {"error": "forbidden", "detail": "no edit access to this profile"}

    try:
        patched = apply_operations(draft.manifest or {}, operations)
        compiled = compile_canonical_manifest(manifest=patched)
    except _BadRequest as exc:
        return {"error": "bad_request", "detail": exc.message}

    draft.manifest = compiled
    draft.updated_at = datetime.now(timezone.utc).isoformat()
    await draft.save()
    return {
        "draft": await export_node(draft),
        "applied_op_count": len(operations),
    }


async def diff_draft(
    *,
    user_id: str,
    draft_id: str,
    include_entry_impact: bool = True,
    sample_limit: int = 20,
) -> Dict[str, Any]:
    """Diff a draft against its published parent + (optional) entry impact."""
    from app.models.nodes import OperationalModel
    from app.services.operational_model_diff import (
        compute_entry_impact_for_attached,
        compute_manifest_diff,
    )

    draft = await OperationalModel.get(draft_id)
    if draft is None:
        return {"error": "not_found", "detail": "draft not found"}
    if draft.status != "draft":
        return {"error": "bad_request", "detail": "target is not a draft"}
    if not draft.draft_of_id:
        return {"error": "bad_request", "detail": "draft has no published parent"}

    parent = await OperationalModel.get(draft.draft_of_id)
    if parent is None:
        return {"error": "not_found", "detail": "published parent missing"}
    if not await _user_can_edit_cp(user_id=user_id, cp=parent):
        return {"error": "forbidden", "detail": "no edit access to this profile"}

    diff = compute_manifest_diff(
        before=parent.manifest or {},
        after=draft.manifest or {},
    )
    payload: Dict[str, Any] = {
        "candidate_id": draft.id,
        "reference_id": parent.id,
        "diff": diff,
    }
    if include_entry_impact:
        payload["entry_impact"] = await compute_entry_impact_for_attached(
            cp=parent,
            candidate_manifest=draft.manifest or {},
            sample_limit=sample_limit,
        )
    return payload


async def publish_draft_for_agent(
    *,
    user_id: str,
    draft_id: str,
    run_migrations: bool = True,
    abort_on_migration_failure: bool = True,
) -> Dict[str, Any]:
    """Publish a draft (atomic swap + migrations) on behalf of the agent."""
    from app.exceptions import BadRequestError as _BadRequest
    from app.models.nodes import OperationalModel
    from app.services.operational_model_atomic_swap import publish_draft as _impl

    draft = await OperationalModel.get(draft_id)
    if draft is None:
        return {"error": "not_found", "detail": "draft not found"}
    if draft.status != "draft" or not draft.draft_of_id:
        return {"error": "bad_request", "detail": "target is not a publishable draft"}
    parent = await OperationalModel.get(draft.draft_of_id)
    if parent is None:
        return {"error": "not_found", "detail": "published parent missing"}
    if not await _user_can_edit_cp(user_id=user_id, cp=parent):
        return {"error": "forbidden", "detail": "no edit access to this profile"}
    try:
        from app.services.operational_model_runtime import compile_canonical_manifest

        # Publishing an unchanged draft is never useful and previously let a
        # failed profile-revision approval look like a successful publication.
        # Compare canonical forms so equivalent user-authored order/omission
        # does not create a meaningless version or a false success receipt.
        parent_manifest = compile_canonical_manifest(manifest=parent.manifest or {})
        draft_manifest = compile_canonical_manifest(manifest=draft.manifest or {})
        if draft_manifest == parent_manifest:
            return {
                "error": "bad_request",
                "detail": "draft has no schema changes to publish",
            }
        return await _impl(
            draft=draft,
            published=parent,
            actor_id=user_id,
            run_migrations=run_migrations,
            abort_on_migration_failure=abort_on_migration_failure,
        )
    except _BadRequest as exc:
        return {"error": "bad_request", "detail": exc.message}


async def discard_draft_for_agent(
    *,
    user_id: str,
    draft_id: str,
) -> Dict[str, Any]:
    """Discard an unpublished draft."""
    from app.models.nodes import OperationalModel
    from app.services.operational_model_atomic_swap import discard_draft as _impl

    draft = await OperationalModel.get(draft_id)
    if draft is None:
        return {"error": "not_found", "detail": "draft not found"}
    if draft.status != "draft":
        return {"error": "bad_request", "detail": "target is not a draft"}
    parent = (
        await OperationalModel.get(draft.draft_of_id) if draft.draft_of_id else None
    )
    if parent is None:
        return await _impl(draft=draft, actor_id=user_id)
    if not await _user_can_edit_cp(user_id=user_id, cp=parent):
        return {"error": "forbidden", "detail": "no edit access to this profile"}
    return await _impl(draft=draft, actor_id=user_id)


async def create_empty_library_draft(
    user_id: str,
    workspace_id: str,
    name: str,
    description: str | None = None,
    scope: str = "track",
) -> Dict[str, Any]:
    """Create a new empty library package draft ready to be filled via patch ops.

    Returns ``{ draft_id, name, scope, manifest }`` on success so the caller
    can immediately pass ``draft_id`` to ``integral_propose_model_revision``.
    """
    from app.models.nodes import (
        OPERATIONAL_MODELS_REGISTRY_ID,
        OperationalModel,
        OperationalModels,
    )
    from app.services.app_graph import ensure_catalog_edge
    from app.services.permissions import can_publish_operational_models_under_workspace

    internal_scope = "app" if scope == "space" else scope
    if internal_scope not in ("track", "app"):
        return {
            "error": "invalid_argument",
            "detail": "scope must be 'track' or 'app' (legacy: 'space' still accepted)",
        }

    if not name or not name.strip():
        return {"error": "missing_argument", "detail": "name is required"}

    if not await can_publish_operational_models_under_workspace(user_id, workspace_id):
        return {
            "error": "forbidden",
            "detail": "No permission to create library packages in this workspace",
        }

    if internal_scope == "track":
        skeleton: Dict[str, Any] = {
            "operational_model_schema_version": 2,
            "scope": "track",
            "track": {
                "entry_types": [],
                "views": [],
                "taxonomy": {"tag_groups": []},
            },
            "package": {},
            "migrations": [],
        }
    else:
        skeleton = {
            "operational_model_schema_version": 2,
            "scope": "app",
            "app": {
                "tracks": [],
                "relations": [],
            },
            "package": {},
            "migrations": [],
        }

    from app.services.operational_model_runtime import compile_canonical_manifest

    try:
        compiled = compile_canonical_manifest(
            manifest=skeleton, scope_hint=internal_scope
        )
    except Exception as exc:  # noqa: BLE001
        return {"error": "manifest_invalid", "detail": str(exc)}

    from datetime import datetime, timezone

    now = datetime.now(timezone.utc).isoformat()
    cp = await OperationalModel.create(
        name=name.strip(),
        version="1.0.0",
        manifest=compiled,
        scope=internal_scope,
        library_package=True,
        status="draft",
        description=description or "",
        workspace_id=workspace_id,
        created_at=now,
        updated_at=now,
    )

    cps_reg = await OperationalModels.get(OPERATIONAL_MODELS_REGISTRY_ID)
    if cps_reg:
        await ensure_catalog_edge(cps_reg, cp)

    return {
        "draft_id": cp.id,
        "name": cp.name,
        "scope": scope,
        "manifest": compiled,
    }


async def apply_library_to_track(
    user_id: str,
    track_id: str,
    library_cp_id: str,
) -> Dict[str, Any]:
    """Merge a published library package into a track's attached operational model.

    Returns the names of entry types and views that were present in the library
    manifest so the caller can confirm what was applied.
    """
    from app.models.nodes import OperationalModel, Track
    from app.services.app_graph import get_track_attached_operational_model
    from app.services.operational_model_merge import (
        merge_library_manifest_into_operational_model,
    )
    from app.services.permissions import resolve_role

    track = await Track.get(track_id)
    if not track:
        return {"error": "not_found", "detail": "Track not found"}

    role = await resolve_role(user_id, "track", track_id)
    # Track-attached operational model mutation is admin-tier substrate authority
    # (swapping the schema bundle the track resolves against). Editors
    # do entry CRUD only and may NOT re-anchor the track to a different
    # library Operational Model.
    if role not in ("owner", "admin"):
        return {
            "error": "forbidden",
            "detail": "admin or owner role required on track",
        }

    lib = await OperationalModel.get(library_cp_id)
    if not lib or not getattr(lib, "library_package", False):
        return {"error": "not_found", "detail": "Library operational model not found"}
    if getattr(lib, "status", None) != "published":
        return {
            "error": "bad_request",
            "detail": "Library operational model must be published before applying",
        }

    attached = await get_track_attached_operational_model(track)
    if not attached:
        return {
            "error": "not_found",
            "detail": "Track has no attached operational model",
        }

    await merge_library_manifest_into_operational_model(
        lib, attached, track, for_space=False
    )
    track.library_merge_source_id = library_cp_id
    await track.save()

    lib_manifest = getattr(lib, "manifest", {}) or {}
    track_section = lib_manifest.get("track") or {}
    merged_entry_types = [
        et.get("name", et.get("key", ""))
        for et in (track_section.get("entry_types") or [])
        if isinstance(et, dict)
    ]
    merged_views = [
        v.get("name", v.get("key", ""))
        for v in (track_section.get("views") or [])
        if isinstance(v, dict)
    ]

    return {
        "track_id": track_id,
        "library_source_id": library_cp_id,
        "merged_entry_types": merged_entry_types,
        "merged_views": merged_views,
    }


async def recommend_profile_customizations(
    user_id: str,
    track_id: str,
    entry_sample_limit: int = 20,
) -> Dict[str, Any]:
    """Analyze a track's entries and attached operational model to surface patch suggestions.

    Returns a list of concrete patch ops (each passable directly to
    ``integral_propose_model_revision``) plus summary statistics about the
    sample analyzed.
    """
    from app.models.nodes import Entry, Track
    from app.services.app_graph import get_track_attached_operational_model
    from app.services.permissions import resolve_role

    track = await Track.get(track_id)
    if not track:
        return {"error": "not_found", "detail": "Track not found"}

    role = await resolve_role(user_id, "track", track_id)
    if not role:
        return {"error": "forbidden", "detail": "no read access to track"}

    cp = await get_track_attached_operational_model(track)
    if not cp:
        return {
            "error": "not_found",
            "detail": "Track has no attached operational model",
        }

    manifest = getattr(cp, "manifest", {}) or {}
    tier = manifest.get("track") or {}
    entry_types_spec = tier.get("entry_types") or []
    views_spec = tier.get("views") or []

    all_entries = await Entry.find({"context.track_id": track_id})
    sample = all_entries[:entry_sample_limit]

    suggestions: List[Dict[str, Any]] = []

    # --- field null-rate analysis ---
    for et_spec in entry_types_spec:
        et_key = str(et_spec.get("key") or "")
        fields = et_spec.get("fields") or []
        et_entries = [
            e for e in sample if str(getattr(e, "entry_type_key", "") or "") == et_key
        ]
        if not et_entries:
            continue
        for field_spec in fields:
            fk = str(field_spec.get("key") or "")
            if not fk:
                continue
            null_count = sum(
                1 for e in et_entries if not (getattr(e, "fields", None) or {}).get(fk)
            )
            null_pct = int(null_count * 100 / len(et_entries))
            if null_pct > 80:
                suggestions.append(
                    {
                        "op": "remove_field",
                        "rationale": (
                            f"Field '{fk}' is empty in {null_pct}% of entries — "
                            "consider removing or making optional."
                        ),
                        "patch_args": {"entry_type": et_key, "field_key": fk},
                    }
                )
            if (
                null_pct == 0
                and str(field_spec.get("type") or "") == "select"
                and not field_spec.get("options")
            ):
                suggestions.append(
                    {
                        "op": "modify_field",
                        "rationale": (
                            f"Select field '{fk}' has no options configured."
                        ),
                        "patch_args": {
                            "entry_type": et_key,
                            "field_key": fk,
                            "patch": {"options": []},
                        },
                    }
                )

    # --- missing views ---
    view_types_present = {
        str(v.get("type") or v.get("view_type") or "") for v in views_spec
    }

    if sample and "table" not in view_types_present:
        suggestions.append(
            {
                "op": "add_view",
                "rationale": "Track has entries but no table view — a table view aids data review.",
                "patch_args": {
                    "spec": {"key": "table", "type": "table", "name": "Table"}
                },
            }
        )

    kanban_views = [
        v
        for v in views_spec
        if str(v.get("type") or v.get("view_type") or "")
        in ("kanban", "composable_board")
    ]
    for kv in kanban_views:
        kv_key = str(kv.get("key") or "")
        kv_config = kv.get("config") or {}
        if not kv_config.get("group_by"):
            select_field_key = next(
                (
                    str(f.get("key") or "")
                    for et_spec in entry_types_spec
                    for f in (et_spec.get("fields") or [])
                    if str(f.get("type") or "") == "select" and f.get("key")
                ),
                "status",
            )
            suggestions.append(
                {
                    "op": "modify_view",
                    "rationale": (
                        f"Kanban view '{kv_key}' has no group_by configured — "
                        "grouping by a select field enables column layout."
                    ),
                    "patch_args": {
                        "key": kv_key,
                        "patch": {"group_by": select_field_key},
                    },
                }
            )

    # --- entry type coverage ---
    if sample and len(entry_types_spec) > 1:
        used_et_keys = {str(getattr(e, "entry_type_key", "") or "") for e in sample}
        for et_spec in entry_types_spec:
            et_key = str(et_spec.get("key") or "")
            if et_key and et_key not in used_et_keys:
                suggestions.append(
                    {
                        "op": "remove_entry_type",
                        "rationale": (
                            f"No entries use entry type '{et_key}' in the sample — "
                            "consider removing it."
                        ),
                        "patch_args": {"key": et_key},
                    }
                )

    total_fields = sum(len(et.get("fields") or []) for et in entry_types_spec)

    return {
        "suggestions": suggestions,
        "entry_count_analyzed": len(sample),
        "profile_summary": {
            "entry_type_count": len(entry_types_spec),
            "view_count": len(views_spec),
            "field_count": total_fields,
        },
    }


async def _user_can_edit_cp(*, user_id: str, cp: Any) -> bool:
    """In-process port of ``_resolve_cp_edit_permission`` from the API layer."""
    from app.models.nodes import App, Track
    from app.services.permissions import (
        can_edit_app,
        can_edit_track,
        can_publish_operational_models_under_workspace,
    )

    if getattr(cp, "library_package", False):
        return await can_publish_operational_models_under_workspace(
            user_id, getattr(cp, "workspace_id", None) or ""
        )
    scope = getattr(cp, "scope", "")
    # A draft CP carries the parent's scope but is attached to NO Track/App
    # (only the published parent holds the ``attached_operational_model_id``
    # back-pointer; see fork_draft in operational_model_atomic_swap). Gating on
    # this node's id alone would find no owner and deny edit even to a user who
    # plainly owns the underlying track — the bug that made get_profile_draft
    # return "no edit access" for a workspace owner. Resolve rights via the
    # published parent too, mirroring apply_patch_to_draft / publish_draft.
    candidate_cp_ids = [cp.id]
    draft_of = getattr(cp, "draft_of_id", None)
    if draft_of:
        candidate_cp_ids.append(draft_of)
    if scope == "track":
        for cid in candidate_cp_ids:
            owners = await Track.find({"context.attached_operational_model_id": cid})
            for t in owners:  # noqa: SIM110 — short-circuit on await
                if await can_edit_track(user_id, t.id):
                    return True
        return False
    if scope == "app":
        for cid in candidate_cp_ids:
            owners = await App.find({"context.attached_operational_model_id": cid})
            for sp in owners:  # noqa: SIM110 — short-circuit on await
                if await can_edit_app(user_id, sp.id):
                    return True
        return False
    return False
