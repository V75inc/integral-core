"""Per-tool dispatch bindings: manifest name -> backing handler + param mapper.

The manifest (``backend/app/agentive/tool_manifest.yaml``, parsed by
:func:`app.agentive.tooling.manifest.load_manifest`) is the *contract*; this
module is the *wiring*. For each route-backed read tool we record:

* ``handler_ref`` — a lazy import that resolves to the ``@endpoint``-decorated
  route handler. Deferred so importing this module does not eagerly pull in
  every ``app.api`` module (and so a typo in a module/function name surfaces at
  dispatch time as an ImportError that :func:`dispatch_tool` envelopes, never a
  hard import failure of the whole tooling package).
* ``param_map`` — maps the tool's call args to the handler's keyword arguments.
  It maps **data only**. It MUST NEVER inject identity (``user_id`` /
  ``principal_id``) or any scope-widening kwarg: the acting principal and the
  bound workspace scope come solely from :func:`dispatch_tool`'s
  ``principal_id`` / ``scope`` (PC-1 / PC-2). A tool arg must not be able to
  act-as another user or read another workspace.

Route-backed reads are wired here. Task 4 completes the READ surface:

* **POST-body reads** (``integral_query`` / ``integral_search_cross_track`` over
  POST /api/retrieve) now thread the tool args through ``body_map`` into the
  stub request's ``await request.json()`` (the ``retrieve`` handler validates
  the body via ``RetrieveRequest.model_validate``). ``integral_get_related``
  (GET /api/entries/{entry_id}/related) — the route DOES exist
  (``app/api/entry_relations.py``, ``list_entry_relations``); it reads
  ``?relation=`` off ``request.query_params``, supplied here via ``query_map``.
* **SERVICE-backed reads** (``integral_describe_model`` etc.) carry a
  ``service_ref`` + ``service_param_map`` instead of a route handler;
  :func:`dispatch_tool` calls ``await fn(user_id=<principal_id>, **kwargs)``
  with the workspace scope bound via the ``current_scope_workspace_id``
  ContextVar.

* **PROPOSE stagers** (Task 5a) — ``op_class == "propose"`` tools carry a
  ``stager`` (no ``handler_ref`` / ``service_ref``). The stager maps the tool's
  call args to ``create_staged_change`` kwargs (``kind`` / ``summary`` /
  ``diff_human`` / ``diff_machine`` / ``payload``); :func:`dispatch_tool` routes
  through ``_dispatch_propose`` to mint a *pending* StagedChange — it STAGES, it
  does not APPLY. The bless endpoint later runs the matching
  ``staging_executors`` executor, so each stager's ``payload`` MUST match that
  executor's splat shape exactly. A propose tool with no stager (or an
  ``execute`` tool, of which none are wired) still returns a clean
  ``not_implemented`` / fail-closed ToolResult rather than guessing.
"""

from contextvars import ContextVar
from dataclasses import dataclass
from typing import Any, Callable, Dict, List, Optional

from app.agentive.tooling import staging_display as _sd
from app.agentive.tooling.stagers_filing import stage_file_content
from app.agentive.tooling.stagers_scheduling import (
    stage_cancel_routine,
    stage_delete_routine,
    stage_schedule_task,
    stage_update_routine,
)

# Principal bound by :func:`dispatch._dispatch_propose` for the duration of an
# async stager that needs a substrate read scoped to the acting user (e.g.
# ``create_entry`` / ``file_content`` hint resolution, which requires a
# ``user_id`` to scope its track search). This is NOT a stager arg (PC-1: a
# stager maps data only and never receives identity through its signature); the
# dispatcher sets it from its own ``principal_id`` and resets it in ``finally``,
# mirroring how it binds workspace scope via ``current_scope_workspace_id``.
# Default None so a stager run outside dispatch fails closed.
_propose_principal: "ContextVar[Optional[str]]" = ContextVar(
    "tooling_propose_principal", default=None
)

# Session id bound by :func:`dispatch._dispatch_propose` for the duration of an
# async stager that needs to resolve the live ChatThread it's running in (e.g.
# ``integral_schedule_task``, which must bind the new RoutineTask to the
# thread it should post into). Mirrors ``_propose_principal`` — a stager must
# not receive this through its own signature (PC-1: data only); the
# dispatcher binds it from its own ``session_id`` and resets it in
# ``finally``. Default None: MCP / consent dispatch surfaces omit session_id,
# so a stager that requires a thread binding must fail closed when unset.
_propose_session_id: "ContextVar[Optional[str]]" = ContextVar(
    "tooling_propose_session_id", default=None
)


@dataclass
class ToolBinding:
    """Wiring for one manifest tool.

    Two dispatch flavors share this record:

    * **Route-backed reads** — ``handler_ref`` resolves (lazily) to a route
      handler called as ``await handler(request, **kwargs)``. The three
      arg-mapping callables each target a DISTINCT seam of the synthesized
      request, and each maps **data only** (never identity/scope):

      - ``param_map`` → the handler ``**kwargs`` (path params, FastAPI Query
        args the handler takes by name).
      - ``body_map`` → ``await request.json()`` (POST-body reads whose handler
        parses the body itself, e.g. ``retrieve`` validating
        ``RetrieveRequest``).
      - ``query_map`` → ``request.query_params`` (reads that pull a param off
        the query string, e.g. ``list_entry_relations`` reading ``?relation=``).

    * **SERVICE-backed reads** — ``service_ref`` resolves (lazily) to a service
      FUNCTION called as ``await fn(user_id=<principal_id>, **service_kwargs)``.
      ``service_param_map`` maps the tool's args to the NON-IDENTITY service
      kwargs only. The service's ``user_id`` is ALWAYS the dispatch
      ``principal_id`` (injected by :func:`dispatch_tool`, never from args), and
      workspace scope is bound by the dispatcher via the ``current_scope_
      workspace_id`` ContextVar around the call — neither may come from args.

    A ``None`` ``handler_ref`` AND ``None`` ``service_ref`` means "binding
    pending" (propose/execute tools land later); :func:`dispatch_tool` returns a
    clean ``not_implemented`` ToolResult rather than guessing.
    """

    handler_ref: Optional[Callable[[], Any]] = None
    param_map: Optional[Callable[[Dict[str, Any]], Dict[str, Any]]] = None
    # body_map / query_map target distinct request seams (POST body / query
    # string) for route-backed reads; both map data only.
    body_map: Optional[Callable[[Dict[str, Any]], Dict[str, Any]]] = None
    query_map: Optional[Callable[[Dict[str, Any]], Dict[str, Any]]] = None
    # SERVICE-backed read: service_ref is a lazy import of the service fn;
    # service_param_map maps tool args -> NON-identity service kwargs only.
    service_ref: Optional[Callable[[], Any]] = None
    service_param_map: Optional[Callable[[Dict[str, Any]], Dict[str, Any]]] = None
    # PROPOSE staging: ``stager`` maps the tool's call args to a dict of
    # ``create_staged_change`` kwargs — ``{"kind", "summary", "diff_human",
    # "diff_machine", "payload"}``. It returns ``kind`` itself so the stager is
    # the single source of the staged-change shape (and so ``modify_operational_model.*``
    # can compute its sub-kind from the args' ``action``). Like the read maps it
    # carries DATA ONLY: it MUST NOT inject ``user_id`` (the staged change's
    # ``user_id`` is the dispatch ``principal_id``, supplied by
    # :func:`dispatch_tool`) nor a scope-widening value (workspace scope is bound
    # by the dispatcher via the ``current_scope_workspace_id`` ContextVar). A
    # stager may be SYNC (the common case — it maps the args it was handed) OR
    # ASYNC when a faithful diff/payload genuinely needs a substrate read under
    # the bound scope (e.g. ``create_entry`` resolving freeform text to a
    # track/title via hints); :func:`dispatch_tool` awaits a coroutine
    # result. The ``payload`` it produces MUST match exactly what the registered
    # ``staging_executors`` executor for ``kind`` splats into its route handler.
    stager: Optional[Callable[[Dict[str, Any]], Any]] = None
    # DIRECT-EXECUTE: an EPHEMERAL ``propose``-classified tool whose effect is
    # conversation/session state — NOT a substrate mutation — runs IMMEDIATELY
    # rather than minting a StagedChange (there is nothing to bless; PC-8
    # stage-substrate-mutations-for-bless does not apply to ephemeral context
    # ops). ``direct_ref`` is a lazy import of an async service fn called as
    # ``await fn(user_id=<principal_id>, **direct_param_map(args))`` with the
    # workspace scope bound via the ``current_scope_workspace_id`` ContextVar
    # (the same seam SERVICE-backed reads use). Identity is the dispatch
    # ``principal_id`` (never an arg); ``direct_param_map`` carries NON-identity
    # data only. ``integral_set_focus`` is the sole user today. A ``propose``
    # tool with a ``direct_ref`` MUST stay in
    # ``manifest._STAGING_EXEMPT_PROPOSE_TOOLS`` (it never mints a StagedChange).
    direct_ref: Optional[Callable[[], Any]] = None
    direct_param_map: Optional[Callable[[Dict[str, Any]], Dict[str, Any]]] = None


def _h(modpath: str, name: str) -> Callable[[], Any]:
    """Build a lazy importer that resolves ``modpath.name`` on first call."""

    def _load() -> Any:
        import importlib

        return getattr(importlib.import_module(modpath), name)

    return _load


# --------------------------------------------------------------------------- #
# param_map helpers
# --------------------------------------------------------------------------- #
def _passthrough(args: Dict[str, Any]) -> Dict[str, Any]:
    """Forward all tool args verbatim as handler kwargs (data only)."""
    return dict(args or {})


def _pick(*keys: str) -> Callable[[Dict[str, Any]], Dict[str, Any]]:
    """Forward only ``keys`` present in args (drop unknown extras)."""

    def _mapper(args: Dict[str, Any]) -> Dict[str, Any]:
        src = args or {}
        return {k: src[k] for k in keys if k in src}

    # Recorded so the dispatcher can tell whether a binding will actually
    # forward an arg the model supplied. Dropping a workspace-targeting arg in
    # silence is what let the resident believe its filter had been applied.
    _mapper.picked_keys = frozenset(keys)  # type: ignore[attr-defined]
    return _mapper


# --------------------------------------------------------------------------- #
# Polymorphic resource dispatchers (get_access / list_share_links)
# --------------------------------------------------------------------------- #
# These manifest tools address App|Track|Entry via a ``resource_type`` arg, but
# the substrate exposes a SEPARATE handler per resource type, each taking the
# path param under its own name. The lazy ref returns a thin async dispatcher
# matching the ``handler(request, **kwargs)`` contract; the param_map supplies
# ``resource_type`` + ``id``.
_ACCESS_HANDLERS = {
    "apps": ("app.api.access", "get_space_access", "app_id"),
    "tracks": ("app.api.access", "get_track_access", "track_id"),
    "entries": ("app.api.access", "get_entry_access", "entry_id"),
}

_SHARE_LINK_HANDLERS = {
    "apps": ("app.api.shares", "list_space_links", "app_id"),
    "tracks": ("app.api.shares", "list_track_links", "track_id"),
    "entries": ("app.api.shares", "list_entry_links", "entry_id"),
}


def _polymorphic_resource_dispatcher(
    routes: Dict[str, Any],
) -> Callable[[], Any]:
    """Return a lazy ref to a ``handler(request, resource_type, id)`` dispatcher.

    ``routes`` maps ``resource_type`` -> (modpath, handler_name, path_kwarg).
    The dispatcher routes to the per-resource handler, translating the generic
    ``id`` into that handler's path-param name (``app_id`` / ``track_id`` /
    ``entry_id``). An unknown ``resource_type`` raises ``ValueError``, which
    :func:`dispatch_tool` envelopes fail-closed.
    """

    def _load() -> Any:
        import importlib

        async def _dispatch(request: Any, *, resource_type: str, id: str) -> Any:
            route = routes.get(resource_type)
            if route is None:
                raise ValueError(
                    f"resource_type must be one of {sorted(routes)}, "
                    f"got {resource_type!r}"
                )
            modpath, handler_name, path_kwarg = route
            handler = getattr(importlib.import_module(modpath), handler_name)
            return await handler(request, **{path_kwarg: id})

        return _dispatch

    return _load


def _resource_id_param_map(args: Dict[str, Any]) -> Dict[str, Any]:
    """Map polymorphic-resource args to the dispatcher's ``resource_type`` + ``id``."""
    src = args or {}
    return {"resource_type": src.get("resource_type"), "id": src.get("id")}


# --------------------------------------------------------------------------- #
# POST-body + query-string mappers (Part A)
# --------------------------------------------------------------------------- #
def _retrieve_body_map(args: Dict[str, Any]) -> Dict[str, Any]:
    """Pass the tool args through as the ``/api/retrieve`` JSON body.

    The ``retrieve`` handler validates the body via
    ``RetrieveRequest.model_validate`` (``extra='forbid'``), so this maps the
    caller's data args verbatim — ``query`` (required) plus optional ``mode`` /
    ``scope`` / ``top_n`` / ``filters`` / ``k``. Data only: the body is NOT an
    identity carrier (auth comes from ``request.state.user``) and ``scope`` here
    is the retrieval ``track:<id>`` pre-filter, NOT the workspace access gate
    (the workspace gate is the dispatch ``scope`` bound as ``X-Integral-Scope``).
    """
    return dict(args or {})


def _relation_query_map(args: Dict[str, Any]) -> Dict[str, Any]:
    """Map ``integral_get_related``'s ``relation`` arg onto the query string.

    ``list_entry_relations`` reads ``request.query_params.get("relation")``;
    surface it here so the in-process stub exposes ``?relation=<field_key>``.
    """
    src = args or {}
    out: Dict[str, Any] = {}
    if src.get("relation") is not None:
        out["relation"] = src["relation"]
    return out


# --------------------------------------------------------------------------- #
# SERVICE-backed read mappers (Part B)
# --------------------------------------------------------------------------- #
def _describe_operational_model_service_map(args: Dict[str, Any]) -> Dict[str, Any]:
    """Map describe-profile args to ``describe_operational_model`` kwargs (non-identity).

    The manifest declares ``track_id`` / ``space_id``; the service fn takes
    ``track_id`` / ``app_id`` (App is the substrate's "space"). Forward only the
    keys present so the fn's "exactly one of track_id|app_id" guard fires
    correctly. ``space_id`` is accepted as an alias for ``app_id``.
    """
    src = args or {}
    out: Dict[str, Any] = {}
    if src.get("track_id") is not None:
        out["track_id"] = src["track_id"]
    app_id = src.get("app_id", src.get("space_id"))
    if app_id is not None:
        out["app_id"] = app_id
    return out


# --------------------------------------------------------------------------- #
# PROPOSE stagers (Task 5a)
# --------------------------------------------------------------------------- #
# Each stager maps the tool's call args -> a dict of ``create_staged_change``
# kwargs ``{"kind", "summary", "diff_human", "diff_machine", "payload"}``. The
# ``payload`` MUST match exactly the dict the registered ``staging_executors``
# executor for that ``kind`` splats into its route handler/service fn — the
# stager is therefore the single source of the staged-change shape. The diff
# text/machine fields are ported faithfully from the existing staged-change
# construction in the resident surface (``integral_tools.py``'s ``_prepare_*``
# and ``staging_executors._x_*``), kept simpler where a substrate read would be
# the only way to enrich the human label (those reads land in the executor /
# T5b refinement, not here — a wrong enrichment is worse than a plain label).
#
# Stagers map DATA ONLY: no ``user_id`` (identity is the dispatch
# ``principal_id``), no scope-widening value (scope is bound by the dispatcher).


def _truncate(value: Any, limit: int = 120) -> str:
    """Render a scalar for a human diff, truncating long strings."""
    text = "" if value is None else str(value)
    return text if len(text) <= limit else text[: limit - 1] + "…"


# ---- entries -------------------------------------------------------------- #
async def _find_visible_entry_with_title(
    *, user_id: str, track_id: str, title: str
) -> Optional[Any]:
    """Find an exact visible title match before an agent stages a create."""
    from app.services.permissions import get_user_accessible_entries

    target = title.strip().casefold()
    if not target:
        return None
    for entry in await get_user_accessible_entries(user_id, track_id):
        if str(getattr(entry, "title", "") or "").strip().casefold() == target:
            return entry
    return None


async def _stage_create_entry(args: Dict[str, Any]) -> Dict[str, Any]:
    """Stage a ``create_entry``.

    Requires explicit ``track_id`` and ``title`` (from schema introspection).
    Freeform ``text`` alone is not classified — use ``integral_file_content``
    when the user provides unstructured content to file.
    """
    src = args or {}
    track_id = src.get("track_id")
    title = (src.get("title") or "").strip()
    body = src.get("body")
    fields = dict(src.get("fields") or {}) if src.get("fields") else {}
    tags = src.get("tags")
    entry_type = src.get("entry_type") or src.get("type_hint")
    text = src.get("text")
    view_id = src.get("view_id")
    view_hint = src.get("view_hint")
    focused_view_id = src.get("focused_view_id")
    kanban_stage_hint = src.get("kanban_stage_hint") or src.get("column_hint")

    from app.services.agent_scope import (
        current_focused_track_id,
        current_focused_view_id,
    )

    if not src.get("focused_track_id") and current_focused_track_id.get():
        src = {**src, "focused_track_id": current_focused_track_id.get()}
    if not focused_view_id and current_focused_view_id.get():
        focused_view_id = current_focused_view_id.get()

    if not track_id:
        focused_track_id = src.get("focused_track_id")
        track_hint = src.get("track_hint")
        if track_hint or focused_track_id:
            from app.services.filing_resolution import resolve_track_for_filing

            track = await resolve_track_for_filing(
                _bound_propose_principal(),
                track_hint=track_hint,
                focused_track_id=focused_track_id,
            )
            if track:
                track_id = track.id
            elif focused_track_id:
                track_id = focused_track_id

    if not title and text:
        title = text.strip().splitlines()[0][:80] if text.strip() else ""

    if not track_id:
        raise ValueError(
            "create_entry: track_id (or resolvable track_hint) is required; "
            "call integral_list_tracks first"
        )
    if not title:
        raise ValueError("create_entry: title is required")

    # A named record normally signals an update request when it already exists.
    # Refuse the duplicate card before it reaches the user: a false create is
    # harder to repair than a clear instruction to resolve and update the
    # existing entry. Callers intentionally modelling repeated same-title
    # records retain an explicit escape hatch.
    if not src.get("allow_duplicate_title"):
        existing = await _find_visible_entry_with_title(
            user_id=_bound_propose_principal(), track_id=track_id, title=title
        )
        if existing is not None:
            raise ValueError(
                "create_entry: an entry named %r already exists in this track "
                "(entry_id=%s). Use integral_update_entry with that entry_id, "
                "then read it back; do not create a duplicate." % (title, existing.id)
            )

    view_warnings: List[str] = []
    resolved_view_name = ""
    if view_id or view_hint or focused_view_id:
        from app.models.nodes import Track
        from app.services.view_create_resolution import (
            load_entry_types_for_track,
            resolve_create_params_for_view,
            resolve_view_for_filing,
        )

        track_node = await Track.get(track_id)
        if not track_node:
            raise ValueError(f"create_entry: track {track_id!r} not found")
        view = await resolve_view_for_filing(
            track_node,
            view_id=view_id,
            view_hint=view_hint,
            focused_view_id=focused_view_id,
        )
        explicit_view_requested = bool(view_id or view_hint)
        if not view and explicit_view_requested:
            from app.services.view_create_resolution import _list_views_for_track

            names = [
                getattr(v, "name", "") or v.id
                for v in await _list_views_for_track(track_node)
            ]
            raise ValueError(
                "create_entry: could not resolve view — call integral_get_track_schema "
                f"and pass view_id or view_hint. Known views: {', '.join(names) or '(none)'}"
            )
        if view:
            entry_types = await load_entry_types_for_track(track_node)
            resolution = await resolve_create_params_for_view(
                track_node,
                view,
                entry_types,
                agent_entry_type=entry_type,
                agent_fields=fields or None,
                text=text,
                kanban_stage_hint=kanban_stage_hint,
            )
            if not entry_type and resolution.entry_type:
                entry_type = resolution.entry_type
            if resolution.fields:
                merged = dict(resolution.fields)
                merged.update(fields)
                fields = merged
            view_warnings = resolution.warnings
            resolved_view_name = resolution.view_name

    payload: Dict[str, Any] = {"track_id": track_id, "title": title}
    if body:
        payload["body"] = body
    if fields:
        payload["fields"] = fields
    if tags:
        payload["tags"] = tags
    if entry_type:
        payload["entry_type"] = entry_type

    track_lbl = await _sd.resolve_track_label(track_id)
    lines = [f"**Create entry** *{title}*", "", f"- **Track:** {track_lbl}"]
    if resolved_view_name:
        lines.append(f"- **View:** {resolved_view_name}")
    if entry_type:
        lines.append(f"- **Type:** {entry_type}")
    if fields:
        field_bits = ", ".join(f"`{k}`" for k in sorted(fields.keys()))
        if field_bits:
            lines.append(f"- **Fields:** {field_bits}")
    for w in view_warnings:
        lines.append(f"- **Note:** {w}")
    if body:
        lines.append("")
        lines.append(f"> {_truncate(body, 160)}")
    return {
        "kind": "create_entry",
        "summary": f"Create entry “{title}” in {track_lbl}",
        "diff_human": "\n".join(lines),
        "diff_machine": {"op": "create_entry", **payload},
        "payload": payload,
    }


def _bound_propose_principal() -> str:
    """Return the acting principal bound by ``_dispatch_propose`` for this stager.

    Filing resolution needs a ``user_id`` to scope track lookup, but the stager
    must NOT inject identity through its own signature (PC-1: a stager maps data
    only). Instead, ``_dispatch_propose`` binds the acting principal for the
    duration of an async stager via the ``_propose_principal`` ContextVar (the
    same window in which it binds workspace scope); this reads that bound
    principal back. If unset (a stager run outside dispatch), this raises —
    fail-closed.
    """
    pid = _propose_principal.get()
    if not pid:
        raise ValueError("propose stager: no bound principal")
    return pid


def _bound_propose_session_id() -> Optional[str]:
    """Return the session id bound by ``_dispatch_propose`` for this stager, if any.

    See ``_propose_session_id`` — unlike ``_bound_propose_principal`` this does
    NOT raise when unset; callers that require a thread binding (e.g.
    ``stage_schedule_task``) raise their own domain-specific error instead.
    """
    return _propose_session_id.get()


_ENTRY_UPDATE_SCALARS = ("title", "body", "entry_type", "status", "tags")


async def _stage_update_entry(args: Dict[str, Any]) -> Dict[str, Any]:
    """Stage an ``update_entry``.

    ``_x_update_entry`` splats ``{entry_id, title?, body?, fields?, tags?,
    entry_type?, status?}``. The manifest tool nests changes under ``updates``;
    accept both the nested form and top-level keys. ``status`` is forwarded as a
    top-level key: the ``update_entry`` handler accepts it as a first-class
    kwarg (``status: Optional[str]``) and ``_x_update_entry`` passes it through,
    so a proposed status change carries into the executor verbatim.
    """
    src = dict(args or {})
    updates = src.get("updates")
    if isinstance(updates, dict):
        merged = {**updates}
    else:
        merged = {k: v for k, v in src.items() if k != "entry_id"}
    entry_id = src.get("entry_id")
    if not entry_id:
        raise ValueError("update_entry: entry_id is required")

    payload: Dict[str, Any] = {"entry_id": entry_id}
    for key in ("title", "body", "fields", "tags", "entry_type", "status"):
        if merged.get(key) is not None:
            payload[key] = merged[key]
    if len(payload) == 1:
        raise ValueError("update_entry: supply at least one field to change")

    current = await _sd.load_entry_record(entry_id)
    if current:
        for revision_key in ("record_revision", "schema_revision"):
            revision = current.get(revision_key)
            if isinstance(revision, int) and revision >= 1:
                payload[f"expected_{revision_key}"] = revision

    # ``status`` is both a platform lifecycle attribute and a common profile
    # field. An existing typed value makes the user's intent unambiguous.
    current_fields = (
        (current or {}).get("custom_fields") or (current or {}).get("fields") or {}
    )
    if (
        "status" in payload
        and isinstance(current_fields, dict)
        and "status" in current_fields
    ):
        fields = dict(payload.get("fields") or {})
        fields.setdefault("status", payload.pop("status"))
        payload["fields"] = fields
    title_lbl = (
        _sd.entry_display_label(current, entry_id)
        if current
        else f"Entry {_sd.short_node_id(entry_id)}"
    )

    label_sources: Dict[str, Any] = {}
    if "tags" in payload:
        label_sources["tags"] = payload["tags"]
    if "fields" in payload and isinstance(payload["fields"], dict):
        label_sources.update(payload["fields"])
    tag_names, entry_names, track_names = (
        await _sd.resolve_node_labels_for_diff(label_sources)
        if label_sources
        else ({}, {}, {})
    )

    lines = [f"**Update entry** *{title_lbl}*", ""]
    for key in _ENTRY_UPDATE_SCALARS:
        if key not in payload:
            continue
        lines.append(
            await _sd.format_scalar_change_line(
                key,
                payload[key],
                (current or {}).get(key),
                tag_names=tag_names,
                entry_names=entry_names,
                track_names=track_names,
            )
        )
    if "fields" in payload and isinstance(payload["fields"], dict):
        rendered = await _sd.format_fields_patch_for_diff(payload["fields"])
        lines.append(f"- **fields:** {rendered}")

    return {
        "kind": "update_entry",
        "summary": f"Update entry “{title_lbl}”",
        "diff_human": "\n".join(lines),
        "diff_machine": {"op": "update_entry", **payload},
        "payload": payload,
    }


async def _stage_delete_entry(args: Dict[str, Any]) -> Dict[str, Any]:
    """Stage a ``delete_entry`` — ``_x_delete_entry`` splats ``{entry_id}``.

    Soft-delete semantics (status=deleted; I-RET-03) — the executor routes
    through the ``delete_entry`` handler on bless. Data only (PC-1): no identity
    key reaches the payload.
    """
    src = args or {}
    entry_id = src.get("entry_id")
    if not entry_id:
        raise ValueError("delete_entry: entry_id is required")
    payload = {"entry_id": entry_id}

    current = await _sd.load_entry_record(entry_id)
    title_lbl = (
        _sd.entry_display_label(current, entry_id)
        if current
        else f"Entry {_sd.short_node_id(entry_id)}"
    )

    return {
        "kind": "delete_entry",
        "summary": f"Delete entry “{title_lbl}”",
        "diff_human": (
            f"**Delete entry** *{title_lbl}*\n\n"
            f"Soft-deletes the entry (status=deleted). Reversible by an admin."
        ),
        "diff_machine": {"op": "delete_entry", **payload},
        "payload": payload,
    }


# ---- comments ------------------------------------------------------------- #
def _stage_add_comment(args: Dict[str, Any]) -> Dict[str, Any]:
    """Stage an ``add_comment``.

    ``_x_add_comment`` splats ``{entry_id, body, parent_id?}`` into the
    ``create_comment`` route handler (which names the comment text ``text``).
    The manifest tool declares ``entry_id`` + ``text``; the legacy MCP surface
    also accepted ``parent_id`` for threaded replies. Accept ``body`` or
    ``text`` for the comment content and forward ``parent_id`` when present.
    Data only — no identity injection (the comment's author is the dispatch
    principal, set inside the handler from ``request.state.user``).
    """
    src = args or {}
    entry_id = src.get("entry_id")
    text = (src.get("text") or src.get("body") or "").strip()
    if not entry_id:
        raise ValueError("add_comment: entry_id is required")
    if not text:
        raise ValueError("add_comment: text is required")

    payload: Dict[str, Any] = {"entry_id": entry_id, "body": text}
    if src.get("parent_id"):
        payload["parent_id"] = src["parent_id"]

    return {
        "kind": "add_comment",
        "summary": f"Comment on entry {entry_id}",
        "diff_human": (
            f"**Add comment** to entry `{entry_id}`"
            + (
                f" (reply to `{payload['parent_id']}`)"
                if payload.get("parent_id")
                else ""
            )
            + f"\n\n> {_truncate(text, 200)}"
        ),
        "diff_machine": {"op": "add_comment", **payload},
        "payload": payload,
    }


# ---- apps ----------------------------------------------------------------- #
def _stage_create_app(args: Dict[str, Any]) -> Dict[str, Any]:
    """Stage a ``create_app``.

    ``_x_create_app`` splats ``{name, description?, visibility?, type_hint?}``
    into the ``create_app`` route handler. The manifest tool declares ``name``
    + ``description`` + ``icon``; the legacy branch also accepted ``visibility``
    + ``type_hint``. ``icon`` is NOT a ``create_app`` handler kwarg, so it is
    dropped (not smuggled through). ``workspace_id`` is intentionally NOT in the
    payload: the executor lets the handler resolve the active scope (PC-2 — scope
    is bound by the dispatcher, never a tool arg).
    """
    src = args or {}
    name = (src.get("name") or src.get("title") or "").strip()
    if not name:
        raise ValueError("create_app: name is required")

    payload: Dict[str, Any] = {"name": name}
    if src.get("description"):
        payload["description"] = src["description"]
    if src.get("visibility"):
        payload["visibility"] = src["visibility"]
    if src.get("type_hint"):
        payload["type_hint"] = src["type_hint"]

    lines = [f"**Create app** *{name}*"]
    if payload.get("description"):
        lines.append("")
        lines.append(f"> {_truncate(payload['description'], 160)}")
    return {
        "kind": "create_app",
        "summary": f"Create app “{name}”",
        "diff_human": "\n".join(lines),
        "diff_machine": {"op": "create_app", **payload},
        "payload": payload,
    }


# ---- tracks --------------------------------------------------------------- #
def _stage_create_track(args: Dict[str, Any]) -> Dict[str, Any]:
    """Stage a ``create_track``.

    ``_x_create_track`` splats ``{title, visibility?, app_id?, description?}``.
    The manifest tool names the title ``name``; accept ``title`` or ``name``.
    """
    src = args or {}
    title = (src.get("title") or src.get("name") or "").strip()
    if not title:
        raise ValueError("create_track: title is required")
    visibility = src.get("visibility") or "private"
    app_id = src.get("app_id") or src.get("space_id") or None
    description = src.get("description") or src.get("purpose") or None

    payload: Dict[str, Any] = {"title": title, "visibility": visibility}
    if app_id:
        payload["app_id"] = app_id
    if description:
        payload["description"] = description
    # Inline entry types (with fields) — same {name, icon?, fields:[…]} shape as
    # integral_author_model. Materialized onto the new track so its "+New"
    # form shows the declared fields (June 29 QA #4).
    entry_types = src.get("entry_types")
    if isinstance(entry_types, list) and entry_types:
        from app.services.operational_model_authoring import validate_inline_entry_types

        validate_inline_entry_types(entry_types)
        payload["entry_types"] = entry_types

    lines = [
        f"**Create track** *{title}*",
        "",
        f"- **Visibility:** `{visibility}`",
        f"- **App:** {app_id or '(no app)'}",
    ]
    if payload.get("entry_types"):
        type_names = [
            str((et or {}).get("name") or (et or {}).get("key") or "").strip()
            for et in payload["entry_types"]
        ]
        type_names = [n for n in type_names if n]
        if type_names:
            lines.append(f"- **Entry types:** {', '.join(type_names)}")
    if description:
        lines.append("")
        lines.append(f"> {_truncate(description, 160)}")
    return {
        "kind": "create_track",
        "summary": f"Create track “{title}”",
        "diff_human": "\n".join(lines),
        "diff_machine": {"op": "create_track", **payload},
        "payload": payload,
    }


def _normalize_in_batch_app_id(app_id: str) -> str:
    """Coerce a model-supplied app reference into an intra-batch token.

    Models often pass the app *name* ("Car Rental Management") or a nonsense
    placeholder ("pending") instead of ``{{app.id}}``. At execute time those
    strings are not ids → policy returns "Cannot add a track to this app" and
    the shell app stays empty. Leave real node ids and already-tokenized refs
    alone; rewrite everything else to positional ``{{app.id}}`` (the app
    created earlier in this batch). Named refs are unnecessary for the common
    one-app greenfield scaffold.
    """
    raw = (app_id or "").strip()
    if not raw:
        return "{{app.id}}"
    if raw.startswith("{{") and raw.endswith("}}"):
        # Model sometimes emits {{app.id:pending}} after a bad coerce — collapse
        # garbage named refs to positional.
        inner = raw[2:-2].strip()
        if inner.startswith("app.id:") or inner.startswith("app_id:"):
            name = inner.split(":", 1)[1].strip().lower()
            if name in {
                "pending",
                "null",
                "none",
                "undefined",
                "tbd",
                "todo",
                "new",
                "app",
                "",
            }:
                return "{{app.id}}"
        return raw
    # jvspatial node ids look like ``n.WorkspaceApp.…`` / ``n.App.…``
    if raw.startswith("n.") and "." in raw[2:]:
        return raw
    return "{{app.id}}"


def _stage_create_app_track(args: Dict[str, Any]) -> Dict[str, Any]:
    """Stage a ``create_track`` inside a specific app (app_id required)."""
    src = args or {}
    app_id = src.get("app_id") or src.get("space_id")
    if not app_id:
        raise ValueError("create_app_track: app_id is required")
    app_id = _normalize_in_batch_app_id(str(app_id))
    staged = _stage_create_track({**src, "app_id": app_id})
    # create_app_track always carries the app_id; app label resolved in async wrapper.
    staged["_app_id_for_summary"] = app_id
    return staged


async def _stage_create_app_track_async(args: Dict[str, Any]) -> Dict[str, Any]:
    staged = _stage_create_app_track(args)
    app_id = staged.pop("_app_id_for_summary", None)
    if app_id:
        app_lbl = await _sd.resolve_app_label(app_id)
        staged["summary"] = f"{staged['summary']} (in app {app_lbl})"
        if staged.get("diff_human"):
            staged["diff_human"] = staged["diff_human"].replace(
                f"- **App:** {app_id}",
                f"- **App:** {app_lbl}",
            )
    return staged


# Track metadata keys the ``update_track`` route handler accepts (and that
# ``_x_update_track`` forwards by splatting ``{track_id, **fields}``). The
# manifest tool nests changes under ``updates``; the stager FLATTENS them and
# allowlists to these handler-recognized keys so an unknown / smuggled key (e.g.
# ``user_id``) can never reach the staged payload (PC-1 — identity is the
# dispatch principal, never a tool arg). NOTE: the handler's purpose field is
# named ``purpose``, not ``description``; both ``purpose`` and a ``description``
# alias are accepted and forwarded as ``purpose``.
_UPDATE_TRACK_PARAM_KEYS = ("title", "purpose", "icon", "visibility", "accent_color")
_TRACK_UPDATE_SCALARS = _UPDATE_TRACK_PARAM_KEYS


async def _stage_update_track(args: Dict[str, Any]) -> Dict[str, Any]:
    """Stage an ``update_track``.

    ``_x_update_track`` splats ``{track_id, **fields}`` into the ``update_track``
    route handler (kwargs ``title`` / ``purpose`` / ``icon`` / ``visibility`` /
    ``accent_color``). The manifest tool nests the changes under an ``updates``
    object; FLATTEN those into top-level payload keys (mirroring
    ``_stage_update_entry``) and ALLOWLIST to the handler's params so a smuggled
    key never reaches the executor (PC-1). A ``description`` alias maps to the
    handler's ``purpose``. Fail closed when no recognized field is supplied.
    """
    src = dict(args or {})
    track_id = src.get("track_id")
    if not track_id:
        raise ValueError("update_track: track_id is required")
    upd = src.get("updates")
    merged = (
        dict(upd)
        if isinstance(upd, dict)
        else {k: v for k, v in src.items() if k != "track_id"}
    )
    # ``description`` is the agent-friendly name for the handler's ``purpose``.
    if "purpose" not in merged and merged.get("description") is not None:
        merged["purpose"] = merged["description"]

    payload: Dict[str, Any] = {"track_id": track_id}
    for key in _UPDATE_TRACK_PARAM_KEYS:
        if merged.get(key) is not None:
            payload[key] = merged[key]
    if len(payload) == 1:
        raise ValueError("update_track: supply at least one field to change")

    current = await _sd.load_track_record(track_id)
    track_lbl = (
        _sd.track_display_label(current, track_id)
        if current
        else f"Track {_sd.short_node_id(track_id)}"
    )

    lines = [f"**Update track** *{track_lbl}*", ""]
    for key in _TRACK_UPDATE_SCALARS:
        if key not in payload:
            continue
        lines.append(
            await _sd.format_scalar_change_line(
                key,
                payload[key],
                (current or {}).get(key),
            )
        )

    return {
        "kind": "update_track",
        "summary": f"Update track “{track_lbl}”",
        "diff_human": "\n".join(lines),
        "diff_machine": {"op": "update_track", **payload},
        "payload": payload,
    }


async def _stage_delete_track(args: Dict[str, Any]) -> Dict[str, Any]:
    """Stage a ``delete_track`` — ``_x_delete_track`` splats ``{track_id}``."""
    src = args or {}
    track_id = src.get("track_id")
    if not track_id:
        raise ValueError("delete_track: track_id is required")
    payload = {"track_id": track_id}

    current = await _sd.load_track_record(track_id)
    track_lbl = (
        _sd.track_display_label(current, track_id)
        if current
        else f"Track {_sd.short_node_id(track_id)}"
    )
    entry_count = (current or {}).get("entry_count")
    if isinstance(entry_count, int):
        cascade_line = (
            f"Removes the track and all **{entry_count}** entr"
            f"{'y' if entry_count == 1 else 'ies'} it contains, plus any "
            f"comments and attachments on those entries. Irreversible once blessed."
        )
    else:
        cascade_line = (
            "Removes the track and **every entry it contains**, plus any comments "
            "and attachments on those entries. Irreversible once blessed."
        )

    return {
        "kind": "delete_track",
        "summary": f"Delete track “{track_lbl}”",
        "diff_human": f"**Delete track** *{track_lbl}*\n\n{cascade_line}",
        "diff_machine": {"op": "delete_track", **payload},
        "payload": payload,
    }


# ---- profiles ------------------------------------------------------------- #
async def _stage_apply_library_operational_model(
    args: Dict[str, Any],
) -> Dict[str, Any]:
    """Stage an ``apply_library_operational_model``.

    ``_x_apply_library_operational_model`` splats ``{library_operational_model_id, track_id?,
    app_id?}``. The manifest tool (``integral_apply_model_to_track``) names
    the library id ``model_template_id``; the MCP surface uses ``library_cp_id``;
    the resident prepare uses ``library_operational_model_id``. Accept all three and emit
    the executor's expected ``library_operational_model_id`` key.
    """
    src = args or {}
    lib_id = (
        src.get("library_operational_model_id")
        or src.get("library_cp_id")
        or src.get("model_template_id")
    )
    if not lib_id:
        raise ValueError(
            "apply_library_operational_model: a library Operational Model id is required"
        )
    track_id = src.get("track_id") or None
    app_id = src.get("app_id") or src.get("space_id") or None
    if not track_id and not app_id:
        raise ValueError(
            "apply_library_operational_model: track_id or app_id is required"
        )

    payload: Dict[str, Any] = {"library_operational_model_id": lib_id}
    if track_id:
        payload["track_id"] = track_id
    if app_id:
        payload["app_id"] = app_id
    container = await _sd.resolve_container_label(track_id=track_id, app_id=app_id)
    return {
        "kind": "apply_library_operational_model",
        "summary": f"Apply library Operational Model {lib_id} to {container}",
        "diff_human": (
            f"**Apply library Operational Model** `{lib_id}` to **{container}**\n\n"
            f"Merges the library package's EntryTypes, Views, and Tags into the "
            f"{'track' if track_id else 'app'}'s attached operational model (additive)."
        ),
        "diff_machine": {"op": "apply_library_operational_model", **payload},
        "payload": payload,
    }


def _stage_discard_profile_draft(args: Dict[str, Any]) -> Dict[str, Any]:
    """Stage a ``discard_profile_draft`` — executor splats ``{draft_id}``."""
    src = args or {}
    draft_id = src.get("draft_id")
    if not draft_id:
        raise ValueError("discard_profile_draft: draft_id is required")
    payload = {"draft_id": draft_id}
    return {
        "kind": "discard_profile_draft",
        "summary": f"Discard profile draft {draft_id}",
        "diff_human": (
            f"**Discard profile draft** `{draft_id}`\n\n"
            f"The draft is deleted. The published parent is untouched."
        ),
        "diff_machine": {"op": "discard_profile_draft", **payload},
        "payload": payload,
    }


_PROFILE_MODIFY_ACTIONS = (
    "add_entry_type",
    "remove_entry_type",
    "add_view",
    "remove_view",
    "add_tag",
    "remove_tag",
)

# Allowlisted action-specific kwargs the staged ``modify_operational_model`` payload may
# carry — the exact non-identity / non-(action|track_id|app_id) params consumed
# by ``app.services.operational_model_authoring.modify_operational_model`` (and splatted by
# ``staging_executors._x_modify_operational_model``). Anything outside this set (e.g.
# ``user_id``) is dropped silently so the stager stays data-only: identity is
# the dispatch ``principal_id``, never a tool arg (PC-1).
_PROFILE_MODIFY_PARAM_KEYS = frozenset(
    {
        "name",
        "icon",
        "view_type",
        "config",
        "fields",
        "color",
        "group_key",
        "entry_type_id",
        "view_id",
        "tag_id",
    }
)


async def _stage_modify_operational_model(args: Dict[str, Any]) -> Dict[str, Any]:
    """Stage a ``modify_operational_model.<action>`` — sub-kind computed from ``action``.

    ``_x_modify_operational_model`` splats ``{action, track_id?, app_id?, **rest}`` where
    ``rest`` is the action-specific kwargs (name/icon/view_type/config/color/
    group_key/entry_type_id/view_id/tag_id — the ``modify_operational_model`` service
    params). ``rest`` is ALLOWLISTED to those known keys so the stager stays
    data-only: arbitrary keys (e.g. ``user_id``) are dropped silently and can
    never reach the staged payload (PC-1 — identity is the dispatch
    ``principal_id``, never a tool arg). The manifest declares 6 valid sub-kinds
    (``modify_operational_model.{add,remove}_{entry_type,view,tag}``). An unknown action
    raises (fail-closed) rather than minting an un-executable kind.
    """
    src = dict(args or {})
    action = (src.get("action") or "").strip()
    if action not in _PROFILE_MODIFY_ACTIONS:
        raise ValueError(
            "modify_operational_model: action must be one of "
            + ", ".join(_PROFILE_MODIFY_ACTIONS)
        )
    track_id = src.get("track_id") or None
    app_id = src.get("app_id") or src.get("space_id") or None
    if not track_id and not app_id:
        raise ValueError("modify_operational_model: track_id or app_id is required")

    rest = {
        k: v
        for k, v in src.items()
        if k in _PROFILE_MODIFY_PARAM_KEYS and v is not None
    }
    payload: Dict[str, Any] = {"action": action}
    if track_id:
        payload["track_id"] = track_id
    if app_id:
        payload["app_id"] = app_id
    payload.update(rest)

    container = await _sd.resolve_container_label(track_id=track_id, app_id=app_id)
    what = (
        rest.get("name")
        or rest.get("entry_type_id")
        or rest.get("view_id")
        or rest.get("tag_id")
        or ""
    )
    verb = "Add" if action.startswith("add_") else "Remove"
    noun = action.split("_", 1)[1].replace("_", " ")
    return {
        "kind": f"modify_operational_model.{action}",
        "summary": f"{verb} {noun} {what}".strip() + f" on {container}",
        "diff_human": (
            f"**Modify profile** ({action}) on **{container}**"
            + (f"\n\n- **{noun}:** {what}" if what else "")
        ),
        "diff_machine": {"op": "modify_operational_model", **payload},
        "payload": payload,
    }


def _stage_propose_profile_revision(args: Dict[str, Any]) -> Dict[str, Any]:
    """Stage a ``propose_profile_revision`` — executor splats ``{draft_id, operations}``."""
    src = args or {}
    draft_id = src.get("draft_id")
    operations = src.get("operations") or []
    # BadRequestError, not ValueError: dispatch_tool now genericizes untyped
    # exceptions to "An internal error occurred" (so raw internals stop
    # reaching callers) and only surfaces the message of a
    # JVSpatialAPIException. A bare ValueError therefore turned an actionable
    # validation message into noise the agent cannot act on. These are caller
    # errors, so the canonical typed error is also what AGENTS.md mandates.
    from app.api.errors import BadRequestError

    if not draft_id:
        raise BadRequestError(message="propose_profile_revision: draft_id is required")
    if not isinstance(operations, list) or not operations:
        raise BadRequestError(
            message="propose_profile_revision: a non-empty operations list is required"
        )
    payload = {"draft_id": draft_id, "operations": operations}
    op_names = [o.get("op") for o in operations if isinstance(o, dict)]
    operation_lines = []
    for operation in operations:
        if not isinstance(operation, dict):
            continue
        if operation.get("op") != "add_field":
            continue
        field = operation.get("spec") or operation.get("field") or {}
        if not isinstance(field, dict):
            continue
        entry_type = operation.get("entry_type") or operation.get("entry_type_key")
        field_name = str(field.get("name") or field.get("key") or "unnamed field")
        field_type = str(field.get("type") or "unspecified")
        choices = field.get("enum") or field.get("choices") or []
        details = f"- **Add field:** {field_name} (`{field_type}`)"
        if entry_type:
            details += f" on `{entry_type}`"
        if isinstance(choices, list) and choices:
            details += "; choices: " + ", ".join(str(choice) for choice in choices)
        operation_lines.append(details)
    detail_block = "\n".join(operation_lines) or (
        f"- **Operations:** {', '.join(str(n) for n in op_names) or '(unnamed)'}"
    )
    return {
        "kind": "propose_profile_revision",
        "summary": f"Apply {len(operations)} op(s) to draft {draft_id}",
        "diff_human": (f"**Revise profile draft** `{draft_id}`\n\n" f"{detail_block}"),
        "diff_machine": {"op": "propose_profile_revision", **payload},
        "payload": payload,
    }


def _stage_publish_profile_draft(args: Dict[str, Any]) -> Dict[str, Any]:
    """Stage a ``publish_profile_draft``.

    ``_x_publish_profile_draft`` splats ``{draft_id, run_migrations?,
    abort_on_migration_failure?}``.
    """
    src = args or {}
    draft_id = src.get("draft_id")
    if not draft_id:
        raise ValueError("publish_profile_draft: draft_id is required")
    payload: Dict[str, Any] = {"draft_id": draft_id}
    if src.get("run_migrations") is not None:
        payload["run_migrations"] = bool(src["run_migrations"])
    if src.get("abort_on_migration_failure") is not None:
        payload["abort_on_migration_failure"] = bool(src["abort_on_migration_failure"])
    return {
        "kind": "publish_profile_draft",
        "summary": f"Publish profile draft {draft_id}",
        "diff_human": (
            f"**Publish profile draft** `{draft_id}`\n\n"
            f"Atomically swaps the draft onto its published parent and runs any "
            f"declared migrations."
        ),
        "diff_machine": {"op": "publish_profile_draft", **payload},
        "payload": payload,
    }


def _stage_draft_new_profile(args: Dict[str, Any]) -> Dict[str, Any]:
    """Stage a ``draft_new_profile``.

    ``_x_draft_new_profile`` calls ``create_empty_library_draft(user_id,
    workspace_id, name, description?, scope?)`` and splats
    ``{name, description?, scope?}`` (``workspace_id`` falls back to the bound
    scope inside the executor, so the stager need not — and must not — inject
    it). The manifest tool names the package ``profile_name`` and declares
    ``description`` + ``template_key``; the legacy MCP surface used ``name`` +
    ``scope``. Accept ``profile_name`` or ``name`` for the package name and
    ``scope`` (``track``/``app``); ``template_key`` is not consumed by the
    empty-draft service and is dropped.
    """
    src = args or {}
    name = (src.get("profile_name") or src.get("name") or "").strip()
    if not name:
        raise ValueError("draft_new_profile: a profile name is required")
    scope = src.get("scope") or "track"
    payload: Dict[str, Any] = {"name": name, "scope": scope}
    if src.get("description"):
        payload["description"] = src["description"]

    return {
        "kind": "draft_new_profile",
        "summary": f"Draft new library Operational Model “{name}”",
        "diff_human": (
            f"**Draft new library Operational Model** *{name}*\n\n"
            f"- **Scope:** `{scope}`\n"
            + (
                f"- **Description:** {_truncate(payload['description'], 200)}\n"
                if payload.get("description")
                else ""
            )
            + "\nCreates an empty package draft ready to populate via revision ops."
        ),
        "diff_machine": {"op": "draft_new_profile", **payload},
        "payload": payload,
    }


def _stage_author_operational_model(args: Dict[str, Any]) -> Dict[str, Any]:
    """Stage an ``author_operational_model``.

    ``_x_author_operational_model`` splats ``{description, scope?, name?, workspace_id?}``
    (``workspace_id`` falls back to the agent_scope ContextVar inside the
    executor, so the stager need not — and must not — inject it). The manifest
    declares ``operational_model_id``/``instructions`` which is a different (NL-revision)
    shape; this stager binds the resident ``author_operational_model`` shape that the
    executor actually consumes, accepting ``instructions`` as an alias for
    ``description``.
    """
    from app.services.operational_model_authoring import _short_operational_model_name

    src = args or {}
    description = (src.get("description") or src.get("instructions") or "").strip()
    if not description:
        raise ValueError("author_operational_model: description is required")
    scope = src.get("scope") or "track"
    # Pass an explicit ``name`` straight through; otherwise leave it UNSET so the
    # service derives a short, clean label from the description's first words.
    # Never default ``name`` to the full ``description`` — that surfaces the
    # whole authoring brief as the Operational Model / entry-type label ("titles treated
    # as description fields"). ``display_name`` mirrors the service derivation
    # for the staged-card copy only.
    explicit_name = (src.get("name") or "").strip()
    display_name = _short_operational_model_name(explicit_name or None, description)
    payload = {"description": description, "scope": scope}
    if explicit_name:
        payload["name"] = explicit_name

    # Optional structured entry types (each ``{name, fields:[{key,name,type,
    # enum?}, …]}``). When present, the Operational Model is published POPULATED in one
    # shot; the service normalizes/validates. Only forward a non-empty list.
    entry_types = src.get("entry_types")
    types_lines = ""
    if isinstance(entry_types, list) and entry_types:
        payload["entry_types"] = entry_types
        rendered = []
        for et in entry_types:
            if not isinstance(et, dict):
                continue
            et_name = (et.get("name") or et.get("key") or "Entry").strip()
            field_names = [
                (f.get("name") or f.get("key") or "").strip()
                for f in (et.get("fields") or [])
                if isinstance(f, dict) and (f.get("name") or f.get("key"))
            ]
            field_names = [f for f in field_names if f]
            rendered.append(
                f"  - **{et_name}** — "
                + (", ".join(field_names) if field_names else "_no fields_")
            )
        if rendered:
            types_lines = "\n- **Entry types:**\n" + "\n".join(rendered)

    tail = (
        "Publishes a populated manifest to the library."
        if types_lines
        else "Creates a minimal starter manifest (no fields) and publishes it "
        "to the library — add fields with integral_modify_model."
    )
    return {
        "kind": "author_operational_model",
        "summary": f"Author library Operational Model “{display_name}”",
        "diff_human": (
            f"**Author library Operational Model** *{display_name}*\n\n"
            f"- **Scope:** `{scope}`\n"
            f"- **Description:** {_truncate(description, 200)}"
            f"{types_lines}\n\n"
            f"{tail}"
        ),
        "diff_machine": {"op": "author_operational_model", **payload},
        "payload": payload,
    }


# Param names each profile-authoring propose stager accepts from a tool call.
# The manifest's published ``params`` for these tools MUST be a subset of the
# matching set: the resident agent calls the tool per its *advertised* schema,
# and a stager that does not recognize those names fails closed. This is the
# single source of truth the drift guard (``tests/
# test_tooling_manifest_stager_params.py``) checks the manifest against — it is
# what caught the ``draft_id``/``high_level_changes`` vs ``action``/``track_id``
# (modify) and ``operational_model_id``/``instructions`` vs ``description`` (author) splits.
#
# ``modify_operational_model`` accepts the structural keys (``action`` + a container,
# where ``space_id`` aliases ``app_id``) plus the action-specific kwargs the
# stager allowlists (:data:`_PROFILE_MODIFY_PARAM_KEYS`). ``author_operational_model``
# accepts ``description`` (with ``instructions`` as a back-compat alias) plus the
# optional ``scope`` / ``name`` / ``entry_types`` (structured types + fields, for
# one-shot populated synthesis). ``workspace_id`` is bound from dispatch scope,
# never a tool arg (PC-2), so it is intentionally NOT accepted here.
STAGER_ACCEPTED_PARAMS: Dict[str, "frozenset[str]"] = {
    "integral_modify_model": (
        frozenset({"action", "track_id", "app_id", "space_id"})
        | _PROFILE_MODIFY_PARAM_KEYS
    ),
    "integral_author_model": frozenset(
        {"description", "instructions", "scope", "name", "entry_types"}
    ),
}


# ---- collaboration -------------------------------------------------------- #
_SHARE_RESOURCE_TYPES = ("app", "track", "entry")
# The manifest's ``integral_share`` declares ``role`` enum
# ``[viewer, commenter, editor]`` with a role-cap privacy note (I-ROLE-03 /
# PC-3). The backing ``sharing.add_collaborator`` accepts a BROADER VALID_ROLES
# set (incl. ``owner``/``admin``); enforce the manifest enum HERE so this
# convenience tool can never stage an over-grant (``owner``/``admin``). Fail
# closed on anything outside the enum rather than silently downgrading.
_SHARE_VALID_ROLES = ("viewer", "commenter", "editor")


def _stage_share(args: Dict[str, Any]) -> Dict[str, Any]:
    """Stage an ``integral_share`` (collaborator-add).

    ``_x_share`` splats ``{resource_type, resource_id, role?, email?,
    collaborator_user_id?}`` into the per-resource collaborator handler. The
    manifest declares a polymorphic collab-OR-link tool; the legacy
    ``integral_share`` branch only ever ADDED A COLLABORATOR (track/app; entry is
    now supported too), so this stager binds the collaborator path. Accept the
    manifest's ``resource_type``/``resource_id``/``role``/``email`` plus an
    explicit ``collaborator_user_id``; legacy ``entity_type``/``entity_id``/
    ``collaborator_email`` are accepted as aliases.

    Data only (PC-1): no ``user_id`` (the acting principal is the dispatch
    principal; the COLLABORATOR is resolved at bless from email/id in the
    executor). The default role follows the manifest privacy note (I-ROLE-03):
    ``commenter`` when unspecified.
    """
    src = args or {}
    resource_type = src.get("resource_type") or src.get("entity_type")
    resource_id = src.get("resource_id") or src.get("entity_id")
    if resource_type not in _SHARE_RESOURCE_TYPES:
        raise ValueError(
            "share: resource_type must be one of " + ", ".join(_SHARE_RESOURCE_TYPES)
        )
    if not resource_id:
        raise ValueError("share: resource_id is required")
    email = (src.get("email") or src.get("collaborator_email") or "").strip()
    collaborator_user_id = src.get("collaborator_user_id")
    if not email and not collaborator_user_id:
        raise ValueError("share: an email or collaborator_user_id is required")
    role = src.get("role") or "commenter"  # I-ROLE-03 default
    # Clamp to the manifest enum (fail-closed): reject ``owner``/``admin`` or any
    # other out-of-enum role rather than forwarding it to the broader
    # ``sharing.VALID_ROLES``. No silent downgrade — an over-grant attempt is an
    # error, not a courtesy.
    if role not in _SHARE_VALID_ROLES:
        raise ValueError("share: role must be one of " + ", ".join(_SHARE_VALID_ROLES))

    payload: Dict[str, Any] = {
        "resource_type": resource_type,
        "resource_id": resource_id,
        "role": role,
    }
    if email:
        payload["email"] = email
    if collaborator_user_id:
        payload["collaborator_user_id"] = collaborator_user_id

    who = email or collaborator_user_id
    return {
        "kind": "share",
        "summary": f"Share {resource_type} {resource_id} with {who} as {role}",
        "diff_human": (
            f"**Share {resource_type}** `{resource_id}`\n\n"
            f"- **Collaborator:** {who}\n"
            f"- **Role:** `{role}`\n\n"
            f"Adds a direct collaborator grant (auto-guest workspace membership "
            f"if cross-workspace)."
        ),
        "diff_machine": {"op": "share", **payload},
        "payload": payload,
    }


_ADD_COLLAB_VALID_ROLES = ("viewer", "commenter", "editor", "admin")


def _stage_add_collaborator(args: Dict[str, Any]) -> Dict[str, Any]:
    """Stage ``integral_add_collaborator`` — same ``share`` executor, ``user_id`` param."""
    src = args or {}
    mapped = dict(src)
    if src.get("user_id") and not src.get("collaborator_user_id"):
        mapped["collaborator_user_id"] = src["user_id"]
    resource_type = mapped.get("resource_type")
    resource_id = mapped.get("resource_id")
    collaborator_user_id = mapped.get("collaborator_user_id")
    if resource_type not in _SHARE_RESOURCE_TYPES:
        raise ValueError(
            "add_collaborator: resource_type must be one of "
            + ", ".join(_SHARE_RESOURCE_TYPES)
        )
    if not resource_id or not collaborator_user_id:
        raise ValueError("add_collaborator: resource_id and user_id are required")
    role = mapped.get("role") or "commenter"
    if role not in _ADD_COLLAB_VALID_ROLES:
        raise ValueError(
            "add_collaborator: role must be one of "
            + ", ".join(_ADD_COLLAB_VALID_ROLES)
        )
    payload = {
        "resource_type": resource_type,
        "resource_id": resource_id,
        "collaborator_user_id": collaborator_user_id,
        "role": role,
    }
    return {
        "kind": "share",
        "summary": (
            f"Add collaborator on {resource_type} {resource_id} "
            f"as {role} ({collaborator_user_id})"
        ),
        "diff_human": (
            f"**Add collaborator** on {resource_type} `{resource_id}`\n\n"
            f"- **User:** {collaborator_user_id}\n"
            f"- **Role:** `{role}`"
        ),
        "diff_machine": {"op": "share", **payload},
        "payload": payload,
    }


def _stage_remove_collaborator(args: Dict[str, Any]) -> Dict[str, Any]:
    _require(args, "resource_type", "resource_id", "user_id")
    payload = {
        "resource_type": args["resource_type"],
        "resource_id": args["resource_id"],
        "user_id": args["user_id"],
    }
    return {
        "kind": "remove_collaborator",
        "summary": (
            f"Remove collaborator {args['user_id']} from "
            f"{args['resource_type']} {args['resource_id']}"
        ),
        "diff_human": (
            f"Remove direct collaborator `{args['user_id']}` from "
            f"{args['resource_type']} `{args['resource_id']}`"
        ),
        "diff_machine": {"op": "remove_collaborator", **payload},
        "payload": payload,
    }


def _stage_set_exclusion(args: Dict[str, Any]) -> Dict[str, Any]:
    _require(args, "resource_type", "resource_id", "user_id")
    payload = {
        "resource_type": args["resource_type"],
        "resource_id": args["resource_id"],
        "user_id": args["user_id"],
    }
    return {
        "kind": "set_exclusion",
        "summary": (
            f"Exclude {args['user_id']} from {args['resource_type']} {args['resource_id']}"
        ),
        "diff_human": (
            f"Add **EXCLUDED_FROM** deny for `{args['user_id']}` on "
            f"{args['resource_type']} `{args['resource_id']}`"
        ),
        "diff_machine": {"op": "set_exclusion", **payload},
        "payload": payload,
    }


def _stage_remove_exclusion(args: Dict[str, Any]) -> Dict[str, Any]:
    _require(args, "resource_type", "resource_id", "user_id")
    payload = {
        "resource_type": args["resource_type"],
        "resource_id": args["resource_id"],
        "user_id": args["user_id"],
    }
    return {
        "kind": "remove_exclusion",
        "summary": (
            f"Restore inherited access for {args['user_id']} on "
            f"{args['resource_type']} {args['resource_id']}"
        ),
        "diff_human": (
            f"Clear **EXCLUDED_FROM** for `{args['user_id']}` on "
            f"{args['resource_type']} `{args['resource_id']}`"
        ),
        "diff_machine": {"op": "remove_exclusion", **payload},
        "payload": payload,
    }


def _stage_mint_share_link(args: Dict[str, Any]) -> Dict[str, Any]:
    _require(args, "resource_type", "resource_id")
    payload = {
        "resource_type": args["resource_type"],
        "resource_id": args["resource_id"],
    }
    if args.get("role"):
        payload["role"] = args["role"]
    if args.get("expires_at"):
        payload["expires_at"] = args["expires_at"]
    return {
        "kind": "mint_share_link",
        "summary": (
            f"Mint share link on {args['resource_type']} {args['resource_id']}"
        ),
        "diff_human": (
            f"Mint a tokenized share link on {args['resource_type']} "
            f"`{args['resource_id']}`"
        ),
        "diff_machine": {"op": "mint_share_link", **payload},
        "payload": payload,
    }


def _stage_revoke_share_link(args: Dict[str, Any]) -> Dict[str, Any]:
    _require(args, "share_link_id")
    payload = {"share_link_id": args["share_link_id"]}
    return {
        "kind": "revoke_share_link",
        "summary": f"Revoke share link {args['share_link_id']}",
        "diff_human": f"Revoke share link `{args['share_link_id']}`",
        "diff_machine": {"op": "revoke_share_link", **payload},
        "payload": payload,
    }


def _stage_invite(args: Dict[str, Any]) -> Dict[str, Any]:
    _require(args, "target_type", "target_id", "email")
    payload = {
        "target_type": args["target_type"],
        "target_id": args["target_id"],
        "email": args["email"],
    }
    if args.get("role"):
        payload["role"] = args["role"]
    if args.get("message"):
        payload["message"] = args["message"]
    return {
        "kind": "invite",
        "summary": (
            f"Invite {args['email']} to {args['target_type']} {args['target_id']}"
        ),
        "diff_human": (
            f"Send invitation to **{args['email']}** for "
            f"{args['target_type']} `{args['target_id']}`"
        ),
        "diff_machine": {"op": "invite", **payload},
        "payload": payload,
    }


def _stage_edit_comment(args: Dict[str, Any]) -> Dict[str, Any]:
    _require(args, "comment_id")
    text = (args.get("text") or args.get("body") or "").strip()
    if not text:
        raise ValueError("edit_comment: text is required")
    payload = {"comment_id": args["comment_id"], "text": text}
    return {
        "kind": "edit_comment",
        "summary": f"Edit comment {args['comment_id']}",
        "diff_human": f"Edit comment `{args['comment_id']}`",
        "diff_machine": {"op": "edit_comment", **payload},
        "payload": payload,
    }


def _stage_delete_comment(args: Dict[str, Any]) -> Dict[str, Any]:
    _require(args, "comment_id")
    payload = {"comment_id": args["comment_id"]}
    return {
        "kind": "delete_comment",
        "summary": f"Delete comment {args['comment_id']}",
        "diff_human": f"Delete comment `{args['comment_id']}`",
        "diff_machine": {"op": "delete_comment", **payload},
        "payload": payload,
    }


def _stage_delete_view(args: Dict[str, Any]) -> Dict[str, Any]:
    _require(args, "view_id")
    payload = {"view_id": args["view_id"]}
    return {
        "kind": "delete_view",
        "summary": f"Delete view {args['view_id']}",
        "diff_human": f"Delete saved view `{args['view_id']}`",
        "diff_machine": {"op": "delete_view", **payload},
        "payload": payload,
    }


def _stage_attach_file(args: Dict[str, Any]) -> Dict[str, Any]:
    _require(args, "entry_id", "sandbox_path")
    payload = {
        "entry_id": args["entry_id"],
        "sandbox_path": args["sandbox_path"],
    }
    return {
        "kind": "attach_file",
        "summary": (
            f"Attach sandbox file {args['sandbox_path']} to entry {args['entry_id']}"
        ),
        "diff_human": (
            f"Attach sandbox file `{args['sandbox_path']}` to entry "
            f"`{args['entry_id']}`"
        ),
        "diff_machine": {"op": "attach_file", **payload},
        "payload": payload,
    }


def _stage_attach_uploaded_file(args: Dict[str, Any]) -> Dict[str, Any]:
    _require(args, "entry_id", "attachment_id")
    payload = {
        "entry_id": args["entry_id"],
        "attachment_id": args["attachment_id"],
    }
    return {
        "kind": "attach_uploaded_file",
        "summary": (
            f"Attach uploaded file {args['attachment_id']} to entry {args['entry_id']}"
        ),
        "diff_human": (
            f"Attach uploaded file `{args['attachment_id']}` to entry "
            f"`{args['entry_id']}`"
        ),
        "diff_machine": {"op": "attach_uploaded_file", **payload},
        "payload": payload,
    }


async def _stage_attach_uploaded_image(args: Dict[str, Any]) -> Dict[str, Any]:
    """Stage an on-demand image attach.

    Composer images are retained as base64 on the chat message (not written to
    attachment storage on upload). Here — at stage time, where the propose
    dispatch has bound the session — we resolve the thread, locate the uploaded
    image (by ``image_id`` or most-recent), and BAKE its bytes into the staged
    payload. The commit-time executor materializes the Attachment + wires it to
    the entry, so attachment storage is only consumed on the approved attach.
    """
    _require(args, "entry_id")
    from app.models.nodes import ChatThread
    from app.services.chat_threads import find_uploaded_image

    session_id = _bound_propose_session_id()
    if not session_id:
        raise ValueError(
            "attach_uploaded_image: no conversation session bound; the resident "
            "supplies it — this tool is only callable from a chat turn"
        )
    threads = await ChatThread.find({"context.provider_session_id": session_id})
    thread = threads[0] if threads else None
    if thread is None:
        raise ValueError("attach_uploaded_image: chat thread not found for session")
    found = await find_uploaded_image(thread, args.get("image_id"))
    if not found:
        raise ValueError(
            "attach_uploaded_image: no uploaded image with retained bytes found. "
            "Pass the image_id from the upload note, or upload the image again."
        )
    filename, mime_type, content_b64 = found
    payload = {
        "entry_id": args["entry_id"],
        "filename": filename,
        "mime_type": mime_type,
        "content_b64": content_b64,
    }
    return {
        "kind": "attach_uploaded_image",
        "summary": f"Attach uploaded image {filename} to entry {args['entry_id']}",
        "diff_human": (
            f"Attach uploaded image `{filename}` to entry `{args['entry_id']}`"
        ),
        # The base64 stays out of the diff (large/noisy); the payload carries it.
        "diff_machine": {
            "op": "attach_uploaded_image",
            "entry_id": args["entry_id"],
            "filename": filename,
            "mime_type": mime_type,
        },
        "payload": payload,
    }


async def _stage_author_skill(args: Dict[str, Any]) -> Dict[str, Any]:
    _require(args, "name", "description", "body_override")
    from app.agentive.services.agent_skills import (
        validate_body_override,
        validate_tools_required,
    )

    validate_body_override(str(args["body_override"]))
    tools_required = list(args.get("tools_required") or [])
    if tools_required:
        # Validate NOW (stage time), not at bless time — an invalid name
        # (e.g. a SKILL name like "integral_entries" passed where a TOOL
        # name like "integral_create_entry" belongs) must fail as a clean,
        # recoverable tool-call error the agent can retry this same turn,
        # not a raw exception surfaced after the user already approved.
        await validate_tools_required(tools_required)
    payload: Dict[str, Any] = {
        "name": args["name"],
        "description": args["description"],
        "body_override": args["body_override"],
        "tools_required": tools_required,
    }
    if args.get("key"):
        payload["key"] = args["key"]
    app_label = ""
    if args.get("app_id"):
        payload["app_id"] = args["app_id"]
        current = await _sd.load_app_record(args["app_id"])
        app_label = (
            _sd.app_display_label(current, args["app_id"])
            if current
            else f"App {_sd.short_node_id(args['app_id'])}"
        )
    if args.get("private") is not None:
        payload["private"] = bool(args["private"])
    key_label = args.get("key") or "auto-derived from name"
    scope_line = f" — scoped to app **{app_label}**" if app_label else ""
    return {
        "kind": "author_skill",
        "summary": f"Create workspace skill '{args['name']}'",
        "diff_human": (
            f"Create workspace skill **{args['name']}** (key: `{key_label}`)"
            f"{scope_line}\n\n{args['description']}"
        ),
        "diff_machine": {"op": "author_skill", **payload},
        "payload": payload,
    }


async def _stage_update_skill(args: Dict[str, Any]) -> Dict[str, Any]:
    _require(args, "skill_id")
    from app.agentive.services.agent_skills import (
        validate_body_override,
        validate_tools_required,
    )

    if args.get("body_override") is not None:
        validate_body_override(str(args["body_override"]))
    if args.get("tools_required") is not None:
        await validate_tools_required(list(args["tools_required"] or []))
    payload: Dict[str, Any] = {"skill_id": args["skill_id"]}
    for key in (
        "name",
        "description",
        "body_override",
        "tools_required",
        "enabled",
        "private",
    ):
        if key in args and args[key] is not None:
            payload[key] = bool(args[key]) if key == "private" else args[key]
    return {
        "kind": "update_skill",
        "summary": f"Update workspace skill {args['skill_id']}",
        "diff_human": f"Update workspace skill `{args['skill_id']}`",
        "diff_machine": {"op": "update_skill", **payload},
        "payload": payload,
    }


def _stage_delete_skill(args: Dict[str, Any]) -> Dict[str, Any]:
    _require(args, "skill_id")
    payload = {"skill_id": args["skill_id"]}
    return {
        "kind": "delete_skill",
        "summary": f"Delete workspace skill {args['skill_id']}",
        "diff_human": f"Delete workspace skill `{args['skill_id']}`",
        "diff_machine": {"op": "delete_skill", **payload},
        "payload": payload,
    }


def _stage_resolve_conflict(args: Dict[str, Any]) -> Dict[str, Any]:
    _require(args, "conflict_id", "resolution")
    payload = {
        "conflict_id": args["conflict_id"],
        "resolution": args["resolution"],
    }
    return {
        "kind": "resolve_conflict",
        "summary": f"Resolve conflict {args['conflict_id']} as {args['resolution']}",
        "diff_human": (
            f"Resolve sync conflict `{args['conflict_id']}` → "
            f"**{args['resolution']}**"
        ),
        "diff_machine": {"op": "resolve_conflict", **payload},
        "payload": payload,
    }


def _stage_trigger_sync(args: Dict[str, Any]) -> Dict[str, Any]:
    _require(args, "connector_id")
    payload = {"connector_id": args["connector_id"]}
    return {
        "kind": "trigger_sync",
        "summary": f"Trigger sync for connector {args['connector_id']}",
        "diff_human": f"Run on-demand sync for connector `{args['connector_id']}`",
        "diff_machine": {"op": "trigger_sync", **payload},
        "payload": payload,
    }


async def _stage_call_workspace_tool(args: Dict[str, Any]) -> Dict[str, Any]:
    """Stage (write) or immediately run (read) a workspace bundle tool.

    Read-only tools return ``_no_stage`` so dispatch yields the output with
    no approval card. Write tools mint ``call_workspace_tool`` (or append
    to an open batch). Agent-ineligible tools raise so the model can recover.
    """
    _require(args, "tool_key")
    tool_key = str(args["tool_key"]).strip()
    payload = args.get("input")
    if payload is None:
        payload = args.get("arguments")
    if not isinstance(payload, dict):
        payload = {}

    from app.services.agent_scope import current_scope_workspace_id
    from app.services.workspace_tools import (
        invoke_workspace_tool,
        is_agent_callable,
        is_write_tool,
        lookup_workspace_tool,
    )

    workspace_id = current_scope_workspace_id.get() or ""
    if not workspace_id:
        raise ValueError("call_workspace_tool: no active workspace")
    try:
        spec = lookup_workspace_tool(workspace_id, tool_key)
    except Exception as exc:
        msg = getattr(exc, "message", None) or str(exc)
        raise ValueError(str(msg)) from exc
    if not is_agent_callable(spec):
        raise ValueError(
            f"tool {tool_key!r} is not callable from chat — it is wired to "
            "an in-app action button. Tell the user to click that button "
            "on the entry's page."
        )
    if not is_write_tool(spec):
        output = await invoke_workspace_tool(
            user_id=_bound_propose_principal(),
            workspace_id=workspace_id,
            tool_key=tool_key,
            payload=payload,
            agent=True,
        )
        return {"_no_stage": True, "data": {"tool_key": tool_key, "output": output}}

    label = spec.get("name") or tool_key
    field_bits = (
        ", ".join(f"`{k}`" for k in sorted(payload.keys())) if payload else "no inputs"
    )
    return {
        "kind": "call_workspace_tool",
        "summary": f"Run “{label}”",
        "diff_human": f"**Run workspace tool** `{tool_key}`\n\n- **Input:** {field_bits}",
        "diff_machine": {
            "op": "call_workspace_tool",
            "tool_key": tool_key,
            "input": payload,
        },
        "payload": {"tool_key": tool_key, "input": payload},
    }


async def _stage_save_view(args: Dict[str, Any]) -> Dict[str, Any]:
    """Stage a ``save_view``.

    ``_x_save_view`` calls ``agent_insights.save_view(user_id, track_id, name,
    view_type="feed", config={})`` and splats ``{track_id, name, view_type?,
    config?}``. The manifest tool declares ``track_id`` + ``view_type`` +
    ``config`` (and an optional ``view_id`` for the update path); the service
    keys the view by ``name``, so accept ``name`` (or the manifest's ``title``)
    for the view name and default ``view_type`` to ``feed`` (the service
    default). Data only (PC-1): no identity / scope key reaches the payload.
    """
    src = args or {}
    track_id = src.get("track_id")
    if not track_id:
        raise ValueError("save_view: track_id is required")
    name = (src.get("name") or src.get("title") or "").strip()
    if not name:
        raise ValueError("save_view: a view name is required")
    view_type = src.get("view_type") or "feed"
    config = src.get("config") or {}

    payload: Dict[str, Any] = {
        "track_id": track_id,
        "name": name,
        "view_type": view_type,
        "config": config,
    }
    track_lbl = await _sd.resolve_track_label(track_id)
    return {
        "kind": "save_view",
        "summary": f"Save view “{name}” on {track_lbl}",
        "diff_human": (
            f"**Save {view_type} view** *{name}* on track **{track_lbl}**\n\n"
            f"Materializes the configured view onto the track's operational model."
        ),
        "diff_machine": {"op": "save_view", **payload},
        "payload": payload,
    }


def _starter_dashboard_widgets() -> List[Dict[str, Any]]:
    """Minimal non-empty widget set when the agent omits ``widgets``.

    Agent create must never stage an empty board — that was the common
    failure mode (name-only create). Prefer ``suggest_dashboard_template``
    when a principal is available; this is the offline fallback.
    """
    return [
        {
            "id": "w_total",
            "type": "metric_card",
            "title": "Total entries",
            "grid": {"x": 0, "y": 0, "w": 3, "h": 2},
            "config": {},
            "data_source": {"kind": "count"},
        },
        {
            "id": "w_tracks",
            "type": "metric_card",
            "title": "Active tracks",
            "grid": {"x": 3, "y": 0, "w": 3, "h": 2},
            "config": {},
            "data_source": {"kind": "count", "metric": "track_count"},
        },
        {
            "id": "w_breakdown",
            "type": "track_breakdown",
            "title": "Tracks overview",
            "grid": {"x": 0, "y": 2, "w": 6, "h": 4},
            "config": {},
            "data_source": {"kind": "track_breakdown", "period": "week"},
        },
        {
            "id": "w_activity",
            "type": "activity_digest",
            "title": "Recent activity",
            "grid": {"x": 6, "y": 2, "w": 6, "h": 4},
            "config": {},
            "data_source": {"kind": "activity_digest", "period": "week"},
        },
    ]


_DASHBOARD_WIDGET_TYPE_ALIASES = {
    # The resident naturally describes dashboard intent using these familiar
    # names.  The persisted dashboard contract deliberately has a smaller,
    # renderer-backed palette.  Translate only stable, unambiguous synonyms
    # before validation so a useful dashboard is not discarded for vocabulary.
    "kpi": "metric_card",
    "metric": "metric_card",
    "chart": "chart_bar",
    "feed": "activity_digest",
    "calendar": "activity_digest",
    "table": "recent_entries",
    "quick_link": "recent_entries",
}


def _canonicalize_dashboard_widget_types(
    widgets: List[Any],
) -> tuple[List[Any], int]:
    """Translate common semantic widget labels into the renderer palette."""
    canonical: List[Any] = []
    translated = 0
    for widget in widgets:
        if not isinstance(widget, dict):
            canonical.append(widget)
            continue
        item = dict(widget)
        widget_type = str(item.get("type") or "").strip().casefold()
        target_type = _DASHBOARD_WIDGET_TYPE_ALIASES.get(widget_type)
        if target_type:
            item["type"] = target_type
            translated += 1
        canonical.append(item)
    return canonical, translated


async def _stage_create_dashboard(args: Dict[str, Any]) -> Dict[str, Any]:
    from app.services.dashboard_widget_validation import (
        normalize_widget_specs,
        validate_widget_specs,
    )

    src = args or {}
    app_id = src.get("app_id")
    if not app_id:
        raise ValueError("create_dashboard: app_id is required")
    name = (src.get("name") or "").strip()
    if not name:
        raise ValueError("create_dashboard: name is required")
    raw_widgets = src.get("widgets") or []
    layout = src.get("layout")
    auto_filled = False

    # Agent path: empty widgets → suggest (or starter fallback). HTTP/UI may
    # still create blank boards via create_dashboard service directly.
    if not raw_widgets:
        pid = _propose_principal.get()
        if pid:
            try:
                from app.services.dashboard_service import suggest_dashboard_template

                suggestion = await suggest_dashboard_template(
                    user_id=pid, app_id=str(app_id)
                )
                if suggestion and not suggestion.get("error"):
                    raw_widgets = list(suggestion.get("widgets") or [])
                    if layout is None and suggestion.get("layout"):
                        layout = suggestion["layout"]
            except Exception:  # noqa: BLE001 — fall through to starter set
                raw_widgets = []
        if not raw_widgets:
            raw_widgets = _starter_dashboard_widgets()
        auto_filled = True

    raw_widgets, translated_widget_types = _canonicalize_dashboard_widget_types(
        list(raw_widgets)
    )
    errors = validate_widget_specs(raw_widgets)
    if errors:
        raise ValueError("create_dashboard: invalid widgets — " + "; ".join(errors))
    norm_widgets, _ = normalize_widget_specs(raw_widgets)
    if not norm_widgets:
        raise ValueError(
            "create_dashboard: widgets required — pass a non-empty widgets "
            "array, or call integral_suggest_dashboard first"
        )
    payload: Dict[str, Any] = {
        "app_id": app_id,
        "name": name,
        "layout": layout or {"columns": 12, "row_height": 80},
        "widgets": norm_widgets,
        "is_default": bool(src.get("is_default", False)),
    }
    notes = []
    if auto_filled:
        notes.append("auto-filled starter widgets")
    if translated_widget_types:
        notes.append(f"normalized {translated_widget_types} widget type(s)")
    filled_note = f" ({'; '.join(notes)})" if notes else ""
    return {
        "kind": "create_dashboard",
        "summary": f"Create dashboard “{name}”",
        "diff_human": (
            f"**Create dashboard** *{name}* with "
            f"{len(norm_widgets)} widget(s){filled_note}."
        ),
        "diff_machine": {"op": "create_dashboard", **payload},
        "payload": payload,
    }


async def _stage_update_dashboard(args: Dict[str, Any]) -> Dict[str, Any]:
    from app.services.dashboard_widget_validation import (
        normalize_widget_specs,
        validate_widget_specs,
    )

    src = args or {}
    app_id = src.get("app_id")
    dashboard_id = src.get("dashboard_id")
    if not app_id or not dashboard_id:
        raise ValueError("update_dashboard: app_id and dashboard_id are required")
    payload = {
        k: src[k]
        for k in ("app_id", "dashboard_id", "name", "layout", "widgets", "is_default")
        if k in src and src[k] is not None
    }
    if "widgets" in payload:
        raw_widgets = payload["widgets"]
        errors = validate_widget_specs(raw_widgets)
        if errors:
            raise ValueError("update_dashboard: invalid widgets — " + "; ".join(errors))
        norm_widgets, _ = normalize_widget_specs(raw_widgets)
        payload["widgets"] = norm_widgets
    widget_note = ""
    if "widgets" in payload:
        widget_note = f" ({len(payload['widgets'])} widget(s))"
    return {
        "kind": "update_dashboard",
        "summary": f"Update dashboard {dashboard_id}",
        "diff_human": f"**Update dashboard** `{dashboard_id}`{widget_note}.",
        "diff_machine": {"op": "update_dashboard", **payload},
        "payload": payload,
    }


async def _stage_delete_dashboard(args: Dict[str, Any]) -> Dict[str, Any]:
    src = args or {}
    app_id = src.get("app_id")
    dashboard_id = src.get("dashboard_id")
    if not app_id or not dashboard_id:
        raise ValueError("delete_dashboard: app_id and dashboard_id are required")
    payload = {"app_id": app_id, "dashboard_id": dashboard_id}
    return {
        "kind": "delete_dashboard",
        "summary": f"Delete dashboard {dashboard_id}",
        "diff_human": f"**Delete dashboard** `{dashboard_id}`.",
        "diff_machine": {"op": "delete_dashboard", **payload},
        "payload": payload,
    }


def _set_focus_direct_map(args: Dict[str, Any]) -> Dict[str, Any]:
    """Map ``integral_set_focus`` args to the direct service fn kwargs.

    The service fn (``conversation_context.set_focus_for_dispatch``) needs a
    ``context_id`` (which ConversationContext to mutate) plus the focus targets.
    The manifest tool declares ``track_id`` (and the legacy MCP surface also
    ``focused_space_id`` + a ``context_id``); accept ``focused_track_id`` or
    ``track_id`` for the track, and forward ``context_id`` / ``focused_space_id``
    when present. Data only — no ``user_id`` (identity is the dispatch principal,
    injected by ``_dispatch_direct``). When the external dispatch surface omits
    ``context_id`` the service fails closed (it cannot guess a conversation).
    """
    src = args or {}
    out: Dict[str, Any] = {}
    if src.get("context_id") is not None:
        out["context_id"] = src["context_id"]
    track = src.get("focused_track_id", src.get("track_id"))
    if track is not None:
        out["focused_track_id"] = track
    space = src.get("focused_space_id", src.get("space_id"))
    if space is not None:
        out["focused_space_id"] = space
    return out


def _mark_notification_read_direct_map(args: Dict[str, Any]) -> Dict[str, Any]:
    src = args or {}
    notification_id = src.get("notification_id")
    if not notification_id:
        raise ValueError("mark_notification_read: notification_id is required")
    return {"notification_id": notification_id}


def _invoke_app_operation_direct_map(args: Dict[str, Any]) -> Dict[str, Any]:
    src = args or {}
    app_id = src.get("app_id")
    operation_key = src.get("operation_key")
    if not app_id:
        raise ValueError("invoke_app_operation: app_id is required")
    if not operation_key:
        raise ValueError("invoke_app_operation: operation_key is required")
    out: Dict[str, Any] = {
        "app_id": app_id,
        "operation_key": operation_key,
        "input": dict(src.get("input") or {}),
    }
    if src.get("idempotency_key"):
        out["idempotency_key"] = src["idempotency_key"]
    if src.get("correlation_id"):
        out["correlation_id"] = src["correlation_id"]
    return out


# --------------------------------------------------------------------------- #
# Phase 0 enabler tools — bulk ops, relation wiring, CRUD fills.
# Sync stagers package allowlisted args into one StagedChange; the matching
# executor (staging_executors) does the per-item work + per-item policy gate.
# --------------------------------------------------------------------------- #

_BULK_UPDATE_KEYS = {"title", "body", "fields", "tags", "status", "entry_type"}


def _require(args: Dict[str, Any], *keys: str) -> None:
    """Raise a clear ValueError when a required arg is absent/empty.

    The dispatcher turns this into an actionable error the agent can recover from,
    instead of an opaque ``KeyError`` surfaced as "internal error".
    """
    missing = [k for k in keys if not (args or {}).get(k)]
    if missing:
        raise ValueError(f"missing required argument(s): {', '.join(missing)}")


def _stage_bulk_update_entries(args: Dict[str, Any]) -> Dict[str, Any]:
    ids = [str(x) for x in (args.get("entry_ids") or [])]
    requested = args.get("updates") or {}
    updates = {k: v for k, v in requested.items() if k in _BULK_UPDATE_KEYS}
    if not ids:
        raise ValueError("bulk_update_entries: entry_ids is required")
    # Unknown keys are dropped silently on purpose -- the allowlist is what
    # stops a smuggled ``user_id`` from riding along in a patch. But dropping
    # every key the caller sent is not a patch, it is a no-op, and staging it
    # anyway produced a card that read "Update 2 entries", reported "Done", and
    # changed nothing -- leaving the observations it claimed to mark still
    # pending, ready to be promoted a second time.
    #
    # Custom fields are the common way to land here: they belong under
    # ``fields``, so ``{"handled": "promoted"}`` filters away to nothing while
    # ``{"fields": {"handled": "promoted"}}`` is what the caller meant. Name the
    # allowed keys so the agent can recover instead of re-staging the same
    # empty patch.
    if not updates:
        allowed = ", ".join(sorted(_BULK_UPDATE_KEYS))
        if requested:
            raise ValueError(
                "bulk_update_entries: none of the supplied keys "
                f"({', '.join(sorted(map(str, requested)))}) can be patched; "
                f"allowed keys are {allowed}. Custom fields go under 'fields'."
            )
        raise ValueError(
            f"bulk_update_entries: supply at least one field to change ({allowed})"
        )
    return {
        "kind": "bulk_update_entries",
        "summary": f"Update {len(ids)} entr{'y' if len(ids) == 1 else 'ies'}",
        "diff_human": f"Apply {sorted(updates)} to {len(ids)} entries",
        "diff_machine": {"entry_ids": ids, "updates": updates},
        "payload": {"entry_ids": ids, "updates": updates},
    }


def _stage_bulk_delete_entries(args: Dict[str, Any]) -> Dict[str, Any]:
    ids = [str(x) for x in (args.get("entry_ids") or [])]
    return {
        "kind": "bulk_delete_entries",
        "summary": f"Delete {len(ids)} entr{'y' if len(ids) == 1 else 'ies'}",
        "diff_human": f"Soft-delete {len(ids)} entries",
        "diff_machine": {"entry_ids": ids},
        "payload": {"entry_ids": ids},
    }


def _stage_add_entry_tag(args: Dict[str, Any]) -> Dict[str, Any]:
    _require(args, "entry_id", "tag_id")
    return {
        "kind": "add_entry_tag",
        "summary": "Add tag to entry",
        "diff_human": f"Tag {args.get('tag_id')} → entry {args.get('entry_id')}",
        "diff_machine": {
            "entry_id": args.get("entry_id"),
            "tag_id": args.get("tag_id"),
        },
        "payload": {"entry_id": args["entry_id"], "tag_id": args["tag_id"]},
    }


def _stage_remove_entry_tag(args: Dict[str, Any]) -> Dict[str, Any]:
    _require(args, "entry_id", "tag_id")
    return {
        "kind": "remove_entry_tag",
        "summary": "Remove tag from entry",
        "diff_human": f"Untag {args.get('tag_id')} from entry {args.get('entry_id')}",
        "diff_machine": {
            "entry_id": args.get("entry_id"),
            "tag_id": args.get("tag_id"),
        },
        "payload": {"entry_id": args["entry_id"], "tag_id": args["tag_id"]},
    }


def _stage_create_tag(args: Dict[str, Any]) -> Dict[str, Any]:
    _require(args, "name")
    payload = {
        k: args[k]
        for k in ("name", "track_id", "app_id", "color", "parent_tag_id")
        if args.get(k)
    }
    return {
        "kind": "create_tag",
        "summary": f"Create tag '{args.get('name')}'",
        "diff_human": f"Create tag '{args.get('name')}'",
        "diff_machine": dict(payload),
        "payload": payload,
    }


def _stage_update_app(args: Dict[str, Any]) -> Dict[str, Any]:
    _require(args, "app_id")
    fields = {
        k: args[k]
        for k in ("name", "description", "visibility", "accent_color")
        if args.get(k) is not None
    }
    return {
        "kind": "update_app",
        "summary": f"Update app {args.get('app_id')}",
        "diff_human": f"Update app: {sorted(fields)}",
        "diff_machine": {"app_id": args.get("app_id"), **fields},
        "payload": {"app_id": args["app_id"], **fields},
    }


async def _stage_delete_app(args: Dict[str, Any]) -> Dict[str, Any]:
    """Stage a ``delete_app`` — ``_x_delete_app`` splats ``{app_id}``."""
    src = args or {}
    app_id = src.get("app_id")
    if not app_id:
        raise ValueError("delete_app: app_id is required")
    payload = {"app_id": app_id}

    current = await _sd.load_app_record(app_id)
    app_lbl = (
        _sd.app_display_label(current, app_id)
        if current
        else f"App {_sd.short_node_id(app_id)}"
    )
    cascade_line = (
        "Removes the app and **every track it contains**, with all their "
        "entries, comments, and attachments. A track shared with another "
        "app is unlinked from this app only, not deleted. Irreversible "
        "once blessed."
    )

    return {
        "kind": "delete_app",
        "summary": f"Delete app “{app_lbl}”",
        "diff_human": f"**Delete app** *{app_lbl}*\n\n{cascade_line}",
        "diff_machine": {"op": "delete_app", **payload},
        "payload": payload,
    }


def _stage_link_entries(args: Dict[str, Any]) -> Dict[str, Any]:
    _require(args, "source_entry_id", "field_key", "target_id")
    return {
        "kind": "link_entries",
        "summary": "Link entry via relation field",
        "diff_human": (
            f"Set {args.get('field_key')} = {args.get('target_id')} "
            f"on entry {args.get('source_entry_id')}"
        ),
        "diff_machine": {
            "source_entry_id": args.get("source_entry_id"),
            "field_key": args.get("field_key"),
            "target_id": args.get("target_id"),
        },
        "payload": {
            "source_entry_id": args["source_entry_id"],
            "field_key": args["field_key"],
            "target_id": args["target_id"],
        },
    }


async def _stage_transform_entry(args: Dict[str, Any]) -> Dict[str, Any]:
    """Stage an ``entry.transform`` handoff (won opportunity → project, etc.)."""
    _require(args, "entry_id")
    entry_id = args["entry_id"]
    current = await _sd.load_entry_record(entry_id)
    title_lbl = (
        _sd.entry_display_label(current, entry_id)
        if current
        else f"Entry {_sd.short_node_id(entry_id)}"
    )
    payload: Dict[str, Any] = {"entry_id": entry_id}
    if args.get("to_track"):
        payload["to_track"] = args["to_track"]
    if args.get("hook_key"):
        payload["hook_key"] = args["hook_key"]
    if args.get("override") is not None:
        payload["override"] = bool(args["override"])
    return {
        "kind": "transform_entry",
        "summary": f"Transform entry “{title_lbl}” via bundle hook",
        "diff_human": (
            f"**Transform entry** *{title_lbl}*\n\n"
            f"Run the matching `entry.transform` hook"
            + (f" (`{payload['hook_key']}`)" if payload.get("hook_key") else "")
            + " to spawn the target entry (e.g. Project)."
        ),
        "diff_machine": {"op": "transform_entry", **payload},
        "payload": payload,
    }


# --------------------------------------------------------------------------- #
# The registry
# --------------------------------------------------------------------------- #
TOOL_BINDINGS: Dict[str, ToolBinding] = {
    # ---- A_discovery: route-backed reads -----------------------------------
    # GET /api/auth/me -> get_current_user. Takes NO data args (no param_map):
    # identity is the bound dispatch principal (PC-1), read off request.state.user
    # by the handler — never a tool arg.
    "integral_whoami": ToolBinding(_h("app.api.auth", "get_current_user")),
    "integral_get_scope": ToolBinding(_h("app.api.users", "get_my_scope")),
    "integral_get_page_context": ToolBinding(
        service_ref=_h(
            "app.services.chat_page_context", "get_page_context_for_dispatch"
        ),
        service_param_map=_pick("include"),
    ),
    "integral_list_workspaces": ToolBinding(
        _h("app.api.workspaces", "list_workspaces")
    ),
    "integral_list_apps": ToolBinding(
        _h("app.api.apps", "list_apps"), _pick("cursor", "limit")
    ),
    # GET /api/apps/{app_id} -> get_app. app_id -> handler path kwarg; the
    # handler runs its own app.read policy check for the acting principal.
    "integral_get_app": ToolBinding(_h("app.api.apps", "get_app"), _pick("app_id")),
    "integral_list_tracks": ToolBinding(
        _h("app.api.tracks", "list_tracks"),
        _pick("cursor", "limit", "app_id", "include_total"),
    ),
    "integral_get_track_schema": ToolBinding(
        _h("app.api.tracks", "get_track_detail_bundle"), _pick("track_id")
    ),
    "integral_describe_substrate": ToolBinding(
        # Manifest http says ``/api/operational-models/substrate``; the real route
        # is ``GET /api/operational-model-substrate`` -> get_operational_model_substrate.
        _h("app.api.operational_models", "get_operational_model_substrate")
    ),
    "integral_list_models": ToolBinding(
        _h("app.api.operational_models", "list_library_operational_models"),
        _pick("type_hint"),
    ),
    "integral_query_spec": ToolBinding(
        handler_ref=_h("app.api.query_spec", "execute_query_spec_endpoint"),
        body_map=lambda args: dict(args.get("spec") or {}),
    ),
    # POST /api/retrieve: the ``retrieve`` handler parses its body via
    # ``await request.json()`` -> ``RetrieveRequest.model_validate``. body_map
    # passes the tool args through as that JSON body; param_map stays None.
    "integral_query": ToolBinding(
        handler_ref=_h("app.api.retrieve", "retrieve"),
        body_map=_retrieve_body_map,
    ),
    # SERVICE-backed: the rich filtered query (cross-track by default, tag /
    # entry_type / status / time-window filters + sort + paging), mirroring
    # integral_count_entries. The migration had mis-wired this to the minimal
    # ``list_entries`` route, dropping every filter the resident's
    # ``query_entries_filtered`` had relied on; agent_insights.query_entries is
    # the intended rich backend and even resolves a track passed by NAME.
    "integral_query_entries": ToolBinding(
        service_ref=_h("app.services.agent_insights", "query_entries"),
        service_param_map=_pick(
            "track_id",
            "query",
            "status",
            "statuses",
            "tags",
            "entry_type",
            "since",
            "until",
            "sort_by",
            "sort_dir",
            "limit",
            "offset",
        ),
    ),
    "integral_resolve_entry": ToolBinding(
        _h("app.api.entries", "get_entry"), _pick("entry_id")
    ),
    # GET /api/entries/{entry_id}/related?relation=<field_key>. The route DOES
    # exist (app/api/entry_relations.py:list_entry_relations); it was deferred
    # only because the stub lacked query_params, which Part A now supplies.
    # entry_id -> handler path kwarg; relation -> request.query_params.
    "integral_get_related": ToolBinding(
        _h("app.api.entry_relations", "list_entry_relations"),
        _pick("entry_id"),
        query_map=_relation_query_map,
    ),
    # POST /api/retrieve — no-track-scope retrieval; same body path as
    # integral_query (query + optional top_n).
    "integral_search_cross_track": ToolBinding(
        handler_ref=_h("app.api.retrieve", "retrieve"),
        body_map=_retrieve_body_map,
    ),
    "integral_get_digest": ToolBinding(
        _h("app.api.feed", "get_feed"),
        _pick(
            "track_id",
            "app_id",
            "cursor",
            "limit",
            "include_total",
        ),
    ),
    "integral_get_feed": ToolBinding(
        _h("app.api.feed", "get_feed"),
        _pick(
            "track_id",
            "app_id",
            "cursor",
            "limit",
            "include_total",
        ),
    ),
    # ---- B_entries: reads --------------------------------------------------
    "integral_list_tags": ToolBinding(
        _h("app.api.tags", "list_tags"), _pick("track_id", "app_id")
    ),
    "integral_list_comments": ToolBinding(
        _h("app.api.comments", "get_entry_comments"), _pick("entry_id")
    ),
    # ---- D_views: reads ----------------------------------------------------
    "integral_list_views": ToolBinding(
        _h("app.api.views", "list_track_views"), _pick("track_id")
    ),
    "integral_describe_dashboard_substrate": ToolBinding(
        _h("app.api.apps_dashboards", "get_dashboard_widget_substrate")
    ),
    "integral_list_dashboards": ToolBinding(
        _h("app.api.apps_dashboards", "list_app_dashboards"), _pick("app_id")
    ),
    "integral_suggest_dashboard": ToolBinding(
        _h("app.api.apps_dashboards", "suggest_app_dashboard"), _pick("app_id")
    ),
    # ---- F_collaboration: polymorphic reads --------------------------------
    "integral_get_access": ToolBinding(
        _polymorphic_resource_dispatcher(_ACCESS_HANDLERS),
        _resource_id_param_map,
    ),
    "integral_list_share_links": ToolBinding(
        _polymorphic_resource_dispatcher(_SHARE_LINK_HANDLERS),
        _resource_id_param_map,
    ),
    # ---- G_notifications / H_audit / I_sync: reads -------------------------
    "integral_list_notifications": ToolBinding(
        _h("app.api.notifications", "get_notifications"),
        _pick("read", "page", "per_page"),
    ),
    "integral_query_audit_log": ToolBinding(
        _h("app.api.audit_log", "list_audit_log"),
        _pick("scope", "actor_kind", "cursor", "limit"),
    ),
    "integral_list_conflicts": ToolBinding(
        _h("app.api.conflicts", "list_all"), _pick("connector_id", "status")
    ),
    "integral_export_view": ToolBinding(
        _h("app.api.tracks", "get_track_entries"),
        _pick("track_id", "cursor", "limit", "q", "view_id"),
    ),
    # ---- SERVICE-backed reads (Part B) -------------------------------------
    # http.method == "SERVICE"; dispatch_tool calls the service fn directly as
    # ``await fn(user_id=<principal_id>, **service_param_map(args))`` with the
    # workspace scope bound via the current_scope_workspace_id ContextVar.
    # user_id is ALWAYS principal_id (never from args); the maps below carry
    # NON-identity data only.
    "integral_describe_model": ToolBinding(
        service_ref=_h(
            "app.services.operational_model_authoring", "describe_operational_model"
        ),
        service_param_map=_describe_operational_model_service_map,
    ),
    "integral_describe_capabilities": ToolBinding(
        service_ref=_h("app.services.agent_capabilities", "describe_capabilities"),
        service_param_map=_pick("include_paused"),
    ),
    # ADR-012 governed QuerySpec — distinct from B_retrieval ``integral_query``
    # (POST /api/retrieve semantic search).
    "integral_governed_query": ToolBinding(
        service_ref=_h("app.services.agent_capabilities", "governed_query"),
        service_param_map=_pick(
            "mode",
            "capability_key",
            "app_id",
            "params",
            "resource",
            "filters",
            "projection",
            "sort",
            "limit",
            "cursor",
            "max_depth",
            "retrieval_mode",
            "catalogue_generation",
        ),
    ),
    "integral_get_model_draft": ToolBinding(
        service_ref=_h(
            "app.services.operational_model_authoring", "get_or_create_draft"
        ),
        service_param_map=_pick("operational_model_id"),
    ),
    "integral_diff_model_draft": ToolBinding(
        service_ref=_h("app.services.operational_model_authoring", "diff_draft"),
        service_param_map=_pick("draft_id", "include_entry_impact", "sample_limit"),
    ),
    "integral_recommend_customizations": ToolBinding(
        service_ref=_h(
            "app.services.operational_model_authoring",
            "recommend_profile_customizations",
        ),
        service_param_map=_pick("track_id", "entry_sample_limit"),
    ),
    # ``count_entries_grouped`` / ``activity_digest`` (agent_insights) take a
    # ``workspace_id`` and apply the B-AGENT-03 workspace gate when it is set;
    # the dispatcher injects the BOUND SCOPE as ``workspace_id`` (signature
    # introspection in _dispatch_service_read), so the maps below carry the
    # NON-identity, NON-scope filter args only. ``group_by`` is required;
    # ``statuses`` is accepted alongside ``status`` (the service merges both).
    "integral_count_entries": ToolBinding(
        service_ref=_h("app.services.agent_insights", "count_entries_grouped"),
        service_param_map=_pick(
            "group_by",
            "track_id",
            "status",
            "statuses",
            "tags",
            "entry_type",
            "since",
            "until",
        ),
    ),
    "integral_activity_digest": ToolBinding(
        service_ref=_h("app.services.agent_insights", "activity_digest"),
        service_param_map=_pick("scope", "scope_id", "period"),
    ),
    "integral_list_workspace_tools": ToolBinding(
        service_ref=_h("app.services.workspace_tools", "list_workspace_tools"),
        service_param_map=_pick(),
    ),
    # ---- PROPOSE tools (Task 5a) -------------------------------------------
    # Each carries a ``stager`` mapping tool args -> create_staged_change kwargs.
    # dispatch_tool's op_class=="propose" branch routes through _dispatch_propose,
    # which binds principal+scope, runs the stager (sync or async), and mints a
    # pending StagedChange. The bless (out of scope here) runs the executor.
    "integral_create_entry": ToolBinding(stager=_stage_create_entry),
    # integral_file_content (Task 5): hint-resolve→stage. The async stager
    # (stagers_filing.stage_file_content) resolves track/type from agent-supplied
    # hints + optional learned personalization under the bound principal/scope.
    # Resolved → a ``file_content`` staged change; missing hints → ``_no_stage``
    # carrying ``_kind: filing_candidates`` (no StagedChange minted).
    "integral_file_content": ToolBinding(stager=stage_file_content),
    "integral_update_entry": ToolBinding(stager=_stage_update_entry),
    "integral_delete_entry": ToolBinding(stager=_stage_delete_entry),
    # ---- Phase 0 enabler tools ----
    "integral_bulk_update_entries": ToolBinding(stager=_stage_bulk_update_entries),
    "integral_bulk_delete_entries": ToolBinding(stager=_stage_bulk_delete_entries),
    "integral_add_entry_tag": ToolBinding(stager=_stage_add_entry_tag),
    "integral_remove_entry_tag": ToolBinding(stager=_stage_remove_entry_tag),
    "integral_create_tag": ToolBinding(stager=_stage_create_tag),
    "integral_update_app": ToolBinding(stager=_stage_update_app),
    "integral_delete_app": ToolBinding(stager=_stage_delete_app),
    "integral_link_entries": ToolBinding(stager=_stage_link_entries),
    "integral_transform_entry": ToolBinding(stager=_stage_transform_entry),
    "integral_add_comment": ToolBinding(stager=_stage_add_comment),
    "integral_create_app": ToolBinding(stager=_stage_create_app),
    "integral_draft_new_model": ToolBinding(stager=_stage_draft_new_profile),
    "integral_create_track": ToolBinding(stager=_stage_create_track),
    "integral_create_app_track": ToolBinding(stager=_stage_create_app_track_async),
    "integral_update_track": ToolBinding(stager=_stage_update_track),
    "integral_delete_track": ToolBinding(stager=_stage_delete_track),
    "integral_save_view": ToolBinding(stager=_stage_save_view),
    "integral_create_dashboard": ToolBinding(stager=_stage_create_dashboard),
    "integral_update_dashboard": ToolBinding(stager=_stage_update_dashboard),
    "integral_delete_dashboard": ToolBinding(stager=_stage_delete_dashboard),
    "integral_apply_model_to_track": ToolBinding(
        stager=_stage_apply_library_operational_model
    ),
    "integral_discard_model_draft": ToolBinding(stager=_stage_discard_profile_draft),
    "integral_modify_model": ToolBinding(stager=_stage_modify_operational_model),
    "integral_propose_model_revision": ToolBinding(
        stager=_stage_propose_profile_revision
    ),
    "integral_publish_model_draft": ToolBinding(stager=_stage_publish_profile_draft),
    "integral_author_model": ToolBinding(stager=_stage_author_operational_model),
    # ---- T5c: share (stage-and-bless) + set_focus (direct-execute) ---------
    # integral_share RECONCILED (T5c): the legacy ``integral_share`` branch added
    # a collaborator (track|app, email→user resolution). The ``share`` executor
    # (_x_share) replicates that via the per-resource collaborator handlers
    # (add_app_collaborator / add_collaborator / add_entry_collaborator) — entry
    # now supported too — resolving email→user at bless. The manifest's
    # share-link mode is the dedicated mint path, not this convenience tool, so
    # this stages the collaborator path the legacy tool actually performed.
    "integral_share": ToolBinding(stager=_stage_share),
    "integral_add_collaborator": ToolBinding(stager=_stage_add_collaborator),
    "integral_remove_collaborator": ToolBinding(stager=_stage_remove_collaborator),
    "integral_set_exclusion": ToolBinding(stager=_stage_set_exclusion),
    "integral_remove_exclusion": ToolBinding(stager=_stage_remove_exclusion),
    "integral_mint_share_link": ToolBinding(stager=_stage_mint_share_link),
    "integral_revoke_share_link": ToolBinding(stager=_stage_revoke_share_link),
    "integral_invite": ToolBinding(stager=_stage_invite),
    "integral_edit_comment": ToolBinding(stager=_stage_edit_comment),
    "integral_delete_comment": ToolBinding(stager=_stage_delete_comment),
    "integral_delete_view": ToolBinding(stager=_stage_delete_view),
    "integral_attach_file": ToolBinding(stager=_stage_attach_file),
    "integral_attach_uploaded_file_to_entry": ToolBinding(
        stager=_stage_attach_uploaded_file
    ),
    "integral_attach_uploaded_image_to_entry": ToolBinding(
        stager=_stage_attach_uploaded_image
    ),
    "integral_author_skill": ToolBinding(stager=_stage_author_skill),
    "integral_update_skill": ToolBinding(stager=_stage_update_skill),
    "integral_delete_skill": ToolBinding(stager=_stage_delete_skill),
    "integral_resolve_conflict": ToolBinding(stager=_stage_resolve_conflict),
    "integral_trigger_sync": ToolBinding(stager=_stage_trigger_sync),
    "integral_call_workspace_tool": ToolBinding(stager=_stage_call_workspace_tool),
    # integral_set_focus (T5c): EPHEMERAL conversation state, NOT a substrate
    # write — runs immediately via the direct_ref path (no StagedChange / bless;
    # PC-8 does not apply). Stays in _STAGING_EXEMPT_PROPOSE_TOOLS. The service
    # needs a context_id (which conversation to focus); the external dispatch
    # contract carries none, so it fails closed there — the RESIDENT supplies the
    # live context_id (see set_focus_for_dispatch docstring + T5c report).
    "integral_set_focus": ToolBinding(
        direct_ref=_h(
            "app.agentive.services.conversation_context", "set_focus_for_dispatch"
        ),
        direct_param_map=_set_focus_direct_map,
    ),
    # integral_propose_design: like the batch-control tools, it needs session_id
    # from dispatch context (to key the thread marker by provider_session_id),
    # which no binding ref carries — so it is intercepted BY NAME in
    # ``_dispatch_propose`` (calling ``record_design_proposed`` directly) rather
    # than via a stager/direct_ref. Its binding is therefore an all-None sentinel,
    # exactly like the batch-control tools; the catalogue advertises it via the
    # ``_INTERCEPTED_EPHEMERAL_TOOLS`` name check in ``_is_dispatchable``.
    "integral_propose_design": ToolBinding(stager=None),
    # Session artifacts: same interception pattern as propose_design.
    "integral_upsert_artifact": ToolBinding(stager=None),
    "integral_get_artifact": ToolBinding(stager=None),
    "integral_list_artifacts": ToolBinding(stager=None),
    # integral_ask_user: same reason as integral_propose_design — it keys a
    # thread marker by provider_session_id, so it needs the dispatch-context
    # session_id that no binding ref carries. Intercepted by name in
    # ``_dispatch_propose``; advertised via ``_INTERCEPTED_EPHEMERAL_TOOLS``.
    "integral_ask_user": ToolBinding(stager=None),
    "integral_mark_notification_read": ToolBinding(
        direct_ref=_h(
            "app.agentive.services.direct_tools",
            "mark_notification_read_for_dispatch",
        ),
        direct_param_map=_mark_notification_read_direct_map,
    ),
    "integral_invoke_app_operation": ToolBinding(
        direct_ref=_h(
            "app.agentive.services.direct_tools",
            "invoke_app_operation_for_dispatch",
        ),
        direct_param_map=_invoke_app_operation_direct_map,
    ),
    # Batch-control tools (Phase 0 batch staging). Intercepted by name in
    # ``_dispatch_propose`` (they need ``session_id`` from dispatch context, which
    # the direct_ref path does not carry), so the binding is a stable non-None
    # sentinel — no stager/direct_ref of its own. See ``_BATCH_CONTROL_TOOLS``.
    "integral_begin_batch": ToolBinding(stager=None),
    "integral_commit_batch": ToolBinding(stager=None),
    "integral_cancel_batch": ToolBinding(stager=None),
    # ---- T5c: deferred orchestrations (no clean single-mutation binding) ----
    # integral_workspace_setup — DEFERRED: the legacy code has NO execute_tool
    # branch and no backing orchestration service (only a persona metadata
    # description), so there is nothing to stage. Explicit stager=None binding
    # gives a stable ``not_implemented`` (not ``unknown_tool``) until a real
    # workspace-template orchestration service exists. Stays staging-exempt.
    "integral_workspace_setup": ToolBinding(stager=None),
    # integral_onboard_user — DEFERRED: a genuine MULTI-TURN state machine
    # (step + context → next_step/prompt; per-step writes), not a single
    # stageable mutation. Resident-orchestration-only — not an external-MCP
    # stage-and-bless tool. Explicit stager=None → ``not_implemented``.
    "integral_onboard_user": ToolBinding(stager=None),
    # Attachment reads bind to a dedicated agent service (not the HTTP routes):
    # the list strips ``extracted_text`` to a summary and the text read caps the
    # body for the model context — neither shape the shared FE endpoints emit.
    "integral_list_attachments": ToolBinding(
        service_ref=_h("app.services.attachment_agent", "list_attachments_for_entry"),
        service_param_map=_pick("entry_id"),
    ),
    "integral_list_track_attachments": ToolBinding(
        service_ref=_h("app.services.attachment_agent", "list_attachments_for_track"),
        service_param_map=_pick("track_id"),
    ),
    # workspace_id is NOT forwarded: the dispatcher injects the bound scope, so
    # this reads the conversation's workspace and nothing else. Forwarding it
    # let a scoped conversation read another workspace's files on membership
    # alone (PC-2). A mismatched arg now gets the actionable scope refusal.
    "integral_list_workspace_attachments": ToolBinding(
        service_ref=_h(
            "app.services.attachment_agent", "list_attachments_for_workspace"
        ),
        service_param_map=_pick(),
    ),
    "integral_get_attachment_text": ToolBinding(
        service_ref=_h("app.services.attachment_agent", "get_attachment_text"),
        service_param_map=_pick("attachment_id"),
    ),
    # Speech-to-text over a stored audio file. workspace_id is NOT forwarded:
    # the dispatcher injects the bound scope, which picks the provider key and
    # must match the attachment's workspace (PC-2).
    "integral_transcribe_audio": ToolBinding(
        service_ref=_h(
            "app.agentive.services.speech.service", "transcribe_attachment_for_agent"
        ),
        service_param_map=_pick("attachment_id", "language", "max_chars"),
    ),
    # Routine tasks (P_scheduling) — user-issued recurring chat instructions.
    "integral_schedule_task": ToolBinding(stager=stage_schedule_task),
    "integral_list_routines": ToolBinding(
        service_ref=_h("app.agentive.services.routine_tasks", "list_routine_tasks"),
        service_param_map=_pick(),
    ),
    "integral_update_routine": ToolBinding(stager=stage_update_routine),
    "integral_cancel_routine": ToolBinding(stager=stage_cancel_routine),
    "integral_delete_routine": ToolBinding(stager=stage_delete_routine),
}
