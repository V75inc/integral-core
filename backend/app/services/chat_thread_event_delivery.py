"""Committed chat invalidations to every API process's authorized local sockets.

The existing event outbox/logger/relay supplies durable ordering. Frames contain
only current scoped IDs and a revision, never messages or historical snapshots.
Reconnect recovers authoritative state through the existing thread read contract.
"""

from __future__ import annotations

import asyncio
import json
from typing import Any

from app.services.authority_facts import primary_node_record, required_node_text
from app.utils.time import utc_now_iso

_queues: dict[int, asyncio.Queue] = {}
_tasks: dict[int, asyncio.Task] = {}


def stop_chat_event_delivery(websocket: Any) -> None:
    """Cancel a socket's sender and discard its bounded queue on unregister."""
    key = id(websocket)
    _queues.pop(key, None)
    task = _tasks.pop(key, None)
    if task is not None and task is not asyncio.current_task():
        task.cancel()


def queue_committed_chat_event(event: Any) -> None:
    """Queue a committed invalidation without blocking the shared API relay."""
    if event.action != "chat.message.create" or event.resource_type != "ChatThread":
        return
    from app.agentive.services.agent_events import _agent_event_connections

    # Scope selects a candidate audience only. Each actual frame still needs
    # current identity, membership, ownership and graph checks below.
    for user_id, sockets in list(_agent_event_connections.items()):
        if event.scope != f"user:{user_id}":
            continue
        for socket in list(sockets):
            key = id(socket)
            queue = _queues.setdefault(key, asyncio.Queue(maxsize=100))
            try:
                queue.put_nowait(event)
                if key not in _tasks:
                    _tasks[key] = asyncio.create_task(_deliver(user_id, socket, queue))
            except asyncio.QueueFull:
                stop_chat_event_delivery(socket)
                asyncio.create_task(_disconnect(user_id, socket))


async def _disconnect(user_id: str, socket: Any) -> None:
    from app.agentive.services.agent_events import unregister_agent_event_connection

    try:
        await asyncio.wait_for(socket.close(code=1013), timeout=1)
    except Exception:
        pass
    await unregister_agent_event_connection(user_id, socket)


async def current_chat_payload(user_id: str, event: Any) -> dict | None:
    """An unavailable/denied recipient withholds one frame, not its socket."""
    from jvspatial.core.context import get_default_context

    from app.models.edges import CATALOGS, CONTAINS
    from app.models.nodes import ChatMessage, ChatThread, ChatThreads
    from app.services.permissions import get_user_node
    from app.services.release_admission import release_admission
    from app.services.workspace_permissions import can_access_workspace

    try:
        database = get_default_context().database
        row = await primary_node_record(database, ChatThread, event.resource_id)
        if row is None:
            return None
        workspace_id = required_node_text(row, "workspace_id")
        async with release_admission(user_id, workspace_id):
            # Local stores have no distributed fence, but still require the
            # same current workspace authorization before releasing any IDs.
            if await can_access_workspace(user_id, workspace_id) == "none":
                return None
            database = get_default_context().database
            # Never hydrate defaults/cache as evidence of ownership or location.
            row = await primary_node_record(database, ChatThread, event.resource_id)
            if row is None or required_node_text(row, "workspace_id") != workspace_id:
                return None
            if required_node_text(row, "user_id") != user_id:
                return None
            if event.scope != f"user:{user_id}" or await get_user_node(user_id) is None:
                return None
            parents = await database.find(
                "edge",
                {"target": row["id"], "entity": CATALOGS._entity_name()},
                limit=2,
            )
            if len(parents) != 1:
                return None
            branch_id = parents[0].get("source")
            branch = await primary_node_record(database, ChatThreads, branch_id)
            if (
                branch is None
                or required_node_text(branch, "workspace_id") != workspace_id
            ):
                return None
            parents = await database.find(
                "edge",
                {"target": branch_id, "entity": CONTAINS._entity_name()},
                limit=2,
            )
            if len(parents) != 1 or parents[0].get("source") != workspace_id:
                return None
            message_id = (event.details or {}).get("message_id")
            if not isinstance(message_id, str) or not message_id:
                return None
            message = await primary_node_record(database, ChatMessage, message_id)
            if message is None or required_node_text(message, "thread_id") != row["id"]:
                return None
            parents = await database.find(
                "edge",
                {"target": message_id, "entity": CONTAINS._entity_name()},
                limit=2,
            )
            if len(parents) != 1 or parents[0].get("source") != row["id"]:
                return None
            return {
                "thread_id": row["id"],
                "workspace_id": workspace_id,
                "status": "message",
                "message_id": message_id,
                "revision": required_node_text(row, "updated_at"),
                "event_id": str(event.id or ""),
            }
    except Exception:
        # Explicit denial and unavailable authority both fail closed. A later
        # authorized event can still use the same socket and authoritative read.
        return None


async def _deliver(user_id: str, socket: Any, queue: asyncio.Queue) -> None:
    from app.agentive.services.agent_events import _agent_event_connections

    try:
        while socket in _agent_event_connections.get(user_id, []):
            event = await queue.get()
            payload = await asyncio.wait_for(
                current_chat_payload(user_id, event), timeout=5
            )
            if payload is not None:
                # Admission has released every transaction/lock before network IO.
                await asyncio.wait_for(
                    socket.send_text(
                        json.dumps(
                            {
                                "type": "thread_stream_update",
                                "payload": payload,
                                "timestamp": utc_now_iso(),
                            }
                        )
                    ),
                    timeout=2,
                )
    except asyncio.CancelledError:
        raise
    except Exception:
        await _disconnect(user_id, socket)
    finally:
        stop_chat_event_delivery(socket)
