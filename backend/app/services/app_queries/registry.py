"""In-process App declared-query registry (mirrors app_operations.registry)."""

from __future__ import annotations

from typing import Any, Dict

_QUERIES: Dict[str, Dict[str, Dict[str, Dict[str, Any]]]] = {}


def register_app_queries(
    workspace_id: str,
    app_id: str,
    bundle_slug: str,
    queries: list,
) -> None:
    ws = _QUERIES.setdefault(workspace_id, {})
    bucket: Dict[str, Dict[str, Any]] = {}
    for q in queries:
        item = dict(q)
        item["_bundle_slug"] = bundle_slug
        key = str(item.get("key") or "").strip()
        if key:
            bucket[key] = item
    ws[app_id] = bucket


def unregister_app_queries(workspace_id: str, app_id: str) -> None:
    ws = _QUERIES.get(workspace_id)
    if not ws:
        return
    ws.pop(app_id, None)
    if not ws:
        _QUERIES.pop(workspace_id, None)


def get_app_query(workspace_id: str, app_id: str, query_key: str):
    return (_QUERIES.get(workspace_id) or {}).get(app_id, {}).get(query_key)


def list_registered_queries(
    workspace_id: str, app_id: str
) -> Dict[str, Dict[str, Any]]:
    return dict((_QUERIES.get(workspace_id) or {}).get(app_id) or {})


def clear_workspace_queries(workspace_id: str) -> None:
    _QUERIES.pop(workspace_id, None)


def all_workspace_query_apps(workspace_id: str) -> Dict[str, Dict[str, Dict[str, Any]]]:
    return dict(_QUERIES.get(workspace_id) or {})
