"""Build an activity feed for a RoutineTask — runs + conversation turns.

Surfaces what the Background Tasks UI needs without inventing a separate
job-log store: ChangeEvents already emitted by the scheduler, plus
assistant messages on the bound chat thread tagged ``origin=routine_task``.
"""

from __future__ import annotations

import logging
import re
from typing import Any, Dict, List, Optional

from app.agentive.nodes import RoutineTask
from app.services import chat_threads as chat_store
from app.services.change_event_logger import (
    envelope_from_dblog,
    get_change_event_logger,
    is_change_event_enabled,
)

logger = logging.getLogger(__name__)

_ENTITY_ID_RE = re.compile(
    r"\b(?:n\.)?(Track|Entry|App|Space)\.([A-Za-z0-9_-]+)\b",
    re.IGNORECASE,
)
_HREF_RE = re.compile(
    r"(?:^|[\s(])(/(?:tracks|apps)/[A-Za-z0-9_.-]+(?:\?entry=[A-Za-z0-9_.-]+)?)"
)


def _text_from_parts(parts: Any) -> str:
    if not isinstance(parts, list):
        return ""
    chunks: List[str] = []
    for part in parts:
        if not isinstance(part, dict):
            continue
        if part.get("type") == "text" and part.get("text"):
            chunks.append(str(part["text"]))
    return "\n".join(chunks).strip()


async def _entry_href(entry_id: str) -> Optional[str]:
    try:
        from app.models.nodes import Entry

        entry = await Entry.get(entry_id)
        if entry is None:
            # Try with n.Entry. prefix stripped / added.
            alt = (
                entry_id[8:]
                if entry_id.startswith("n.Entry.")
                else f"n.Entry.{entry_id}"
            )
            entry = await Entry.get(alt)
        if entry is None:
            return None
        track_id = getattr(entry, "track_id", None) or ""
        if not track_id:
            return None
        return f"/tracks/{track_id}?entry={entry.id}"
    except Exception:  # noqa: BLE001
        return None


async def _links_from_text(text: str) -> List[Dict[str, str]]:
    links: List[Dict[str, str]] = []
    seen: set = set()

    for match in _HREF_RE.finditer(text):
        href = match.group(1)
        if href in seen:
            continue
        seen.add(href)
        kind = "track" if href.startswith("/tracks/") else "app"
        links.append(
            {
                "kind": kind,
                "id": href.split("/")[-1].split("?")[0],
                "label": href,
                "href": href,
            }
        )

    for match in _ENTITY_ID_RE.finditer(text):
        kind_raw = match.group(1).lower()
        entity_id = match.group(2)
        full = match.group(0)
        if kind_raw == "space":
            kind_raw = "app"
        node_id = full if full.startswith("n.") else f"n.{match.group(1)}.{entity_id}"
        if kind_raw == "track":
            href = f"/tracks/{node_id}"
            label = full
        elif kind_raw == "entry":
            href = await _entry_href(node_id)
            if not href:
                continue
            label = full
        elif kind_raw == "app":
            href = f"/apps/{node_id}"
            label = full
        else:
            continue
        if href in seen:
            continue
        seen.add(href)
        links.append(
            {
                "kind": kind_raw,
                "id": node_id,
                "label": label,
                "href": href,
            }
        )
    return links


async def _write_scope_links(
    write_scope: List[Dict[str, str]],
) -> List[Dict[str, str]]:
    links: List[Dict[str, str]] = []
    for grant in write_scope or []:
        rtype = (grant.get("resource_type") or "").lower()
        rid = grant.get("resource_id") or ""
        if not rid:
            continue
        if rtype == "track":
            href = f"/tracks/{rid}"
        elif rtype == "entry":
            href = await _entry_href(rid)
            if not href:
                continue
        elif rtype in ("app", "space"):
            href = f"/apps/{rid}"
        else:
            continue
        links.append(
            {
                "kind": rtype or "other",
                "id": rid,
                "label": f"{rtype}:{rid}" if rtype else rid,
                "href": href,
            }
        )
    return links


def _message_origin(meta: Any) -> Optional[str]:
    if not isinstance(meta, dict):
        return None
    interact = meta.get("interactPayload") or meta.get("interact_payload") or {}
    if isinstance(interact, dict) and interact.get("origin"):
        return str(interact["origin"])
    if meta.get("origin"):
        return str(meta["origin"])
    return None


async def build_routine_activity(
    routine: RoutineTask, *, limit: int = 40
) -> Dict[str, Any]:
    """Assemble run events + routine-origin chat turns for the activity panel."""
    events: List[Dict[str, Any]] = []

    if is_change_event_enabled():
        try:
            ce_logger = get_change_event_logger()
            scope = (
                f"workspace:{routine.workspace_id}" if routine.workspace_id else None
            )
            rows = await ce_logger.find_all(scope=scope, actor_kind="agent")
            for row in rows:
                env = envelope_from_dblog(row)
                if (env.resource_id or "") != routine.id:
                    continue
                if env.resource_type and env.resource_type != "RoutineTask":
                    continue
                details = env.details if isinstance(env.details, dict) else {}
                status = details.get("status")
                reason = details.get("reason")
                action = env.action or "routine_task.run_completed"
                if action == "routine_task.auto_paused":
                    summary = f"Auto-paused: {reason or 'repeated failures'}"
                elif action == "routine_task.completed":
                    summary = "Reached max runs and completed"
                elif status == "success":
                    summary = "Run succeeded"
                elif status == "error":
                    summary = f"Run failed{': ' + str(reason) if reason else ''}"
                else:
                    summary = action.replace("routine_task.", "").replace("_", " ")
                events.append(
                    {
                        "id": env.id or f"ce-{env.ts}-{action}",
                        "kind": "run_event",
                        "ts": env.ts,
                        "action": action,
                        "status": status,
                        "summary": summary,
                        "reason": reason,
                        "links": [],
                    }
                )
        except Exception:  # noqa: BLE001
            logger.warning(
                "build_routine_activity: change-event load failed for %s",
                routine.id,
                exc_info=True,
            )

    if routine.thread_id:
        try:
            thread = await chat_store.get_thread(routine.thread_id)
            if thread is not None and (
                not getattr(thread, "user_id", None)
                or thread.user_id == routine.user_id
            ):
                messages = await chat_store.list_messages(thread)
                for msg in messages:
                    meta = msg.provider_metadata or {}
                    if _message_origin(meta) != "routine_task":
                        continue
                    text = _text_from_parts(msg.parts)
                    if not text and isinstance(meta, dict):
                        final = meta.get("finalContent")
                        if isinstance(final, str):
                            text = final
                    preview = (text or "").strip()
                    if len(preview) > 280:
                        preview = preview[:277] + "…"
                    events.append(
                        {
                            "id": msg.id,
                            "kind": "message",
                            "ts": msg.created_at,
                            "action": None,
                            "status": None,
                            "summary": preview or "(empty message)",
                            "reason": None,
                            "message_id": msg.id,
                            "role": msg.role,
                            "links": await _links_from_text(text or ""),
                        }
                    )
        except Exception:  # noqa: BLE001
            logger.warning(
                "build_routine_activity: thread message load failed for %s",
                routine.id,
                exc_info=True,
            )

    events.sort(key=lambda e: e.get("ts") or "", reverse=True)
    if limit > 0:
        events = events[:limit]

    return {
        "routine_id": routine.id,
        "thread_id": routine.thread_id or "",
        "events": events,
        "write_scope_links": await _write_scope_links(list(routine.write_scope or [])),
    }
