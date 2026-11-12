"""Commit chat upload visibility and workspace charge in one transaction."""

from __future__ import annotations

from jvspatial.core.context import get_default_context

from app.api.errors import ResourceConflictError, ResourceNotFoundError
from app.config import settings
from app.models.nodes import Attachment, ChatThread, Workspace
from app.services.app_operations.transaction_scope import (
    current_graph_transaction_database,
)
from app.services.release_admission import release_admission


async def finalize_chat_upload(
    *,
    user_id: str,
    workspace_id: str,
    thread_id: str,
    attachment_id: str,
    content_hash: str,
    size: int,
    storage_key: str,
    scan_status: str,
    scan_engine: str,
    scan_message: str,
) -> tuple[Attachment, bool, str]:
    """Fence the pending source, quota and charge; return whether quota refused it."""
    async with release_admission(user_id, workspace_id):
        database = current_graph_transaction_database()
        workspace = await database.lock_record("node", workspace_id)
        attachment = await database.lock_record("node", attachment_id)
        thread = await database.lock_record("node", thread_id)
        if (
            not workspace
            or workspace.get("entity") != Workspace._entity_name()
            or not attachment
            or attachment.get("entity") != Attachment._entity_name()
            or not thread
            or thread.get("entity") != ChatThread._entity_name()
        ):
            raise ResourceNotFoundError(message="Chat attachment not found")
        source = attachment.get("context") or {}
        conversation = thread.get("context") or {}
        if (
            source.get("owner_kind") != "chat"
            or source.get("uploaded_by") != user_id
            or source.get("content_hash") != content_hash
            or source.get("size") != size
            or source.get("storage_key") != storage_key
            or source.get("scan_status") != "pending"
            or conversation.get("user_id") != user_id
            or conversation.get("workspace_id") != workspace_id
            or conversation.get("archived") is not False
        ):
            raise ResourceConflictError(
                message="Chat attachment changed during upload."
            )
        usage = workspace.get("context") or {}
        used = int(usage.get("storage_bytes_used") or 0)
        quota = int(usage.get("storage_quota_bytes") or 0)
        rejected = (
            bool(storage_key)
            and scan_status != "blocked"
            and settings.STORAGE_QUOTA_ENFORCE
            and quota > 0
            and used + size > quota
        )
        graph = get_default_context()
        current = await graph._deserialize_entity(Attachment, attachment)
        current.scan_status = "quota_rejected" if rejected else scan_status
        if scan_status == "blocked":
            current.storage_key = ""
        current.scan_engine = scan_engine
        current.scan_message = (
            "Workspace storage quota exceeded." if rejected else scan_message
        )
        if rejected:
            current.metadata_status = "failed"
        await current.save()
        if storage_key and scan_status != "blocked" and not rejected:
            await database.find_one_and_update(
                "node",
                {"id": workspace_id, "entity": Workspace._entity_name()},
                {"$inc": {"context.storage_bytes_used": size}},
            )
            await graph._evict_from_cache(workspace_id)
        outbox = ""
        if not rejected:
            from app.api.utils import export_node
            from app.services.app_operations.event_outbox import insert_revision_event

            outbox = await insert_revision_event(
                transaction=database,
                workspace_id=workspace_id,
                namespace="chat-upload-create-v1",
                subject_id=attachment_id,
                revision=1,
                event={
                    "actor_kind": "human",
                    "actor_id": user_id,
                    "action": "attachment.create",
                    "resource_type": "Attachment",
                    "resource_id": attachment_id,
                    "before": None,
                    "after": await export_node(current),
                    "scope": f"chat_thread:{thread_id}",
                },
            )
        return current, rejected, outbox
