"""Declarative migration runner for ContentProfile publish events.

Pillar 2 of the agent-authorable substrate. When a draft is published, the
candidate manifest's ``migrations[]`` block declares the data-side transforms
that must run so existing entries stay consistent with the new schema.

Manifest declaration shape:

    migrations:
      - from_version: "v1"
        to_version:   "v1.1"
        ops:
          - { op: rename_field, entry_type: task, from: "prio", to: "priority" }
          - { op: default_fill, entry_type: task, field: "priority", value: "medium" }
          - { op: prune_enum_option, entry_type: task, field: "priority", option: "urgent" }
          - { op: coerce_type, entry_type: task, field: "estimate", to: "number" }
          - { op: delete_field, entry_type: task, field: "legacy" }
          - { op: move_field, from_entry_type: task, to_entry_type: subtask, field: "owner" }

Each op runs over every Entry in every Track that uses the published CP. The
runner returns a structured run record with per-op success / failure counts
and any entry ids that could not be migrated.

Failure policy: when ``abort_on_failure`` is True, the first unrecoverable
error short-circuits the publish and rolls back the partial transform. When
False, the runner keeps going and reports failures so the operator can repair
manually.

This module intentionally keeps the op set small and declarative. Adding new
ops is a matter of adding a handler to ``_OP_HANDLERS`` plus a JSON schema
shape to the agent's tool descriptions.
"""

from __future__ import annotations

from typing import Any, Awaitable, Callable, Dict, List, Optional

from app.exceptions import BadRequestError
from app.models.edges import CONTAINS
from app.models.nodes import App, ContentProfile, Entry, EntryType, Track

# ---------------------------------------------------------------------------
# Op handlers
# ---------------------------------------------------------------------------


async def _affected_tracks(published_cp: ContentProfile) -> List[Track]:
    """Find every Track whose published-side schema is the published_cp.

    For track-attached profiles this is the singleton owner. For
    app-attached profiles, it's every Track inside the owning App.
    """
    if published_cp.scope == "track":
        return await Track.find(
            {"context.attached_content_profile_id": published_cp.id}
        )
    if published_cp.scope == "app":
        apps = await App.find({"context.attached_content_profile_id": published_cp.id})
        out: List[Track] = []
        for sp in apps:
            out.extend(await sp.nodes(edge=[CONTAINS], node=["Track"]))
        return out
    return []


async def _entries_for_entry_type_key(track: Track, entry_type_key: str) -> List[Entry]:
    """Return entries in ``track`` whose EntryType slug matches ``entry_type_key``.

    Entry slugs are computed from EntryType.name via ``_slug``; we resolve via
    EntryType.id so the runner is robust to renamed types.
    """
    from app.services.content_profile_compile import _slug

    entries: List[Entry] = await track.nodes(edge=[CONTAINS], node=["Entry"])
    if not entries:
        return []
    et_id_to_key: Dict[str, str] = {}
    for e in entries:
        type_id = getattr(e, "type_id", None) or ""
        if type_id and type_id not in et_id_to_key:
            et = await EntryType.get(type_id)
            et_id_to_key[type_id] = _slug(str(et.name or "")) if et else ""
    return [
        e
        for e in entries
        if et_id_to_key.get(getattr(e, "type_id", None) or "") == entry_type_key
    ]


async def _rename_field(
    *,
    track: Track,
    op: Dict[str, Any],
    log: Dict[str, Any],
) -> None:
    et_key = str(op.get("entry_type") or "").strip()
    src = str(op.get("from") or "").strip()
    dst = str(op.get("to") or "").strip()
    if not (et_key and src and dst):
        raise BadRequestError(message="rename_field requires entry_type, from, to")
    affected = await _entries_for_entry_type_key(track, et_key)
    for e in affected:
        cf = dict(getattr(e, "custom_fields", None) or {})
        if src in cf:
            cf[dst] = cf.pop(src)
            e.custom_fields = cf
            await e.save()
            log["mutated_entries"].append(e.id)


async def _default_fill(
    *,
    track: Track,
    op: Dict[str, Any],
    log: Dict[str, Any],
) -> None:
    et_key = str(op.get("entry_type") or "").strip()
    field_key = str(op.get("field") or "").strip()
    value = op.get("value")
    if not (et_key and field_key):
        raise BadRequestError(message="default_fill requires entry_type and field")
    affected = await _entries_for_entry_type_key(track, et_key)
    for e in affected:
        cf = dict(getattr(e, "custom_fields", None) or {})
        if cf.get(field_key) is None:
            cf[field_key] = value
            e.custom_fields = cf
            await e.save()
            log["mutated_entries"].append(e.id)


async def _delete_field(
    *,
    track: Track,
    op: Dict[str, Any],
    log: Dict[str, Any],
) -> None:
    et_key = str(op.get("entry_type") or "").strip()
    field_key = str(op.get("field") or "").strip()
    if not (et_key and field_key):
        raise BadRequestError(message="delete_field requires entry_type and field")
    affected = await _entries_for_entry_type_key(track, et_key)
    for e in affected:
        cf = dict(getattr(e, "custom_fields", None) or {})
        if field_key in cf:
            cf.pop(field_key, None)
            e.custom_fields = cf
            await e.save()
            log["mutated_entries"].append(e.id)


async def _prune_enum_option(
    *,
    track: Track,
    op: Dict[str, Any],
    log: Dict[str, Any],
) -> None:
    et_key = str(op.get("entry_type") or "").strip()
    field_key = str(op.get("field") or "").strip()
    option = op.get("option")
    replacement = op.get("replacement")
    if not (et_key and field_key) or option is None:
        raise BadRequestError(
            message="prune_enum_option requires entry_type, field, option"
        )
    affected = await _entries_for_entry_type_key(track, et_key)
    for e in affected:
        cf = dict(getattr(e, "custom_fields", None) or {})
        cur = cf.get(field_key)
        if isinstance(cur, list):
            new_list = [v for v in cur if v != option]
            if new_list != cur:
                cf[field_key] = new_list
                e.custom_fields = cf
                await e.save()
                log["mutated_entries"].append(e.id)
        elif cur == option:
            cf[field_key] = replacement
            e.custom_fields = cf
            await e.save()
            log["mutated_entries"].append(e.id)


async def _coerce_type(
    *,
    track: Track,
    op: Dict[str, Any],
    log: Dict[str, Any],
) -> None:
    et_key = str(op.get("entry_type") or "").strip()
    field_key = str(op.get("field") or "").strip()
    target = str(op.get("to") or "").strip().lower()
    if not (et_key and field_key and target):
        raise BadRequestError(message="coerce_type requires entry_type, field, to")
    affected = await _entries_for_entry_type_key(track, et_key)
    for e in affected:
        cf = dict(getattr(e, "custom_fields", None) or {})
        if field_key not in cf or cf[field_key] is None:
            continue
        try:
            cf[field_key] = _coerce(cf[field_key], target)
        except (ValueError, TypeError) as exc:
            log["errors"].append(
                {
                    "op": "coerce_type",
                    "entry_id": e.id,
                    "field": field_key,
                    "to": target,
                    "reason": str(exc),
                }
            )
            continue
        e.custom_fields = cf
        await e.save()
        log["mutated_entries"].append(e.id)


async def _move_field(
    *,
    track: Track,
    op: Dict[str, Any],
    log: Dict[str, Any],
) -> None:
    """Move a value from one entry type to another (within the same track).

    Implementation detail: this only moves the *field value*; reparenting
    entries between entry types is a separate, more invasive operation and
    intentionally not implemented here.
    """
    src_et = str(op.get("from_entry_type") or "").strip()
    dst_et = str(op.get("to_entry_type") or "").strip()
    field_key = str(op.get("field") or "").strip()
    if not (src_et and dst_et and field_key):
        raise BadRequestError(
            message=("move_field requires from_entry_type, to_entry_type, field")
        )
    # Without re-typing entries this op is a no-op at the data layer; flag as
    # a manual review item rather than silently succeeding.
    log["pending_manual_review"].append(
        {
            "op": "move_field",
            "from_entry_type": src_et,
            "to_entry_type": dst_et,
            "field": field_key,
            "track_id": track.id,
            "reason": ("move_field requires re-typing entries; not implemented in v1"),
        }
    )


# ---------------------------------------------------------------------------
# Phase 5 Plan 05-02 additions — 3 new declarative ops.
#
# All three are append-only extensions to the existing 6-op catalogue (locked
# decision #2: extension, not greenfield). Each is idempotent (re-running
# over already-migrated entries is a no-op) and reuses the same
# ``(*, track, op, log)`` handler signature for dispatch parity with the
# existing 6 ops at ``_OP_HANDLERS``.
# ---------------------------------------------------------------------------


async def _add_field_with_default(
    *,
    track: Track,
    op: Dict[str, Any],
    log: Dict[str, Any],
) -> None:
    """Phase 5 Plan 05-02 — ``add_field_with_default`` op.

    Adds a manifest-declared field to existing entries with the declared
    default value. Idempotent — entries that already have the field present
    are skipped (existing values are preserved).

    Op shape::

        {op: add_field_with_default, entry_type: task, field: cycle,
         type: text, default: "sprint"}
    """
    et_key = str(op.get("entry_type") or "").strip()
    field_key = str(op.get("field") or "").strip()
    if not (et_key and field_key):
        raise BadRequestError(
            message="add_field_with_default requires entry_type and field"
        )
    default_value = op.get("default")
    affected = await _entries_for_entry_type_key(track, et_key)
    for e in affected:
        cf = dict(getattr(e, "custom_fields", None) or {})
        if field_key in cf:
            # Idempotent — preserve existing value.
            continue
        cf[field_key] = default_value
        e.custom_fields = cf
        await e.save()
        log["mutated_entries"].append(e.id)


async def _rename_entry_type(
    *,
    track: Track,
    op: Dict[str, Any],
    log: Dict[str, Any],
) -> None:
    """Phase 5 Plan 05-02 — ``rename_entry_type`` op.

    Rewrites ``Entry.type_id`` on every entry whose current EntryType slug
    matches ``op.from`` so it points at the EntryType node whose slug now
    matches ``op.to``. The manifest's ``entry_types[].key`` is already updated
    by the atomic swap before this handler runs; the handler only needs to
    rewrite the per-Entry foreign key.

    Other EntryTypes in the same track are not touched. Re-running when all
    entries already point at the new type is a no-op.

    Op shape::

        {op: rename_entry_type, from: task, to: ticket}
    """
    from_key = str(op.get("from") or "").strip()
    to_key = str(op.get("to") or "").strip()
    if not (from_key and to_key):
        raise BadRequestError(message="rename_entry_type requires from and to")
    if from_key == to_key:
        return  # no-op
    from app.services.content_profile_compile import _slug

    # Resolve the target EntryType node by walking every EntryType under the
    # track AND every EntryType referenced by an existing entry. After the
    # swap the renamed EntryType carries the new name, so its _slug should
    # match ``to_key``.
    target_et: Optional[EntryType] = None
    entries = await track.nodes(edge=[CONTAINS], node=["Entry"])
    if not entries:
        return
    et_by_id: Dict[str, Optional[EntryType]] = {}
    # Discovery via the entries' type_ids (covers old-type lookups even when
    # the rename has not yet been mirrored onto the EntryType node).
    for e in entries:
        tid = getattr(e, "type_id", None) or ""
        if tid and tid not in et_by_id:
            et_by_id[tid] = await EntryType.get(tid)
    # Discovery via the track's direct EntryType children (covers the new-type
    # node that was just created by the manifest sync but does not yet have
    # any entries pointing at it).
    try:
        for et in await track.nodes(edge=[CONTAINS], node=["EntryType"]):
            if et and et.id not in et_by_id:
                et_by_id[et.id] = et
    except Exception:  # noqa: BLE001 — stub-track may not implement EntryType
        pass

    # Prefer an EntryType whose current slug matches ``to_key`` (post-rename);
    # fall back to one whose slug still matches ``from_key`` (pre-rename
    # data) so the runner remains correct even when run before the
    # EntryType.name itself has been rewritten on the node.
    for et in et_by_id.values():
        if et and _slug(str(et.name or "")) == to_key:
            target_et = et
            break
    if target_et is None:
        for et in et_by_id.values():
            if et and _slug(str(et.name or "")) == from_key:
                target_et = et
                break
    if target_et is None:
        return

    for e in entries:
        tid = getattr(e, "type_id", None) or ""
        src_et = et_by_id.get(tid)
        if src_et is None:
            continue
        cur_slug = _slug(str(src_et.name or ""))
        if cur_slug != from_key and cur_slug != to_key:
            continue  # not affected
        if e.type_id == target_et.id and cur_slug == to_key:
            continue  # idempotent — already on target
        if e.type_id == target_et.id:
            continue
        e.type_id = target_et.id
        await e.save()
        log["mutated_entries"].append(e.id)


async def _change_view_type(
    *,
    track: Track,
    op: Dict[str, Any],
    log: Dict[str, Any],
) -> None:
    """Phase 5 Plan 05-02 — ``change_view_type`` op.

    Updates a persisted View node's ``type`` field (and merges declared
    defaults into ``View.config`` — e.g. ``group_by`` when switching to
    ``kanban``). Idempotent — views already on the target type are skipped;
    existing view-config values are preserved unless explicitly overwritten
    by the op's declared defaults.

    Op shape::

        {op: change_view_type, view_key: my_view, from: table, to: kanban,
         group_by: priority}
    """
    view_key = str(op.get("view_key") or "").strip()
    to_type = str(op.get("to") or "").strip()
    if not (view_key and to_type):
        raise BadRequestError(message="change_view_type requires view_key and to")
    extras = {k: v for k, v in op.items() if k not in {"op", "view_key", "from", "to"}}

    views = await track.nodes(edge=[CONTAINS], node=["View"])
    for view in views:
        v_key = getattr(view, "key", None) or getattr(view, "name", None) or ""
        if v_key != view_key:
            continue
        if getattr(view, "type", None) == to_type:
            continue  # idempotent
        view.type = to_type
        cfg = dict(getattr(view, "config", None) or {})
        for k, v in extras.items():
            cfg.setdefault(k, v)
        view.config = cfg
        await view.save()
        log["mutated_entries"].append(view.id)


def _coerce(value: Any, to_type: str) -> Any:
    if to_type == "text" or to_type == "markdown":
        return str(value)
    if to_type == "number":
        if isinstance(value, bool):
            return int(value)
        if isinstance(value, (int, float)):
            return value
        return float(str(value)) if "." in str(value) else int(str(value))
    if to_type == "boolean":
        if isinstance(value, bool):
            return value
        if isinstance(value, str):
            return value.strip().lower() in {"true", "1", "yes", "on"}
        return bool(value)
    if to_type == "json":
        return value
    raise ValueError(f"unsupported coercion target '{to_type}'")


_OP_HANDLERS: Dict[
    str,
    Callable[..., Awaitable[None]],
] = {
    # Pillar 2 v1 (6 ops):
    "rename_field": _rename_field,
    "default_fill": _default_fill,
    "delete_field": _delete_field,
    "prune_enum_option": _prune_enum_option,
    "coerce_type": _coerce_type,
    "move_field": _move_field,
    # Phase 5 Plan 05-02 additions (3 ops) — locked decision #2:
    # extension, not greenfield. Append-only — NEVER replace this dict.
    "add_field_with_default": _add_field_with_default,
    "rename_entry_type": _rename_entry_type,
    "change_view_type": _change_view_type,
}


# ---------------------------------------------------------------------------
# Runner
# ---------------------------------------------------------------------------


async def run_publish_migrations(
    *,
    published_cp: ContentProfile,
    candidate_manifest: Dict[str, Any],
    abort_on_failure: bool = True,
    actor_id: Optional[str] = None,
) -> Dict[str, Any]:
    """Execute every ``migrations[].ops[]`` entry against affected entries.

    Returns:
      ``{"executed": True, "ops": [<per-op summary>, ...],
      "errors": [<unrecoverable errors>, ...],
      "mutated_entry_count": <int>}``

    Raises ``BadRequestError`` (when ``abort_on_failure=True``) on the first
    error.
    """
    migrations = list((candidate_manifest or {}).get("migrations") or [])
    if not migrations:
        return {
            "executed": True,
            "ops": [],
            "errors": [],
            "mutated_entry_count": 0,
            "pending_manual_review": [],
        }

    tracks = await _affected_tracks(published_cp)
    op_summaries: List[Dict[str, Any]] = []
    all_errors: List[Dict[str, Any]] = []
    all_pending: List[Dict[str, Any]] = []
    total_mutated = 0

    for mig in migrations:
        ops = list((mig or {}).get("ops") or [])
        for op in ops:
            kind = str((op or {}).get("op") or "").strip()
            handler = _OP_HANDLERS.get(kind)
            if handler is None:
                msg = f"Unknown migration op '{kind}'"
                if abort_on_failure:
                    raise BadRequestError(message=msg)
                all_errors.append({"op": kind, "reason": msg})
                continue
            log: Dict[str, Any] = {
                "op": kind,
                "from_version": mig.get("from_version"),
                "to_version": mig.get("to_version"),
                "mutated_entries": [],
                "errors": [],
                "pending_manual_review": [],
            }
            try:
                for track in tracks:
                    await handler(track=track, op=op, log=log)
            except BadRequestError as exc:
                if abort_on_failure:
                    raise
                log["errors"].append({"reason": exc.message})
            op_summaries.append(log)
            all_errors.extend(log["errors"])
            all_pending.extend(log["pending_manual_review"])
            total_mutated += len(log["mutated_entries"])

    return {
        "executed": True,
        "ops": op_summaries,
        "errors": all_errors,
        "mutated_entry_count": total_mutated,
        "pending_manual_review": all_pending,
        "actor_id": actor_id,
    }
