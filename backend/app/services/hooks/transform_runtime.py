"""Entry.transform execution + post-update auto-dispatch.

Used by ``POST /api/entries/{id}/transform`` and by ``update_entry`` when a
declarative transform gate (e.g. opportunity ``stage=won``) becomes satisfied.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional, Tuple

from app.models.edges import REFERENCES
from app.models.nodes import Entry, EntryType, Track
from app.schemas.policy import Resource, Subject
from app.services.entry_create import create_entry_in_track
from app.services.hooks.declarative import (
    OverrideRequiredError,
    TransformGateDeniedError,
    apply_transform,
    gate_satisfied,
)
from app.services.hooks.registry import get_workspace_hooks
from app.services.hooks.resolver import find_matching_bindings
from app.services.policy_engine import evaluate as policy_evaluate

logger = logging.getLogger(__name__)


def _slug(name: str) -> str:
    return (name or "").strip().lower().replace(" ", "_").replace("-", "_")


async def entry_type_key(entry: Entry) -> str:
    """Return the slug of the entry's EntryType name, or empty string if unset."""
    if not entry.type_id:
        return ""
    et = await EntryType.get(entry.type_id)
    if et is None:
        return ""
    return _slug(et.name)


async def track_type_key(track: Optional[Track]) -> str:
    """Return the slug of the track title, or empty string if track is None."""
    if track is None:
        return ""
    return _slug(track.title)


async def augment_payload_with_target_entry_type(
    payload: Dict[str, Any],
    workspace_id: str,
    tgt_track: Track,
) -> Dict[str, Any]:
    """Add ``target_entry_type`` when a transform binding's declared ET exists on tgt."""
    candidates = get_workspace_hooks(workspace_id, "entry.transform")
    declared = {(b.get("match") or {}).get("target_entry_type") for b in candidates}
    declared = {s for s in declared if s}
    if not declared:
        return payload
    ets_on_tgt = await EntryType.find({"context.track_id": tgt_track.id})
    available_slugs = {_slug(et.name) for et in ets_on_tgt}
    overlap = declared & available_slugs
    if len(overlap) >= 1:
        payload = dict(payload)
        payload["target_entry_type"] = sorted(overlap)[0]
    return payload


# Title / template_id aliases come from app.track_aliases (F0) — not hardcoded.


def _track_type_want_set(track_type: str, workspace_id: str = "") -> set:
    from app.services.hooks.track_aliases import track_type_want_set

    return track_type_want_set(track_type, workspace_id)


async def find_workspace_track_by_type(
    workspace_id: str, track_type: str
) -> Optional[Track]:
    """Resolve a workspace Track by ``template_id`` or title slug.

    Alias groups declared in installed App manifests (``app.track_aliases``)
    let hooks that name either form hit the same board. When several
    tracks share a template_id, prefer an exact title-slug hit in the
    want-set, then earliest ``created_at``.
    """
    want = _track_type_want_set(track_type, workspace_id)
    if not want or not workspace_id:
        return None
    matches: List[Track] = []
    for track in await Track.find({"context.workspace_id": workspace_id}):
        title_slug = _slug(getattr(track, "title", "") or "")
        template_slug = _slug(getattr(track, "template_id", "") or "")
        if title_slug in want or template_slug in want:
            matches.append(track)
    if not matches:
        return None

    def _rank(t: Track) -> tuple:
        title_slug = _slug(getattr(t, "title", "") or "")
        template_slug = _slug(getattr(t, "template_id", "") or "")
        return (
            0 if template_slug in want else 1,
            0 if title_slug in want else 1,
            str(getattr(t, "created_at", "") or ""),
            str(t.id),
        )

    matches.sort(key=_rank)
    return matches[0]


async def already_transformed(source: Entry) -> bool:
    """True when a provenance REFERENCES(transform_source) already points at source."""
    try:
        inbound = await source.nodes(edge=[REFERENCES], direction="in", node=["Entry"])
    except Exception:  # noqa: BLE001
        return False
    ctx = await source.get_context()
    for other in inbound or []:
        try:
            edges = await ctx.find_edges_between(
                other.id, source.id, edge_class=REFERENCES
            )
        except Exception:  # noqa: BLE001
            continue
        for edge in edges or []:
            if str(getattr(edge, "field_key", "") or "") == "transform_source":
                return True
    return False


async def execute_transform(
    *,
    src: Entry,
    tgt_track: Track,
    workspace_id: str,
    user_id: str,
    hook_key: Optional[str] = None,
    override: bool = False,
    enforce_gate: bool = True,
) -> Dict[str, Any]:
    """Materialize a new entry on ``tgt_track`` from ``src`` via a matching hook.

    Returns ``{"new_entry_id": ..., "hook_key": ...}``.
    Raises ``OverrideRequiredError``, ``TransformGateDeniedError``, or
    returns via caller-raised hook errors when no / ambiguous bindings.
    """
    from app.services.hooks.errors import AmbiguousHookError, HookNotConfiguredError

    source_entry_type = await entry_type_key(src)
    src_track = await Track.get(src.track_id) if src.track_id else None
    payload: Dict[str, Any] = {
        "source_entry_type": source_entry_type,
        "source_track_type": await track_type_key(src_track),
        "target_track_type": await track_type_key(tgt_track),
    }
    payload = await augment_payload_with_target_entry_type(
        payload, workspace_id, tgt_track
    )

    bindings = find_matching_bindings(
        workspace_id, "entry.transform", payload, explicit_hook_key=hook_key
    )
    if not bindings:
        raise HookNotConfiguredError(
            message="no entry.transform hook matched",
            details={"payload": payload, "available": []},
        )
    if len(bindings) > 1 and not hook_key:
        raise AmbiguousHookError(
            message="multiple bindings matched; supply ?hook=<key>",
            details={"candidates": [b["key"] for b in bindings]},
        )
    binding = bindings[0]
    declarative = binding.get("declarative") or {}

    src_track = await Track.get(src.track_id) if src.track_id else None
    src_ws = str(getattr(src_track, "workspace_id", "") or "") if src_track else ""
    tgt_ws = str(getattr(tgt_track, "workspace_id", "") or "")
    if src_ws and tgt_ws and src_ws != tgt_ws:
        raise TransformGateDeniedError(
            "transform target track is in a different workspace"
        )

    create_decision = await policy_evaluate(
        subject=Subject(kind="human", id=user_id),
        action="entry.create",
        resource=Resource(
            kind="entry",
            id="",
            scope=f"track:{tgt_track.id}",
        ),
    )
    if not create_decision.allowed:
        raise TransformGateDeniedError("entry.create denied on transform target track")

    src_payload: Dict[str, Any] = {
        "title": src.title or "",
        "body": src.body or "",
        "custom_fields": dict(src.custom_fields or {}),
    }
    projected = apply_transform(
        src_payload,
        declarative,
        override=override,
        enforce_gate=enforce_gate,
    )

    target_entry_type_key = (binding.get("match") or {}).get("target_entry_type") or ""
    target_et = None
    if target_entry_type_key:
        for et in await EntryType.find({"context.track_id": tgt_track.id}):
            if _slug(et.name) == target_entry_type_key:
                target_et = et
                break

    new_entry = await create_entry_in_track(
        track=tgt_track,
        user_id=user_id,
        title=str(projected.get("title") or ""),
        body=str(projected.get("body") or ""),
        custom_fields=dict(projected.get("custom_fields") or {}),
        entry_type=target_et,
        workspace_id=workspace_id or tgt_ws or src_ws,
        actor_kind="human",
    )
    prov = declarative.get("provenance_edge")
    if isinstance(prov, dict) and prov.get("edge") == "REFERENCES":
        await new_entry.connect(
            src, edge=REFERENCES, field_key="transform_source", cross_track=True
        )

    return {"new_entry_id": new_entry.id, "hook_key": binding["key"]}


def _source_type_matches(binding: Dict[str, Any], source_entry_type: str) -> bool:
    match = binding.get("match") or {}
    expected = match.get("source_entry_type")
    if expected is None:
        return True
    return _slug(str(expected)) == _slug(source_entry_type)


async def maybe_auto_transform_after_update(
    *,
    entry: Entry,
    workspace_id: str,
    user_id: str,
) -> Optional[Dict[str, Any]]:
    """If a gated entry.transform binding is now satisfied, spawn the target entry.

    Best-effort: missing peer app / already transformed / gate not met → no-op.
    Failures are logged and swallowed so they never roll back the parent update.
    """
    if not workspace_id:
        return None
    try:
        if await already_transformed(entry):
            return None
        source_entry_type = await entry_type_key(entry)
        if not source_entry_type:
            return None
        candidates = get_workspace_hooks(workspace_id, "entry.transform")
        src_payload = {
            "title": entry.title or "",
            "body": entry.body or "",
            "custom_fields": dict(entry.custom_fields or {}),
        }
        for binding in candidates:
            if not _source_type_matches(binding, source_entry_type):
                continue
            declarative = binding.get("declarative") or {}
            # Auto-fire only for gated transforms (won / accepted / …).
            if not declarative.get("gate_field"):
                continue
            if not gate_satisfied(src_payload, declarative):
                continue
            target_track_type = (binding.get("match") or {}).get("target_track_type")
            if not target_track_type:
                continue
            tgt_track = await find_workspace_track_by_type(
                workspace_id, str(target_track_type)
            )
            if tgt_track is None:
                logger.info(
                    "auto_transform: peer track type %r not in workspace %s "
                    "(hook=%s) — skipping",
                    target_track_type,
                    workspace_id,
                    binding.get("key"),
                )
                continue
            result = await execute_transform(
                src=entry,
                tgt_track=tgt_track,
                workspace_id=workspace_id,
                user_id=user_id,
                hook_key=str(binding.get("key") or "") or None,
                override=False,
                enforce_gate=True,
            )
            logger.info(
                "auto_transform: spawned %s via hook %s from entry %s",
                result.get("new_entry_id"),
                result.get("hook_key"),
                entry.id,
            )
            return result
    except (OverrideRequiredError, TransformGateDeniedError) as exc:
        logger.info("auto_transform skipped for entry %s: %s", entry.id, exc)
    except Exception:  # noqa: BLE001
        logger.exception("auto_transform failed for entry %s", entry.id)
    return None


async def resolve_transform_target_track(
    *,
    workspace_id: str,
    source_entry_type: str,
    to_track_id: Optional[str] = None,
    hook_key: Optional[str] = None,
) -> Tuple[Optional[Track], Optional[str]]:
    """Resolve target track for an explicit transform call.

    Returns ``(track, error_message)``. When ``to_track_id`` is set, loads it.
    Otherwise picks from binding ``target_track_type`` when unambiguous.
    """
    if to_track_id:
        track = await Track.get(to_track_id)
        if track is None:
            return None, "to_track required (and must resolve)"
        return track, None

    candidates = get_workspace_hooks(workspace_id, "entry.transform")
    if hook_key:
        candidates = [b for b in candidates if str(b.get("key")) == hook_key]
    candidates = [b for b in candidates if _source_type_matches(b, source_entry_type)]
    if not candidates:
        return None, "no entry.transform hook matched; supply to_track"
    types = {
        str((b.get("match") or {}).get("target_track_type") or "") for b in candidates
    }
    types.discard("")
    if len(types) != 1:
        return None, "to_track required when target track type is ambiguous"
    track = await find_workspace_track_by_type(workspace_id, next(iter(types)))
    if track is None:
        return None, (
            f"target track type {next(iter(types))!r} not installed in workspace"
        )
    return track, None
