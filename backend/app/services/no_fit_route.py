"""W2.3: when ranking has no winner, name the smallest structure to add.

Does not invent a destination. Empty workspace → new App. Tracks exist but
none share schema labels → new Track on an existing App. A track resembles
the note but cannot hold it → new EntryType on that track. The note is
returned as ``preserve`` so it can be filed once after that structure lands.
When a conversation session is present, ``preserve`` is also stored as a
session artifact and filed exactly once after the matching structure is
approved.
"""

from __future__ import annotations

from typing import Any, Dict, Optional

PRESERVE_KEY = "no_fit.preserve"
PRESERVE_KIND = "no_fit_preserve"

# Consumed staging kinds that complete each route. ``create_app`` alone is
# not enough for ``new_app`` — there is still no track to file into.
_VIA_KINDS = {
    "scaffold": frozenset({"create_track", "commit_batch"}),
    "create_app_track": frozenset({"create_track"}),
    "entry_type": frozenset(
        {"modify_operational_model", "modify_operational_model.add_entry_type"}
    ),
}


def _label(text: str) -> str:
    words = [part for part in (text or "").split() if part][:4]
    return " ".join(words) or "Note"


def _structure_stage_hint(route: Dict[str, Any]) -> Dict[str, Any]:
    text = str((route.get("preserve") or {}).get("text") or "")
    label = _label(text)
    kind = route.get("kind")
    if kind == "new_app":
        return {"tool": "integral-scaffold", "via": "scaffold"}
    if kind == "new_track":
        return {
            "tool": "integral_create_app_track",
            "via": "create_app_track",
            "args": {"app_id": route.get("app_id"), "title": label},
        }
    return {
        "tool": "integral_modify_model",
        "via": "entry_type",
        "args": {
            "action": "add_entry_type",
            "track_id": route.get("track_id"),
            "name": label,
        },
    }


def classify_no_fit(
    facet: Dict[str, Any],
    *,
    open_track_count: int,
) -> Optional[Dict[str, Any]]:
    """Return a route when this facet has no winner. None when it can file."""
    if facet.get("winner"):
        return None
    preserved = {
        "text": facet.get("text") or "",
        "fields": dict(facet.get("fields") or {}),
    }
    if open_track_count <= 0:
        route = {
            "kind": "new_app",
            "via": "scaffold",
            "why": "there is no open track to file into",
            "preserve": preserved,
        }
        route["stage"] = _structure_stage_hint(route)
        return route
    candidates = [
        row for row in (facet.get("candidates") or []) if isinstance(row, dict)
    ]
    close = [row for row in candidates if (row.get("schema_fit") or 0) > 0]
    if close:
        best = close[0]
        route = {
            "kind": "new_entry_type",
            "via": "entry_type",
            "app_id": best.get("app_id"),
            "track_id": best.get("track_id"),
            "track_title": best.get("track_title"),
            "why": (
                f"{best.get('track_title') or 'this track'} resembles the note "
                "but has no entry type that can hold it"
            ),
            "preserve": preserved,
        }
        route["stage"] = _structure_stage_hint(route)
        return route
    host = candidates[0] if candidates else None
    route = {
        "kind": "new_track",
        "via": "create_app_track",
        "app_id": (host or {}).get("app_id"),
        "app_name": (host or {}).get("app_name"),
        "why": "open tracks exist but none share schema labels with this note",
        "preserve": preserved,
    }
    route["stage"] = _structure_stage_hint(route)
    return route


def attach_no_fit_routes(
    ranked: Dict[str, Any],
    *,
    open_track_count: int,
) -> Dict[str, Any]:
    """Add ``route`` to each no-fit facet. Leaves winners untouched."""
    if ranked.get("error"):
        return ranked
    facets = []
    for facet in ranked.get("facets") or []:
        if not isinstance(facet, dict):
            continue
        row = dict(facet)
        route = classify_no_fit(row, open_track_count=open_track_count)
        if route is not None:
            row["route"] = route
        facets.append(row)
    ranked = dict(ranked)
    ranked["facets"] = facets
    return ranked


def _pending_route(ranked: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    for facet in ranked.get("facets") or []:
        if not isinstance(facet, dict):
            continue
        route = facet.get("route")
        if isinstance(route, dict) and not facet.get("winner"):
            return route
    return None


async def persist_no_fit_preserve(
    *,
    user_id: str,
    session_id: Optional[str],
    ranked: Dict[str, Any],
) -> Dict[str, Any]:
    """Store the first no-fit preserve on the session. No-op without a session."""
    if ranked.get("error") or not session_id:
        return ranked
    route = _pending_route(ranked)
    if route is None:
        return ranked
    preserve = route.get("preserve") or {}
    from app.agentive.artifacts import upsert_artifact

    meta = {k: v for k, v in route.items() if k != "preserve"}
    saved = await upsert_artifact(
        user_id=user_id,
        session_id=session_id,
        key=PRESERVE_KEY,
        kind=PRESERVE_KIND,
        title=_label(str(preserve.get("text") or "")),
        body=str(preserve.get("text") or ""),
        metadata={
            "status": "pending",
            "route": meta,
            "fields": dict(preserve.get("fields") or {}),
            "filed_entry_id": None,
        },
    )
    if saved.get("ok"):
        for facet in ranked.get("facets") or []:
            if isinstance(facet, dict) and isinstance(facet.get("route"), dict):
                facet["route"]["artifact"] = {
                    "key": PRESERVE_KEY,
                    "version": saved.get("version"),
                }
    return ranked


async def load_no_fit_preserve(
    *,
    user_id: str,
    session_id: Optional[str],
) -> Optional[Dict[str, Any]]:
    """Return the session's pending or filed no-fit artifact, or None."""
    from app.agentive.artifacts import get_artifact

    got = await get_artifact(user_id=user_id, session_id=session_id, key=PRESERVE_KEY)
    if not got.get("ok"):
        return None
    return got


def _track_id_from_structure(
    *,
    kind: str,
    payload: Dict[str, Any],
    result: Dict[str, Any],
    route: Dict[str, Any],
) -> Optional[str]:
    track = result.get("track")
    if isinstance(track, dict) and track.get("id"):
        return str(track["id"])
    for key in ("track_id", "id"):
        value = result.get(key)
        if value and (key == "track_id" or kind.startswith("create_track")):
            return str(value)
    if payload.get("track_id"):
        return str(payload["track_id"])
    if route.get("track_id"):
        return str(route["track_id"])
    return None


def _entry_type_from_structure(
    *,
    payload: Dict[str, Any],
    result: Dict[str, Any],
    route: Dict[str, Any],
) -> str:
    types = payload.get("entry_types")
    if isinstance(types, list) and types and isinstance(types[0], dict):
        name = types[0].get("name") or types[0].get("key")
        if name:
            return str(name)
    if payload.get("name") and (
        payload.get("action") == "add_entry_type" or route.get("via") == "entry_type"
    ):
        return str(payload["name"])
    applied = result.get("entry_types_applied")
    if isinstance(applied, dict):
        names = applied.get("names") or applied.get("entry_types")
        if isinstance(names, list) and names:
            first = names[0]
            if isinstance(first, dict):
                return str(first.get("name") or first.get("key") or "Note")
            return str(first)
    return _label(str((route.get("preserve") or {}).get("text") or "")) or "Note"


def _kind_matches(kind: str, via: str) -> bool:
    allowed = _VIA_KINDS.get(via) or frozenset()
    if kind in allowed:
        return True
    return any(kind.startswith(f"{prefix}.") for prefix in allowed)


async def file_preserve_once(
    *,
    user_id: str,
    session_id: Optional[str],
    track_id: str,
    entry_type: Optional[str] = None,
) -> Dict[str, Any]:
    """File the session preserve onto ``track_id`` exactly once."""
    got = await load_no_fit_preserve(user_id=user_id, session_id=session_id)
    if got is None:
        return {"error": "not_found", "detail": "no preserved note on this session"}
    meta = got.get("metadata") if isinstance(got.get("metadata"), dict) else {}
    if meta.get("status") == "filed" and meta.get("filed_entry_id"):
        return {
            "filed": True,
            "already": True,
            "entry_id": meta.get("filed_entry_id"),
        }
    if not track_id:
        return {"error": "track_required", "detail": "no track to file the note into"}
    route = meta.get("route") if isinstance(meta.get("route"), dict) else {}
    text = str(got.get("body") or "")
    fields = dict(meta.get("fields") or {})
    type_name = entry_type or _entry_type_from_structure(
        payload={}, result={}, route={**route, "preserve": {"text": text}}
    )
    from app.agentive.staging_executors import _x_file_content
    from app.services.agent_scope import current_scope_workspace_id
    from app.services.chat_threads import get_thread_by_session

    thread = await get_thread_by_session(session_id or "")
    workspace_id = getattr(thread, "workspace_id", None) if thread else None
    token = current_scope_workspace_id.set(workspace_id) if workspace_id else None
    try:
        created = await _x_file_content(
            user_id,
            {
                "track_id": track_id,
                "title": _label(text),
                "body": text,
                "entry_type": type_name,
                "fields": fields or None,
            },
        )
    finally:
        if token is not None:
            current_scope_workspace_id.reset(token)
    if created.get("error") or created.get("filed") is False:
        return created
    entry = created.get("entry") if isinstance(created.get("entry"), dict) else {}
    entry_id = entry.get("id") or created.get("id")
    from app.agentive.artifacts import upsert_artifact

    await upsert_artifact(
        user_id=user_id,
        session_id=session_id,
        key=PRESERVE_KEY,
        kind=PRESERVE_KIND,
        title=str(got.get("title") or _label(text)),
        body=text,
        metadata={
            **meta,
            "status": "filed",
            "filed_entry_id": entry_id,
        },
    )
    return {"filed": True, "already": False, "entry": created, "entry_id": entry_id}


async def maybe_file_preserve_after_structure(
    *,
    user_id: str,
    session_id: Optional[str],
    kind: str,
    payload: Optional[Dict[str, Any]] = None,
    result: Optional[Dict[str, Any]] = None,
) -> Optional[Dict[str, Any]]:
    """File the preserved note once the matching structure lands. None if n/a."""
    if not session_id or (isinstance(result, dict) and result.get("error")):
        return None
    got = await load_no_fit_preserve(user_id=user_id, session_id=session_id)
    if got is None:
        return None
    meta = got.get("metadata") if isinstance(got.get("metadata"), dict) else {}
    if meta.get("status") == "filed":
        return {
            "filed": True,
            "already": True,
            "entry_id": meta.get("filed_entry_id"),
        }
    route = meta.get("route") if isinstance(meta.get("route"), dict) else {}
    if not _kind_matches(kind, str(route.get("via") or "")):
        return None
    payload = payload if isinstance(payload, dict) else {}
    result = result if isinstance(result, dict) else {}
    if route.get("via") == "create_app_track" and route.get("app_id"):
        created_app = None
        track = result.get("track")
        if isinstance(track, dict):
            created_app = track.get("app_id") or track.get("app")
        created_app = created_app or payload.get("app_id")
        if created_app and str(created_app) != str(route["app_id"]):
            return None
    if route.get("via") == "entry_type" and route.get("track_id"):
        target = payload.get("track_id") or result.get("track_id")
        if target and str(target) != str(route["track_id"]):
            return None
    track_id = _track_id_from_structure(
        kind=kind, payload=payload, result=result, route=route
    )
    if not track_id:
        return {
            "error": "track_required",
            "detail": "structure landed but no track id was returned",
        }
    return await file_preserve_once(
        user_id=user_id,
        session_id=session_id,
        track_id=track_id,
        entry_type=_entry_type_from_structure(
            payload=payload, result=result, route=route
        ),
    )
