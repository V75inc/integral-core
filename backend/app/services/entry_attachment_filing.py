"""File existing chat attachment graph state with the Entry's exact receipt."""

from __future__ import annotations

import logging
from typing import Any

from jvspatial.core.context import get_default_context

from app.api.errors import InsufficientPermissionsError, ResourceNotFoundError
from app.models.edges import CATALOGS, CONTAINS, HAS_ATTACHMENT
from app.models.nodes import Attachment, ChatThread, ChatThreads, Entry
from app.services.entry_write_scope import (
    entry_write_scope,
    is_postgres_entry_transaction,
)
from app.utils.time import utc_now_iso

logger = logging.getLogger(__name__)


async def file_chat_attachment(
    *, user_id: str, attachment_id: str, entry_id: str
) -> dict[str, Any]:
    """Join ownership, Entry revision, relationship, event intent and receipt.

    This path never writes attachment bytes or changes storage accounting.
    Its source remains linked to its original chat; that edge is not an
    additional Entry home or a new permission grant.
    """
    from app.agentive.services.staging_execution import staged_command_effect
    from app.api.utils import export_node
    from app.services.app_operations.event_outbox import insert_entry_event
    from app.services.attachment_agent import validate_chat_attachment_for_entry
    from app.services.authority_facts import primary_node_record
    from app.services.entry_effect_scope import entry_effect_scope
    from app.services.entry_update import update_entry_in_track
    from app.services.release_admission import release_admission
    from app.services.resource_location import DATA_APP_STATES, locate_resource

    location = await locate_resource(
        "entry", entry_id, allowed_app_states=DATA_APP_STATES
    )
    if location is None:
        raise ResourceNotFoundError(message="Entry not found")
    caller_context = get_default_context()
    outbox = None
    event = None
    async with (
        entry_write_scope(entry_id) as graph,
        release_admission(user_id, location.workspace_id),
    ):
        if (
            await locate_resource("entry", entry_id, allowed_app_states=DATA_APP_STATES)
            != location
        ):
            raise InsufficientPermissionsError(message="Entry scope changed")
        row = await graph.database.find_one_and_update(
            "node", {"id": entry_id, "entity": Entry._entity_name()}, {"$set": {}}
        )
        entry = await graph._deserialize_entity(Entry, row) if row else None
        if entry is None:
            raise ResourceNotFoundError(message="Entry not found")
        before = list(entry.attachment_ids or [])
        next_ids = list(dict.fromkeys([*before, attachment_id]))
        async with (
            entry_effect_scope(
                user_id=user_id,
                actor_kind="human",
                actor_id=None,
                action="entry.update",
                target_kind="entry",
                target_id=entry_id,
                workspace_id=location.workspace_id,
                payload={"entry_id": entry_id, "attachment_ids": next_ids},
            ),
            staged_command_effect(
                user_id=user_id,
                workspace_id=location.workspace_id,
                kind="attach_uploaded_file",
                targets={"entry_id": entry_id, "attachment_id": attachment_id},
            ) as complete,
        ):
            attachment_row = await graph.database.find_one_and_update(
                "node",
                {"id": attachment_id, "entity": Attachment._entity_name()},
                {"$set": {}},
            )
            if attachment_row is None:
                raise ResourceNotFoundError(message="Attachment not found")
            source = attachment_row.get("context") or {}
            if (
                source.get("owner_kind") != "chat"
                or source.get("scan_status") == "blocked"
            ):
                raise InsufficientPermissionsError(
                    message="Attachment source is unavailable"
                )
            attachment, _, error = await validate_chat_attachment_for_entry(
                user_id=user_id, attachment_id=attachment_id, entry_id=entry_id
            )
            if error:
                raise InsufficientPermissionsError(
                    message="Attachment source is unavailable"
                )
            attachment = await graph._deserialize_entity(Attachment, attachment_row)
            if attachment is None:
                raise ResourceNotFoundError(message="Attachment not found")
            parents = await graph.database.find(
                "edge",
                {"entity": HAS_ATTACHMENT._entity_name(), "target": attachment_id},
                limit=2,
            )
            if len(parents) != 1:
                raise InsufficientPermissionsError(
                    message="Attachment source is unavailable"
                )
            thread_id = parents[0].get("source")
            if (
                not thread_id
                or await graph.database.find_one_and_update(
                    "node",
                    {"id": thread_id, "entity": ChatThread._entity_name()},
                    {"$set": {}},
                )
                is None
            ):
                raise InsufficientPermissionsError(
                    message="Attachment source is unavailable"
                )
            thread = await primary_node_record(graph.database, ChatThread, thread_id)
            catalogs = await graph.database.find(
                "edge",
                {"entity": CATALOGS._entity_name(), "target": thread_id},
                limit=2,
            )
            branch = (
                await primary_node_record(
                    graph.database, ChatThreads, catalogs[0].get("source", "")
                )
                if len(catalogs) == 1
                else None
            )
            roots = (
                await graph.database.find(
                    "edge",
                    {"entity": CONTAINS._entity_name(), "target": branch["id"]},
                    limit=2,
                )
                if branch is not None
                else []
            )
            if (
                thread is None
                or thread["context"].get("user_id") != user_id
                or thread["context"].get("workspace_id") != location.workspace_id
                or branch is None
                or branch["context"].get("workspace_id") != location.workspace_id
                or len(roots) != 1
                or roots[0].get("source") != location.workspace_id
            ):
                raise InsufficientPermissionsError(
                    message="Attachment source is unavailable"
                )
            updated = await update_entry_in_track(
                entry_id=entry_id,
                user_id=user_id,
                workspace_id=location.workspace_id,
                attachment_ids=next_ids,
                expected_record_revision=int(entry.record_revision or 1),
                change_event_sink=lambda _: None,
            )
            await updated.connect(
                attachment,
                edge=HAS_ATTACHMENT,
                attached_at=utc_now_iso(),
                attached_by=user_id,
            )
            attachment.owner_kind = "entry"
            await attachment.save()
            event = {
                "actor_kind": "human",
                "actor_id": user_id,
                "action": "attachment.attach",
                "resource_type": "Entry",
                "resource_id": entry_id,
                "before": {"attachment_ids": before},
                "after": {
                    "attachment_ids": next_ids,
                    "record_revision": updated.record_revision,
                },
                "scope": f"track:{location.track_id}",
                "details": {
                    "attachment_id": attachment_id,
                    "attachment_owner_kind_before": "chat",
                },
            }
            if is_postgres_entry_transaction(graph.database):
                outbox = await insert_entry_event(
                    transaction=graph.database,
                    workspace_id=location.workspace_id,
                    event=event,
                )
            if complete is not None:
                await complete(
                    {
                        "entry_id": entry_id,
                        "attachment_id": attachment_id,
                        "record_revision": updated.record_revision,
                    }
                )
    if is_postgres_entry_transaction(graph.database):
        if outbox and graph is not caller_context:
            from app.services.app_operations.event_outbox import deliver_operation_event

            try:
                await deliver_operation_event(outbox_id=outbox)
            except Exception:
                logger.exception("Attachment filing event awaits recovery")
    elif event is not None:
        from app.services.change_event import emit_change_event

        await emit_change_event(**event)
    await attachment.set_context(caller_context)
    return {"attachment": await export_node(attachment), "entry_id": entry_id}
