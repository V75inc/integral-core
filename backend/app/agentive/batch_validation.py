"""Read-only scaffold checks shared by commit and continuation hints.

No domain identities live here. A valid build has shaped, visible tracks and
backward-only references; execution still checks policy for every operation.
"""

from typing import Any, Dict, List


def scaffold_missing(
    ops: List[Dict[str, Any]], *, allow_empty: bool = False
) -> List[str]:
    """Return actionable omissions per track, not misleading global counts."""
    from app.agentive.staging_executors import _capture_batch_refs, _resolve_batch_refs

    if not any(op.get("kind") == "create_app" for op in ops):
        return []
    refs: Dict[str, str] = {}
    tracks: Dict[str, Dict[str, Any]] = {}
    apps: Dict[str, str] = {}
    for idx, op in enumerate(ops):
        kind = op.get("kind")
        payload = _resolve_batch_refs(op.get("payload") or {}, refs)
        identity = f"step:{idx}"
        name = payload.get("title") or payload.get("name") or identity
        if kind == "create_app":
            apps[identity] = name
            _capture_batch_refs(refs, idx, {"app": {"id": identity, "name": name}})
        elif kind in {"create_track", "create_app_track"}:
            tracks[identity] = {
                "name": name,
                "app_id": payload.get("app_id"),
                "shaped": bool(payload.get("entry_types")),
                "view": False,
                "seed": False,
            }
            _capture_batch_refs(refs, idx, {"track": {"id": identity, "title": name}})
        elif payload.get("track_id") in tracks:
            target = tracks[payload["track_id"]]
            if kind == "apply_profile_to_track":
                target["shaped"] = True
            elif kind == "save_view":
                target["view"] = True
            elif kind == "create_entry":
                target["seed"] = True
    missing = []
    for app_id, name in apps.items():
        if not any(t["app_id"] == app_id for t in tracks.values()):
            missing.append(f"integral_create_app_track for app {name!r}")
    for track in tracks.values():
        name = track["name"]
        if not track["app_id"]:
            missing.append(f"Attach track {name!r} to its app with app_id")
        if not track["shaped"]:
            missing.append(
                f"integral_apply_profile_to_track for {name!r} (or inline entry_types)"
            )
        if not track["view"]:
            missing.append(f"integral_save_view for {name!r}")
        if not allow_empty and not track["seed"]:
            missing.append(
                f"integral_create_entry demo for {name!r}; allow_empty only if requested"
            )
    return missing


def validate_batch_references(ops: List[Dict[str, Any]]) -> None:
    """Reject dangling/forward or ambiguous named references before any writes."""
    from app.agentive.staging import StagingError
    from app.agentive.staging_executors import (
        _BATCH_REF_RE,
        _capture_batch_refs,
        _resolve_batch_refs,
    )

    def strings(value):
        if isinstance(value, str):
            yield value
        elif isinstance(value, dict):
            for child in value.values():
                yield from strings(child)
        elif isinstance(value, list):
            for child in value:
                yield from strings(child)

    creators = {
        "create_app": "app",
        "create_track": "track",
        "create_app_track": "track",
        "create_entry": "entry",
        "create_tag": "tag",
        "save_view": "view",
        "create_comment": "comment",
    }
    refs: Dict[str, str] = {}
    names = set()
    for idx, op in enumerate(ops):
        payload = op.get("payload") or {}
        resolved = _resolve_batch_refs(payload, refs)
        unresolved = sorted(
            {m.group(0) for s in strings(resolved) for m in _BATCH_REF_RE.finditer(s)}
        )
        if unresolved:
            raise StagingError(
                "invalid_batch_reference",
                f"Step {idx + 1} has unresolved references: {', '.join(unresolved)}. "
                "Create the target earlier in this batch and use its exact name. "
                "Cancel and rebuild the unapproved batch to correct existing operations.",
            )
        entity = creators.get(op.get("kind"))
        if entity:
            name = payload.get("title") or payload.get("name")
            if (
                name
                and (entity, name) in names
                and any(
                    f"{entity}.id:{name}" in s or f"{entity}_id:{name}" in s
                    for later in ops
                    for s in strings(later.get("payload") or {})
                )
            ):
                raise StagingError(
                    "invalid_batch_reference",
                    f"Duplicate {entity} name {name!r} makes named references ambiguous.",
                )
            names.add((entity, name))
            _capture_batch_refs(
                refs, idx, {entity: {"id": f"step:{idx}", "name": name}}
            )
