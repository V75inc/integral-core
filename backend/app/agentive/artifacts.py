"""Session-scoped working artifacts for the resident harness.

Harness-agnostic scratchpad: blueprints, checklists, notes keyed by
``(user, session, key)``. Persisted on ``ChatThread.artifacts`` so any
binding can upsert/get without Integral-specific UI cards.
"""

from __future__ import annotations

import asyncio
import re
from contextlib import asynccontextmanager
from copy import deepcopy
from typing import Any, Dict, List, Optional

from app.utils.time import utc_now_iso

# Soft size caps — artifacts are conversation working memory, not files.
_MAX_BODY_CHARS = 100_000
_MAX_KEY_LEN = 128
_MAX_TITLE_LEN = 240
_MAX_KIND_LEN = 64


def _normalize_key(key: str) -> str:
    return (key or "").strip()[:_MAX_KEY_LEN]


async def _resolve_artifact_thread(
    *, user_id: str, session_id: str, workspace_id: Optional[str]
):
    """Resolve private working state under the host-bound workspace, if given.

    Direct legacy host callers may omit workspace_id; resident tool dispatch
    always supplies it. Model arguments never set the workspace.
    """
    if workspace_id is None:
        # Compatibility for trusted host callers that already resolved their
        # conversation. The model-facing dispatch always supplies a scope.
        from app.services.chat_threads import get_thread_by_session

        thread = await get_thread_by_session(session_id)
        if thread is None or (getattr(thread, "user_id", "") or "") != user_id:
            return None
        return thread

    if not workspace_id:
        return None
    from app.models.nodes import ChatThread
    from app.services.workspace_permissions import can_access_workspace

    if await can_access_workspace(user_id, workspace_id) == "none":
        return None
    if session_id.startswith("n.ChatThread."):
        candidate = await ChatThread.get(session_id)
        candidates = [candidate] if candidate is not None else []
    else:
        from jvspatial.core.context import get_default_context

        candidates = await get_default_context().find(
            ChatThread,
            {
                "context.user_id": user_id,
                "context.workspace_id": workspace_id,
                "context.provider_session_id": session_id,
                "context.archived": False,
            },
            limit=2,
        )
    if len(candidates) != 1:
        return None
    thread = candidates[0]
    if (
        thread.user_id != user_id
        or thread.workspace_id != workspace_id
        or thread.archived
        or (
            (thread.id != session_id or thread.provider_id != "integral_native")
            if session_id.startswith("n.ChatThread.")
            else thread.provider_session_id != session_id
        )
    ):
        return None
    return thread


@asynccontextmanager
async def _artifact_thread_access(
    *, user_id: str, session_id: str, workspace_id: Optional[str]
):
    """Bind access to live authority and serialize changes on the thread row."""
    thread = await _resolve_artifact_thread(
        user_id=user_id, session_id=session_id, workspace_id=workspace_id
    )
    if thread is None:
        yield None
        return
    actual_workspace = workspace_id or getattr(thread, "workspace_id", "")
    if not actual_workspace:
        # Old host-only records without workspace identity retain the legacy
        # local behavior. Model-facing dispatch always requires a workspace.
        yield thread
        return
    from app.models.nodes import ChatThread
    from app.services.app_operations.transaction_scope import (
        current_graph_transaction_database,
        graph_transaction_available,
    )
    from app.services.release_admission import release_admission

    async with release_admission(user_id, actual_workspace):
        row = None
        if graph_transaction_available():
            transaction = current_graph_transaction_database()
            row = await transaction.lock_record("node", thread.id)
            if row is None or row.get("entity") != ChatThread._entity_name():
                yield None
                return
        # Re-resolve after the row lock and authority admission; stale cached
        # ownership, archived state, or a changed session is never sufficient.
        current = await _resolve_artifact_thread(
            user_id=user_id, session_id=session_id, workspace_id=actual_workspace
        )
        if current is None or current.id != thread.id:
            yield None
            return
        if row is not None:
            from jvspatial.core.context import get_default_context

            current = await get_default_context()._deserialize_entity(ChatThread, row)
            if (
                current is None
                or current.user_id != user_id
                or current.workspace_id != actual_workspace
                or current.archived
                or (
                    (
                        current.id != session_id
                        or current.provider_id != "integral_native"
                    )
                    if session_id.startswith("n.ChatThread.")
                    else current.provider_session_id != session_id
                )
            ):
                yield None
                return
        yield current


async def _save_artifact_map(thread, store: Dict[str, Any]) -> None:
    """Update only artifacts, preserving concurrent unrelated thread fields."""
    from app.services.app_operations.transaction_scope import (
        current_graph_transaction_database,
    )

    transaction = current_graph_transaction_database(required=False)
    if transaction is None:
        thread.artifacts = store
        await thread.save()
        return
    updated = await transaction.find_one_and_update(
        "node",
        {"id": thread.id, "entity": thread._entity_name()},
        {"$set": {"context.artifacts": store}},
    )
    if updated is None:
        raise RuntimeError("Conversation disappeared while saving an artifact")


async def upsert_artifact(
    *,
    user_id: str,
    session_id: Optional[str],
    key: str,
    workspace_id: Optional[str] = None,
    kind: str,
    expected_version: Optional[int] = None,
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
    if expected_version is not None and (
        type(expected_version) is not int or expected_version < 0
    ):
        return {
            "error": "invalid_version",
            "detail": "Expected version must be a non-negative integer.",
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

    draft_format = ""
    if kind_s == "document_draft":
        if (
            not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}", art_key)
            or art_key != key
        ):
            return {
                "error": "invalid_key",
                "detail": "Use letters, numbers, dots, underscores or hyphens in the draft key.",
            }
        draft_format = str((metadata or {}).get("format") or "").strip().lower()
        if expected_version is None:
            return {
                "error": "version_required",
                "detail": "Document drafts require an expected version.",
            }
        from app.services.document_drafts import render_document_draft

        # Do not spend rendering work for an unavailable or foreign session.
        if (
            await _resolve_artifact_thread(
                user_id=user_id, session_id=session_id, workspace_id=workspace_id
            )
            is None
        ):
            return {"error": "not_found", "detail": "Chat thread not found"}
        try:
            await asyncio.to_thread(
                render_document_draft,
                title=(title or "").strip(),
                body=body_s,
                format=draft_format,
            )
        except (ValueError, ImportError) as exc:
            return {"error": "draft_render_failed", "detail": str(exc)}
        # Only Core-owned display metadata is retained for document drafts.
        metadata = {"format": draft_format}

    async with _artifact_thread_access(
        user_id=user_id, session_id=session_id, workspace_id=workspace_id
    ) as thread:
        if thread is None:
            return {"error": "not_found", "detail": "Chat thread not found"}

        store: Dict[str, Any] = dict(getattr(thread, "artifacts", None) or {})
        prev = store.get(art_key) if isinstance(store.get(art_key), dict) else None
        current_version = int((prev or {}).get("version") or 0)
        # A caller must not bypass the draft's revision fence by replacing it
        # with another artifact kind. Check this under the same thread lock
        # as the write, since a concurrent call could have created a draft.
        if prev and prev.get("kind") == "document_draft" and expected_version is None:
            return {
                "error": "version_required",
                "detail": "Replacing a document draft requires its expected version.",
            }
        if expected_version is not None and expected_version != current_version:
            return {
                "error": "version_conflict",
                "detail": "Artifact changed; reload before revising.",
                "current_version": current_version,
            }
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
        await _save_artifact_map(thread, store)
        result = {
            "ok": True,
            "_kind": "document_draft" if draft_format else "artifact",
            "key": art_key,
            "kind": kind_s,
            "title": entry["title"],
            "version": entry["version"],
            "updated_at": now,
            "replaced": prev is not None,
        }
        if draft_format:
            result.update(thread_id=thread.id, format=draft_format)
        return result


async def get_artifact(
    *,
    user_id: str,
    session_id: Optional[str],
    key: str,
    workspace_id: Optional[str] = None,
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

    async with _artifact_thread_access(
        user_id=user_id, session_id=session_id, workspace_id=workspace_id
    ) as thread:
        if thread is None:
            return {"error": "not_found", "detail": "Chat thread not found"}

        store: Dict[str, Any] = dict(getattr(thread, "artifacts", None) or {})
        entry = store.get(art_key)
        if not isinstance(entry, dict):
            return {
                "error": "not_found",
                "detail": f"No artifact with key {art_key!r}.",
            }
        result = {"ok": True, "_kind": "artifact", **entry}
        marker = getattr(thread, "design_proposed", None) or {}
        if (
            art_key == "app_design_blueprint"
            and marker.get("artifact_key") == art_key
            and isinstance(marker.get("blueprint"), dict)
        ):
            # The preview alone cannot reproduce stable field keys or entry-type
            # names. Read the current typed revision from design authority, not
            # editable scratch metadata or an older conversation message. This
            # also repairs readback of artifacts saved before typed projection.
            result.update(
                kind="app_design_blueprint",
                metadata={"source": "saved_design"},
                body=str(marker.get("proposal") or ""),
                title=str(marker.get("summary") or entry.get("title") or "App design"),
                blueprint=deepcopy(marker["blueprint"]),
                design_id=marker.get("design_id"),
                blueprint_revision=marker.get("blueprint_revision"),
                blueprint_digest=marker.get("blueprint_digest"),
            )
        return result


async def list_artifacts(
    *,
    user_id: str,
    session_id: Optional[str],
    kind: Optional[str] = None,
    workspace_id: Optional[str] = None,
) -> dict:
    """List artifacts for the session, optionally filtered by kind."""
    if not session_id:
        return {
            "error": "session_required",
            "detail": "Artifacts require a conversation session.",
        }

    async with _artifact_thread_access(
        user_id=user_id, session_id=session_id, workspace_id=workspace_id
    ) as thread:
        if thread is None:
            return {"error": "not_found", "detail": "Chat thread not found"}

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
        return {
            "ok": True,
            "_kind": "artifact_list",
            "items": items,
            "count": len(items),
        }
