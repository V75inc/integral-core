"""Session-scoped working artifacts for the resident harness.

Harness-agnostic scratchpad: blueprints, checklists, notes keyed by
``(user, session, key)``. Persisted on ``ChatThread.artifacts`` so any
binding can upsert/get without Integral-specific UI cards.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from app.utils.time import utc_now_iso

# Soft size caps — artifacts are conversation working memory, not files.
_MAX_BODY_CHARS = 100_000
_MAX_KEY_LEN = 128
_MAX_TITLE_LEN = 240
_MAX_KIND_LEN = 64


def _normalize_key(key: str) -> str:
    return (key or "").strip()[:_MAX_KEY_LEN]


async def upsert_artifact(
    *,
    user_id: str,
    session_id: Optional[str],
    key: str,
    kind: str,
    title: str = "",
    body: str = "",
    metadata: Optional[Dict[str, Any]] = None,
) -> dict:
    """Create or replace an artifact on the session thread."""
    if not session_id:
        return {
            "error": "session_required",
            "detail": "Artifacts require a conversation session.",
        }
    art_key = _normalize_key(key)
    if not art_key:
        return {"error": "key_required", "detail": "key must be non-empty."}
    kind_s = (kind or "").strip()[:_MAX_KIND_LEN]
    if not kind_s:
        return {"error": "kind_required", "detail": "kind must be non-empty."}
    body_s = body if isinstance(body, str) else str(body or "")
    if len(body_s) > _MAX_BODY_CHARS:
        return {
            "error": "body_too_large",
            "detail": f"body exceeds {_MAX_BODY_CHARS} characters.",
        }

    from app.services.chat_threads import get_thread_by_session

    thread = await get_thread_by_session(session_id)
    if thread is None:
        return {"error": "not_found", "detail": "Chat thread not found"}
    if (getattr(thread, "user_id", "") or "") != user_id:
        return {
            "error": "forbidden",
            "detail": "Thread does not belong to the caller",
        }

    store: Dict[str, Any] = dict(getattr(thread, "artifacts", None) or {})
    prev = store.get(art_key) if isinstance(store.get(art_key), dict) else None
    now = utc_now_iso()
    entry = {
        "key": art_key,
        "kind": kind_s,
        "title": (title or "").strip()[:_MAX_TITLE_LEN],
        "body": body_s,
        "metadata": dict(metadata or {}) if isinstance(metadata, dict) else {},
        "updated_at": now,
        "created_at": (prev or {}).get("created_at") or now,
        "version": int((prev or {}).get("version") or 0) + 1,
    }
    store[art_key] = entry
    thread.artifacts = store
    await thread.save()
    return {
        "ok": True,
        "_kind": "artifact",
        "key": art_key,
        "kind": kind_s,
        "title": entry["title"],
        "version": entry["version"],
        "updated_at": now,
        "replaced": prev is not None,
    }


async def get_artifact(
    *,
    user_id: str,
    session_id: Optional[str],
    key: str,
) -> dict:
    """Fetch one artifact by key."""
    if not session_id:
        return {
            "error": "session_required",
            "detail": "Artifacts require a conversation session.",
        }
    art_key = _normalize_key(key)
    if not art_key:
        return {"error": "key_required", "detail": "key must be non-empty."}

    from app.services.chat_threads import get_thread_by_session

    thread = await get_thread_by_session(session_id)
    if thread is None:
        return {"error": "not_found", "detail": "Chat thread not found"}
    if (getattr(thread, "user_id", "") or "") != user_id:
        return {
            "error": "forbidden",
            "detail": "Thread does not belong to the caller",
        }

    store: Dict[str, Any] = dict(getattr(thread, "artifacts", None) or {})
    entry = store.get(art_key)
    if not isinstance(entry, dict):
        return {
            "error": "not_found",
            "detail": f"No artifact with key {art_key!r}.",
        }
    return {"ok": True, "_kind": "artifact", **entry}


async def list_artifacts(
    *,
    user_id: str,
    session_id: Optional[str],
    kind: Optional[str] = None,
) -> dict:
    """List artifacts for the session, optionally filtered by kind."""
    if not session_id:
        return {
            "error": "session_required",
            "detail": "Artifacts require a conversation session.",
        }

    from app.services.chat_threads import get_thread_by_session

    thread = await get_thread_by_session(session_id)
    if thread is None:
        return {"error": "not_found", "detail": "Chat thread not found"}
    if (getattr(thread, "user_id", "") or "") != user_id:
        return {
            "error": "forbidden",
            "detail": "Thread does not belong to the caller",
        }

    store: Dict[str, Any] = dict(getattr(thread, "artifacts", None) or {})
    kind_f = (kind or "").strip() or None
    items: List[Dict[str, Any]] = []
    for entry in store.values():
        if not isinstance(entry, dict):
            continue
        if kind_f and entry.get("kind") != kind_f:
            continue
        items.append(
            {
                "key": entry.get("key"),
                "kind": entry.get("kind"),
                "title": entry.get("title"),
                "version": entry.get("version"),
                "updated_at": entry.get("updated_at"),
                "body_chars": len(str(entry.get("body") or "")),
            }
        )
    items.sort(key=lambda x: str(x.get("updated_at") or ""), reverse=True)
    return {"ok": True, "_kind": "artifact_list", "items": items, "count": len(items)}
