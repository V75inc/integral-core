"""Hook-point catalog + per-workspace tool/hook registration (DR-30-02).

Hook points are FROZEN (I-HOOK-01). Tools + hook bindings are
registered per workspace at App install time (services/app_lifecycle).

ToolContext is the SOLE substrate-access surface bundles see. Tools
never import from app.services / app.models directly.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple

from app.utils.time import utc_now_iso

# HookMisconfiguredError imported lazily inside functions to avoid circular:
# registry → errors → api.errors → api.__init__ → entries_precompute → registry (partial)

logger = logging.getLogger(__name__)

# I-HOOK-01: frozen catalog. Adding a hook point requires a Decision Record.
HOOK_POINTS: frozenset = frozenset(
    {
        "entry.transform",
        "entry.public_share",
        "entry.precompute",
        "entry.create",  # DR-32-01: entry-save side effects belong in bundles (leave balance)
        "entry.validate",  # pre-write bundle validation; failures reject the write
        "entry.update",  # DR-32-01: entry-save side effects belong in bundles (leave balance)
        "connector.dedup",
        "connector.auto_link",
    }
)

# Per-workspace registration tables. Keyed by workspace_id.
#   _TOOLS[ws_id]: Dict[tool_key, spec_dict] — spec includes
#     handler_ref (resolved module+callable cached on dispatch).
#   _HOOKS[ws_id]: Dict[point, List[binding_dict]] — bindings
#     ordered by registration order (bundle install order).
_TOOLS: Dict[str, Dict[str, Dict[str, Any]]] = {}
_HOOKS: Dict[str, Dict[str, List[Dict[str, Any]]]] = {}


def register_workspace_tools(
    workspace_id: str, bundle_slug: str, tools: List[Dict[str, Any]]
) -> None:
    """Cache compiled tool specs for a workspace.

    Called by ``services/app_lifecycle.install_hook`` after a trusted
    bundle is installed. Idempotent — re-registration overwrites.
    """
    if not tools:
        return
    from app.services.hooks.errors import HookMisconfiguredError

    bucket = _TOOLS.setdefault(workspace_id, {})
    for spec in tools:
        key = str(spec.get("key") or "").strip()
        if not key:
            raise HookMisconfiguredError(
                message=f"tool spec missing 'key' (bundle {bundle_slug})"
            )
        # A tool key is a workspace capability name. A second bundle may not
        # silently replace it: that would change a live capability's authority
        # and handler without an install/upgrade decision. Re-registering the
        # same bundle remains idempotent for restart and manifest refresh.
        existing = bucket.get(key)
        existing_bundle = str((existing or {}).get("_bundle_slug") or "")
        if existing is not None and existing_bundle != bundle_slug:
            raise HookMisconfiguredError(
                message=(
                    f"tool key {key!r} is already registered by bundle "
                    f"{existing_bundle!r}"
                ),
                details={
                    "workspace_id": workspace_id,
                    "tool_key": key,
                    "existing_bundle": existing_bundle,
                    "requested_bundle": bundle_slug,
                },
            )
        # Stamp the source bundle for debug / audit.
        spec_with_meta = {**spec, "_bundle_slug": bundle_slug}
        bucket[key] = spec_with_meta
    logger.info(
        "registered %d tools for workspace %s (bundle %s)",
        len(tools),
        workspace_id,
        bundle_slug,
    )


def register_workspace_hooks(
    workspace_id: str, bundle_slug: str, hooks: List[Dict[str, Any]]
) -> None:
    """Cache compiled hook bindings for a workspace.

    Order preserved per point (matches bundle install order).
    """
    if not hooks:
        return
    from app.services.hooks.errors import HookMisconfiguredError

    ws_bucket = _HOOKS.setdefault(workspace_id, {})
    for binding in hooks:
        point = str(binding.get("point") or "").strip()
        if point not in HOOK_POINTS:
            raise HookMisconfiguredError(
                message=(
                    f"unknown hook point {point!r} " f"(allowed: {sorted(HOOK_POINTS)})"
                ),
                details={"bundle_slug": bundle_slug, "binding_key": binding.get("key")},
            )
        binding_with_meta = {**binding, "_bundle_slug": bundle_slug}
        ws_bucket.setdefault(point, []).append(binding_with_meta)
    logger.info(
        "registered %d hook bindings for workspace %s (bundle %s)",
        len(hooks),
        workspace_id,
        bundle_slug,
    )


def get_workspace_tools(workspace_id: str) -> Dict[str, Dict[str, Any]]:
    """Return a snapshot of the tool specs registered for ``workspace_id`` (keyed by tool key)."""
    return dict(_TOOLS.get(workspace_id) or {})


def get_workspace_hooks(workspace_id: str, point: str) -> List[Dict[str, Any]]:
    """Return the hook bindings registered for ``workspace_id`` at extension point ``point``."""
    return list((_HOOKS.get(workspace_id) or {}).get(point) or [])


def clear_workspace_registrations(workspace_id: str) -> None:
    """Drop all tool + hook registrations for a workspace.

    Prefer :func:`unregister_bundle_registrations` when uninstalling a
    single bundle so sibling apps in the same workspace keep their hooks.
    """
    _TOOLS.pop(workspace_id, None)
    _HOOKS.pop(workspace_id, None)


def unregister_bundle_registrations(workspace_id: str, bundle_slug: str) -> None:
    """Remove tools + hook bindings contributed by one bundle install."""
    if not bundle_slug:
        logger.warning(
            "unregister_bundle_registrations: empty bundle_slug for workspace %s",
            workspace_id,
        )
        return

    tool_bucket = _TOOLS.get(workspace_id)
    if tool_bucket is not None:
        for key in [
            k
            for k, spec in tool_bucket.items()
            if spec.get("_bundle_slug") == bundle_slug
        ]:
            del tool_bucket[key]
        if not tool_bucket:
            _TOOLS.pop(workspace_id, None)

    hook_bucket = _HOOKS.get(workspace_id)
    if hook_bucket is not None:
        for point in list(hook_bucket.keys()):
            hook_bucket[point] = [
                binding
                for binding in hook_bucket[point]
                if binding.get("_bundle_slug") != bundle_slug
            ]
            if not hook_bucket[point]:
                del hook_bucket[point]
        if not hook_bucket:
            _HOOKS.pop(workspace_id, None)

    logger.info(
        "unregistered bundle %s from workspace %s",
        bundle_slug,
        workspace_id,
    )


@dataclass
class ToolContext:
    """Bundle-tool access facade (DR-30-01).

    Tools NEVER import app.services / app.models directly. All
    substrate calls flow through this context so the substrate can
    revoke / audit / scope-gate access uniformly.

    Methods are async + lazy-loaded so this module stays import-light.
    """

    user_id: str
    workspace_id: str
    scope: str
    #: Slug of the bundle whose tool is running. Stamped by
    #: ``tool_dispatch.run_tool`` from the registered spec — a tool never
    #: supplies it, so it cannot claim to be another bundle. Empty when a
    #: caller invokes the facade outside the tool dispatcher.
    bundle_slug: str = ""
    #: Policy subject kind for dual-gate tool invokes (human vs agent).
    actor_kind: str = "human"

    async def get_entry(self, entry_id: str):
        """Fetch an Entry node by id (scoped to caller's workspace access)."""
        from app.models.nodes import Entry
        from app.services.permissions import resolve_role

        ent = await Entry.get(entry_id)
        if ent is None:
            return None
        # Standard access gate.
        role = await resolve_role(self.user_id, "entry", entry_id)
        if role is None:
            return None
        return ent

    async def get(self, object_ref: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """Fetch a serialized object by ObjectRef-shaped dict (ToolContext v2)."""
        kind = str((object_ref or {}).get("kind") or "entry").strip()
        oid = str((object_ref or {}).get("id") or "").strip()
        if not oid:
            return None
        if kind != "entry":
            return None
        ent = await self.get_entry(oid)
        if ent is None:
            return None
        return {
            "kind": "entry",
            "id": ent.id,
            "title": getattr(ent, "title", "") or "",
            "track_id": getattr(ent, "track_id", None),
            "type_id": getattr(ent, "type_id", None),
            "custom_fields": dict(getattr(ent, "custom_fields", None) or {}),
            "workspace_id": self.workspace_id,
        }

    async def query(self, query_spec: Dict[str, Any]) -> Dict[str, Any]:
        """Execute a governed QuerySpec (ADR-012)."""
        from app.schemas.governed_query import QuerySpec
        from app.services.governed_query import execute_query

        spec = QuerySpec.model_validate(query_spec)
        result = await execute_query(
            user_id=self.user_id,
            workspace_id=self.workspace_id,
            spec=spec,
        )
        return result.model_dump()

    async def invoke(
        self, operation_key: str, payload: Optional[Dict[str, Any]] = None
    ) -> Dict[str, Any]:
        """Invoke a typed App operation for this context's app (if set)."""
        from app.services.app_operations.dispatch import invoke_app_operation

        app_id = str(getattr(self, "app_id", "") or "").strip()
        if not app_id:
            raise ValueError("invoke requires app_id on context")
        return await invoke_app_operation(
            user_id=self.user_id,
            workspace_id=self.workspace_id,
            app_id=app_id,
            operation_key=operation_key,
            payload=payload or {},
            idempotency_key=getattr(self, "idempotency_key", None),
            correlation_id=getattr(self, "correlation_id", None),
        )

    async def find_entries(self, query: Dict[str, Any]) -> List[Any]:
        """Find entries matching a jvspatial query, scoped to this workspace.

        Callers pass a raw jvspatial query dict. Entry nodes do NOT carry a
        ``workspace_id`` field (verified: 0/405 entries match a
        ``context.workspace_id`` filter), so AND-merging that key silently
        returned ZERO rows for every query — breaking any bundle tool that
        reads entries (e.g. leave-balance recalc summed 0 approved requests).

        Entries are workspace-scoped through their Track (which DOES carry
        ``workspace_id``). Run the caller's query, then keep only entries whose
        Track belongs to this workspace — no cross-workspace leak, and the
        query actually matches.
        """
        from app.models.nodes import Entry, Track

        # resolve_role is used in the per-entry gate below. The sibling helpers
        # in this class each import it locally; this one did not, so the first
        # entry that reached the permission check would raise NameError. mypy
        # caught it as `Name "resolve_role" is not defined` — it is unreachable
        # only while the query returns nothing, which is precisely the bug the
        # docstring above says was just fixed.
        from app.services.permissions import resolve_role

        rows = list(await Entry.find(query))
        if not rows:
            return []
        track_in_ws: Dict[str, bool] = {}
        scoped: List[Any] = []
        for entry in rows:
            track_id = getattr(entry, "track_id", None)
            if not track_id:
                continue
            if track_id not in track_in_ws:
                track = await Track.get(track_id)
                track_in_ws[track_id] = bool(
                    track and getattr(track, "workspace_id", None) == self.workspace_id
                )
            if not track_in_ws[track_id]:
                continue
            role = await resolve_role(self.user_id, "entry", entry.id)
            if role is None:
                continue
            scoped.append(entry)
        return scoped

    async def _tracks_by_title(self, track_type: str) -> List[Any]:
        """Tracks matching ``track_type`` by title-slug, scoped to this
        workspace, EXCLUDING tracks whose parent App has been uninstalled.

        The type is matched slug-insensitively against each track's title.
        Lives on the facade so bundle tools never import app.models/Track to
        do a workspace-scoped, type-filtered entry walk — the substrate stays
        the single scope/audit gate. Returns ``[]`` for an empty type.

        Each matching track is gated with ``resolve_role(..., "track", …)`` and
        each entry with ``resolve_role(..., "entry", …)`` — same ACL as
        :meth:`find_entries`.
        """
        from app.models.edges import CONTAINS
        from app.models.nodes import Track
        from app.services.operational_model_compile import _slug
        from app.services.permissions import resolve_role

        want = _slug(track_type or "")
        if not want:
            return []
        out: List[Any] = []
        for track in await Track.find({"context.workspace_id": self.workspace_id}):
            if _slug(getattr(track, "title", "") or "") != want:
                continue
            if await resolve_role(self.user_id, "track", track.id) is None:
                continue
            try:
                # App's stored discriminator is "WorkspaceApp"
                # (__entity_name__ = "WorkspaceApp" — see app/models/nodes.py),
                # not "App" — node=["App"] silently matches nothing.
                parents = await track.nodes(
                    edge=[CONTAINS], node=["WorkspaceApp"], direction="in"
                )
            except Exception:  # noqa: BLE001
                parents = []
            if parents and not any(
                getattr(p, "lifecycle_state", "active") == "active" for p in parents
            ):
                continue
            out.append(track)
        return out

    async def find_entries_in_track_type(
        self, track_type: str, entry_type: Optional[str] = None
    ) -> List[Any]:
        """Entries on tracks of a given type, scoped to this workspace.

        The type is matched slug-insensitively against each track's title.
        Lives on the facade so bundle tools never import app.models/Track to
        do a workspace-scoped, type-filtered entry walk — the substrate stays
        the single scope/audit gate. Returns ``[]`` for an empty type.
        """
        from app.models.nodes import Entry, EntryType
        from app.services.operational_model_compile import _slug
        from app.services.permissions import resolve_role

        wanted_type = _slug(entry_type) if entry_type else ""
        type_key_cache: Dict[str, str] = {}

        async def _entry_type_slug(type_id: str) -> str:
            if not type_id:
                return ""
            if type_id not in type_key_cache:
                et = await EntryType.get(type_id)
                if et is None:
                    type_key_cache[type_id] = ""
                else:
                    manifest_key = str(
                        (et.form_schema or {}).get("_manifest_entry_type_key") or ""
                    ).strip()
                    type_key_cache[type_id] = _slug(manifest_key or et.name)
            return type_key_cache[type_id]

        out: List[Any] = []
        for track in await self._tracks_by_title(track_type):
            try:
                entries = await track.nodes(
                    edge=["CONTAINS"], direction="out", node=["Entry"]
                )
            except Exception:  # noqa: BLE001
                continue
            for e in entries:
                if not isinstance(e, Entry):
                    continue
                if wanted_type and await _entry_type_slug(e.type_id) != wanted_type:
                    continue
                if await resolve_role(self.user_id, "entry", e.id) is None:
                    continue
                out.append(e)
        return out

    async def find_track_id_by_title(self, track_type: str) -> Optional[str]:
        """Resolve a track's id by its title, scoped to this workspace.

        Same title-slug matching as ``find_entries_in_track_type`` (which
        can't help here — an empty track returns no entries to read a
        ``track_id`` off of). Added alongside ``create_entry``: creating
        the FIRST entry in a track a tool has never written to before
        needs the track's id, not its entries. Returns ``None`` if no
        (live) track with that title exists in this workspace.
        """
        tracks = await self._tracks_by_title(track_type)
        return tracks[0].id if tracks else None

    async def get_employee_compensation(self, employee_id: str) -> Optional[float]:
        """Resolve an Employee entry → latest Compensation Record → annual_pay.

        Generic graph walk over REFERENCES for ``base_salary`` — no domain
        shim. Returns None if unresolved or the caller has no role on the
        employee entry.
        """
        from app.services.permissions import resolve_role

        if not (self.user_id or "").strip() or not (employee_id or "").strip():
            return None
        if await resolve_role(self.user_id, "entry", employee_id) is None:
            return None
        try:
            from app.models.nodes import Entry
        except Exception:  # noqa: BLE001
            return None
        emp = await Entry.get(employee_id)
        if emp is None:
            return None
        try:
            comps = await emp.nodes(edge=["REFERENCES"], direction="in", node=["Entry"])
        except Exception:  # noqa: BLE001
            return None
        if not comps:
            return None

        def _effective(c) -> str:
            return str(
                (getattr(c, "custom_fields", {}) or {}).get("effective_date") or ""
            )

        for c in sorted(comps, key=_effective, reverse=True):
            if await resolve_role(self.user_id, "entry", getattr(c, "id", "")) is None:
                continue
            cf = getattr(c, "custom_fields", {}) or {}
            pay = cf.get("base_salary")
            if pay is not None:
                try:
                    return float(pay)
                except Exception:  # noqa: BLE001
                    continue
        return None

    async def get_app_settings(self, app_key: str) -> Dict[str, Any]:
        """Return the ``settings`` dict of the App installed from ``app_key``
        in this workspace — ``{}`` if that App isn't installed (or has no
        ``settings_schema`` values set). Lets a bundle's own hooks/tools read
        an app-level toggle (e.g. "sync employees from HRM if HRM is
        installed") without importing app.models directly — same
        target-App-key resolution ``relation.target_app`` cross-App relations
        already use, so an app's dependency key and its settings lookup key
        are always the same string.
        """
        from app.exceptions import CrossAppTargetNotFoundError
        from app.models.nodes import App
        from app.services.relation_runtime import resolve_target_app

        try:
            app_id = await resolve_target_app(
                workspace_id=self.workspace_id, target_app_key=app_key
            )
        except CrossAppTargetNotFoundError:
            return {}
        app_node = await App.get(app_id)
        return dict(getattr(app_node, "settings", None) or {}) if app_node else {}

    async def emit_audit(self, action: str, details: Dict[str, Any]) -> None:
        """Emit a ChangeEvent for tool-side audit."""
        from typing import cast

        from app.schemas.audit import ChangeEventAction
        from app.services.change_event import emit_change_event

        await emit_change_event(
            actor_kind="human",
            actor_id=self.user_id,
            action=cast(ChangeEventAction, action),
            resource_type="Tool",
            resource_id="",
            before=None,
            after={"details": details, "tool_invocation": True},
            scope=self.scope,
        )

    async def get_entry_system(self, entry_id: str):
        """Fetch Entry by id — trusted bundle tools only; requires explicit principal."""
        from app.models.nodes import Entry
        from app.services.permissions import resolve_role

        if not (self.user_id or "").strip():
            return None
        ent = await Entry.get(entry_id)
        if ent is None:
            return None
        role = await resolve_role(self.user_id, "entry", entry_id)
        if role is None:
            return None
        return ent

    async def update_entry_fields(
        self, entry_id: str, custom_fields: Dict[str, Any]
    ) -> bool:
        """Merge custom_fields onto entry and persist (trusted bundle tools only).

        Emits a ChangeEvent the same way ``create_entry``/``attach_file`` on
        this facade already do — this was the one write path here that
        didn't (found live: a Pay Run's Finalize button moving it
        draft -> approved, and Recompute Lines rewriting every line's
        gross-to-net figures, both go through this exact method and were
        producing NO audit trail at all — the single most audit-worthy
        action in the whole payroll app was invisible to `GET /audit-log`).
        ``before``/``after`` are scoped to just the keys being changed, not
        the whole custom_fields blob, so the entry stays readable in a diff
        view instead of a wall of unrelated fields.
        """
        from app.models.nodes import Entry
        from app.services.permissions import resolve_role

        if not (self.user_id or "").strip():
            return False
        # Same role set as services/permissions.py::can_edit_entry — omitting
        # "admin" here would deny a legitimate admin the write that the HTTP
        # path grants them.
        role = await resolve_role(self.user_id, "entry", entry_id)
        if role not in ("owner", "admin", "editor"):
            return False
        ent = await Entry.get(entry_id)
        if ent is None:
            return False
        existing = ent.custom_fields or {}
        before = {k: existing.get(k) for k in custom_fields}
        ent.custom_fields = {**existing, **custom_fields}
        await ent.save()

        from app.services.change_event import emit_change_event

        await emit_change_event(
            actor_kind="agent",
            actor_id=self.user_id,
            action="entry.update",
            resource_type="Entry",
            resource_id=entry_id,
            before=before,
            after=dict(custom_fields),
            scope=f"tool:{self.scope}",
        )
        return True

    async def conditional_update_entry_fields(
        self,
        entry_id: str,
        *,
        state_field: str,
        expected_state: str,
        updates: Dict[str, Any],
    ) -> Tuple[bool, Optional[str]]:
        """Atomically transition entry custom_fields when state matches expected."""
        from app.services.entry_conditional_update import (
            conditional_update_entry_custom_fields,
        )

        return await conditional_update_entry_custom_fields(
            user_id=self.user_id,
            entry_id=entry_id,
            state_field=state_field,
            expected_state=expected_state,
            updates=updates,
            scope=f"tool:{self.scope}",
        )

    async def _own_bundle_app(self) -> Optional[Any]:
        """The calling bundle's App in the caller's OWN personal workspace.

        The shared half of ADR-008's resolution: the bundle comes from
        ``self.bundle_slug``, which the dispatcher stamps from the registered
        spec (a tool cannot claim to be another bundle), and the workspace is
        the acting principal's own — never ``self.workspace_id``, which is
        wherever the hook happened to fire.

        Returns ``None`` on any missing link. Never raises.
        """
        from app.models.nodes import App
        from app.services.permissions import get_user_node
        from app.services.personal_workspace import ensure_personal_workspace

        slug = (self.bundle_slug or "").strip()
        if not slug or not (self.user_id or "").strip():
            return None
        try:
            user = await get_user_node(self.user_id)
            if user is None:
                return None
            workspace = await ensure_personal_workspace(user)
            if workspace is None or getattr(workspace, "kind", "") != "personal":
                return None
            found = await App.find({"context.source_operational_model_slug": slug})
            return next(
                (
                    a
                    for a in list(found or [])
                    if getattr(a, "workspace_id", "") == workspace.id
                    and getattr(a, "lifecycle_state", "") == "active"
                ),
                None,
            )
        except Exception:  # noqa: BLE001
            logger.exception("_own_bundle_app failed (bundle=%s)", slug)
            return None

    async def own_bundle_view(self) -> Optional[Dict[str, Any]]:
        """What the calling bundle has in the caller's own personal workspace.

        Returns ``{"app_id", "workspace_id", "settings", "track_ids"}`` —
        where ``track_ids`` maps MANIFEST TRACK KEY to track id — or ``None``
        when this bundle is not installed there.

        A read counterpart to :meth:`create_entry_in_own_bundle_track`, for
        the checks a tool has to make BEFORE writing: is this the App's own
        entry (a feedback loop), has the person switched the behaviour off,
        is this workspace excluded. Scoped to the calling bundle's own App,
        so it exposes nothing a bundle does not already own.
        """
        from app.models.edges import CONTAINS

        app_node = await self._own_bundle_app()
        if app_node is None:
            return None
        try:
            tracks = await app_node.nodes(
                edge=[CONTAINS], node=["Track"], direction="out"
            )
        except Exception:  # noqa: BLE001
            logger.exception("own_bundle_view: track walk failed")
            return None
        return {
            "app_id": app_node.id,
            "workspace_id": str(getattr(app_node, "workspace_id", "") or ""),
            "settings": dict(getattr(app_node, "settings", None) or {}),
            "track_ids": {
                str(getattr(t, "template_id", "") or ""): t.id
                for t in tracks
                if str(getattr(t, "template_id", "") or "")
            },
        }

    async def track_kind(self, track_id: str) -> str:
        """``Track.kind`` for ``track_id``, or ``""``.

        A generic field read, gated by the caller's access to the track. Its
        one use today is letting a tool recognise substrate-managed tracks it
        should leave alone — ``agent_scratch`` is the resident's working
        memory (I-SCRATCH-02 makes ``kind`` the queryable discriminator, and
        title-matching explicitly wrong).
        """
        from app.models.nodes import Track
        from app.services.permissions import resolve_role

        if not track_id or not (self.user_id or "").strip():
            return ""
        try:
            if await resolve_role(self.user_id, "track", track_id) is None:
                return ""
            track = await Track.get(track_id)
            return str(getattr(track, "kind", "") or "") if track else ""
        except Exception:  # noqa: BLE001
            logger.exception("track_kind failed for %s", track_id)
            return ""

    async def create_entry_in_own_bundle_track(
        self,
        track_key: str,
        *,
        title: str,
        body: str = "",
        fields: Optional[Dict[str, Any]] = None,
        entry_type_key: str = "",
    ) -> Optional[str]:
        """Create an entry in the calling bundle's own track, in the caller's
        own personal workspace. Returns the new entry id, or ``None``.

        ADR-008, in its narrow form. A hook fires in whichever workspace the
        entry was saved in, but some bundles keep a per-person record that
        belongs in that person's OWN workspace — so the target cannot be
        reached through ``self.workspace_id``, and until now could not be
        reached at all: the facade had no create of any kind.

        Deliberately fused rather than split into a generic ``create_entry``
        plus a track lookup. A general create would let every trusted bundle
        write into any track its caller can write to — a far larger surface
        than the need that motivated it. This one cannot address anything
        except a track of an App installed from **this tool's own bundle**,
        in a ``kind="personal"`` workspace **owned by the acting principal**,
        matched by MANIFEST TRACK KEY (``Track.template_id``) so a track
        somebody made by hand and named the same thing is not a target.

        The write goes through ``entry_writer.create_entry_internal``, so the
        policy gate, operational-model validation, change events and audit all
        run exactly as on the HTTP path (I-CRUD-01).

        ``entry_type_key`` names the manifest entry type; when omitted the
        track's default is resolved. An entry MUST carry a type: a typeless
        row is invisible in any view with ``entry_type_keys`` and carries no
        field schema, so it exists in the database and nowhere a person
        looks. ``create_entry_internal`` defaults ``type_id`` to empty and
        does not resolve one — only the HTTP handler does — which is exactly
        how three of four observations went missing from the Stream view in
        the browser while every DB assertion passed.

        Returns ``None`` — never raises — on any missing link or refusal. A
        hook that cannot record must not fail the save that triggered it.
        """
        from app.services.entry_type_resolver import (
            default_entry_type_id_for_track,
            resolve_entry_type_id_by_key,
        )
        from app.services.entry_writer import create_entry_internal
        from app.services.permissions import resolve_role

        key = (track_key or "").strip()
        if not key:
            return None
        view = await self.own_bundle_view()
        if view is None:
            return None
        track_id = (view.get("track_ids") or {}).get(key)
        if not track_id:
            return None

        try:
            # Same role set as ``update_entry_fields`` and the HTTP path.
            role = await resolve_role(self.user_id, "track", track_id)
            if role not in ("owner", "admin", "editor"):
                logger.warning(
                    "create_entry_in_own_bundle_track: %s refused on track %s "
                    "(role=%r)",
                    self.user_id,
                    track_id,
                    role,
                )
                return None
            type_id = ""
            if entry_type_key:
                type_id = await resolve_entry_type_id_by_key(track_id, entry_type_key)
            if not type_id:
                type_id = await default_entry_type_id_for_track(track_id)
            if not type_id:
                logger.warning(
                    "create_entry_in_own_bundle_track: track %s has no entry "
                    "type; refusing rather than writing a row no view shows",
                    track_id,
                )
                return None

            created = await create_entry_internal(
                # The person's own save is what caused this, it runs under
                # their principal, and their role on the target track is what
                # gated it — so the audit actor is the human, exactly as it
                # would be for the write they actually made.
                #
                # The ROW says something different, and should: provenance
                # marks it agent-produced and names the bundle, so nothing
                # downstream mistakes a record the App wrote for something the
                # person typed. Actor and source are not the same question.
                actor_kind="human",
                actor_id=self.user_id,
                payload={
                    "track_id": track_id,
                    "title": title,
                    "type_id": type_id,
                    "body": body,
                    "custom_fields": dict(fields or {}),
                    "provenance": {
                        "source": "agent",
                        "source_id": f"bundle:{self.bundle_slug}",
                    },
                },
            )
        except Exception:  # noqa: BLE001
            logger.exception(
                "create_entry_in_own_bundle_track failed (bundle=%s key=%s)",
                self.bundle_slug,
                key,
            )
            return None
        return str((created or {}).get("id") or "") or None

    async def put_attachment(
        self,
        entry_id: str,
        content: bytes,
        filename: str,
        mime_type: str = "application/octet-stream",
    ) -> Optional[str]:
        """Create + wire an Attachment onto an Entry, return its id.

        Trusted bundle-tool write accessor (the render engine uses it to persist
        a rendered file). Least-privilege gated: the write only lands when the
        acting caller holds ``owner``/``editor`` on the target entry — a tool
        never attaches where the caller couldn't. Preserves **I-GRAPH-01**: the
        Attachment is wired to the Entry via ``HAS_ATTACHMENT`` in the same unit
        of work (never a floating node), and ``entry.attachment_ids`` is
        refreshed as the denormalized fast-path cache. Returns the attachment id,
        or ``None`` when the entry is missing / access is insufficient.
        """

        from app.models.edges import HAS_ATTACHMENT
        from app.models.nodes import Attachment, Entry
        from app.services.attachment_storage import get_attachment_storage_service
        from app.services.permissions import resolve_role

        ent = await Entry.get(entry_id)
        if ent is None:
            return None
        role = await resolve_role(self.user_id, "entry", entry_id)
        if role not in ("owner", "editor"):
            return None

        attachment = await Attachment.create(
            filename=filename,
            mime_type=mime_type,
            size=len(content),
            storage_key="",
            source_type="file",
            external_url="",
            uploaded_by=self.user_id,
            scan_status="pending",
            metadata_status="pending",
            created_at=utc_now_iso(),
        )
        storage = get_attachment_storage_service()
        try:
            stored = await storage.save_attachment(
                entry_id=ent.id,
                attachment_id=attachment.id,
                filename=filename,
                content=content,
            )
        except Exception:  # noqa: BLE001
            await attachment.delete()
            raise
        attachment.storage_key = str(stored.get("path") or "")
        await attachment.save()
        await ent.connect(
            attachment,
            edge=HAS_ATTACHMENT,
            attached_at=utc_now_iso(),
            attached_by=self.user_id,
        )
        if attachment.id not in (ent.attachment_ids or []):
            ent.attachment_ids = list(ent.attachment_ids or []) + [attachment.id]
            await ent.save()
        return attachment.id

    async def document_render(
        self,
        title: str,
        body: str,
        renderer: str,
        sections: Optional[List[Dict[str, str]]] = None,
        rows: Optional[List[Tuple[str, str]]] = None,
    ) -> bytes:
        """Render title/body/sections/rows to document bytes.

        Dispatches the substrate ``document_render`` engines (docx / pptx /
        pdf / markdown). Lives on the facade because bundle tools may not
        import ``app.services``; composition tools reach the engines only
        through here. Privilege redaction and attachment wiring stay in the
        calling tool.
        """
        from app.services.document_render import render_document

        return render_document(
            title=title,
            body=body,
            renderer=renderer,
            sections=sections or [],
            rows=rows or [],
        )

    async def rollup_plan(self, plan_id: str) -> Dict[str, Any]:
        """Roll a plan's status up from its linked items, return
        ``{open_count, at_risk}``.

        Dispatches the substrate ``PlanRollupWalker`` (jvspatial Pillar 3 — a
        multi-hop cascade is a walker, not procedural recursion). Lives on the
        facade because bundle tools may not import ``app.models`` / jvspatial;
        the ``rollup_status`` tool reaches the walker only through here.
        """
        from app.services.walkers.plan_rollup import roll_up_plan

        return await roll_up_plan(plan_id)
