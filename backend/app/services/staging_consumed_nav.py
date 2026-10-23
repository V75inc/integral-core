"""Extract navigable resource ids from a staged-change execute result.

Mirrors ``frontend/src/features/ai-chat/staging/consumedSummary.tsx`` so
persisted ``consumed_nav`` on the staged-change envelope survives chat reload.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional


def _as_record(value: Any) -> Optional[Dict[str, Any]]:
    if isinstance(value, dict):
        return value
    return None


def _staged_op(staged: Dict[str, Any]) -> str:
    diff = _as_record(staged.get("diff_machine"))
    if diff and diff.get("op"):
        return str(diff["op"])
    return str(staged.get("kind") or "")


def _unwrap_api_resource(result: Dict[str, Any], key: str) -> Optional[Dict[str, Any]]:
    node = _as_record(result.get(key))
    if not node or node.get("error"):
        return None
    inner = _as_record(node.get(key))
    if inner and inner.get("id"):
        return inner
    if node.get("id"):
        return node
    return None


def _created_ref_from_result(
    result: Optional[Dict[str, Any]],
    op: str,
    diff: Optional[Dict[str, Any]],
) -> Optional[Dict[str, Any]]:
    if not result or result.get("error") or result.get("filed") is False:
        return None

    entry = _unwrap_api_resource(result, "entry")
    if entry:
        return {
            "kind": "entry",
            "id": str(entry.get("id") or ""),
            "title": entry.get("title"),
            "trackId": entry.get("track_id") or (diff or {}).get("track_id"),
        }

    track = _unwrap_api_resource(result, "track")
    if track:
        return {
            "kind": "track",
            "id": str(track.get("id") or ""),
            "title": track.get("title"),
        }

    app = _unwrap_api_resource(result, "app")
    if app:
        return {
            "kind": "app",
            "id": str(app.get("id") or ""),
            "title": app.get("name") or app.get("title"),
        }

    dashboard = _unwrap_api_resource(result, "dashboard")
    if dashboard:
        return {
            "kind": "dashboard",
            "id": str(dashboard.get("id") or ""),
            "title": dashboard.get("name"),
            "appId": dashboard.get("app_id"),
        }
    if (
        result.get("id")
        and result.get("app_id")
        and op
        in (
            "create_dashboard",
            "update_dashboard",
        )
    ):
        return {
            "kind": "dashboard",
            "id": str(result["id"]),
            "title": result.get("name"),
            "appId": result.get("app_id"),
        }

    if result.get("id"):
        track_id = result.get("track_id")
        type_id = result.get("type_id")
        is_entry = op in ("create_entry", "file_content", "update_entry") or bool(
            track_id or type_id
        )
        if is_entry:
            return {
                "kind": "entry",
                "id": str(result["id"]),
                "title": result.get("title"),
                "trackId": track_id or (diff or {}).get("track_id"),
            }
        if op in ("create_track", "update_track"):
            return {
                "kind": "track",
                "id": str(result["id"]),
                "title": result.get("title"),
            }
        if op == "create_app":
            return {
                "kind": "app",
                "id": str(result["id"]),
                "title": result.get("name") or result.get("title"),
            }
    return None


def _extract_consumed_nav(
    execute_result: Any,
    staged: Dict[str, Any],
) -> Dict[str, Any]:
    """Return a ``ConsumedNav``-shaped dict for transcript persistence."""
    diff = _as_record(staged.get("diff_machine"))
    op = _staged_op(staged)
    result = _as_record(execute_result)

    # Typed App commands return governed object references rather than CRUD
    # envelopes. Preserve all of them so a batch receipt can link each record.
    output = _as_record((result or {}).get("output")) or {}
    object_refs = (result or {}).get("object_refs") or output.get("object_refs")
    if isinstance(object_refs, list) and object_refs:
        refs = [
            {
                "kind": ref["kind"],
                "id": ref["id"],
                "title": ref.get("title"),
                "trackId": ref.get("track_id"),
            }
            for ref in object_refs
            if isinstance(ref, dict)
            and ref.get("kind") in ("entry", "track", "app")
            and isinstance(ref.get("id"), str)
        ]
        if refs:
            return {"created": refs, "appId": (result or {}).get("app_id")}

    sub_results = (
        result.get("results")
        if result and isinstance(result.get("results"), list)
        else None
    )
    if sub_results:
        created: List[Dict[str, Any]] = []
        for raw in sub_results:
            sub = _as_record(raw)
            if not sub:
                continue
            sub_kind = str(sub.get("kind") or "")
            ref = _created_ref_from_result(
                _as_record(sub.get("result")), sub_kind, diff
            )
            if ref and ref.get("id"):
                created.append(ref)
        first_app = next((r for r in created if r.get("kind") == "app"), None)
        first_track = next((r for r in created if r.get("kind") == "track"), None)
        first_entry = next((r for r in created if r.get("kind") == "entry"), None)
        return {
            "created": created,
            "appId": first_app.get("id") if first_app else None,
            "trackId": (
                (first_track or {}).get("trackId")
                or (first_entry or {}).get("trackId")
                or (first_track or {}).get("id")
            ),
            "entryId": first_entry.get("id") if first_entry else None,
            "title": (
                (first_app or {}).get("title")
                or (first_track or {}).get("title")
                or (first_entry or {}).get("title")
            ),
        }

    ref = _created_ref_from_result(result, op, diff)
    if ref:
        return {
            "title": ref.get("title"),
            "created": [ref] if ref.get("id") else None,
            "appId": ref["id"] if ref.get("kind") == "app" else None,
            "trackId": (
                ref["id"]
                if ref.get("kind") == "track"
                else ref.get("trackId") if ref.get("kind") == "entry" else None
            ),
            "entryId": ref["id"] if ref.get("kind") == "entry" else None,
        }

    return {
        "entryId": (diff or {}).get("entry_id"),
        "trackId": (diff or {}).get("track_id"),
        "appId": (diff or {}).get("app_id"),
    }


def extract_consumed_nav(execute_result: Any, staged: Dict[str, Any]) -> Dict[str, Any]:
    """Preserve canonical destinations alongside legacy transcript navigation."""
    nav = _extract_consumed_nav(execute_result, staged)
    if isinstance(execute_result, dict) and isinstance(
        execute_result.get("result_links"), list
    ):
        nav["resultLinks"] = execute_result["result_links"]
    return nav
