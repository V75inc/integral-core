"""Revision-bound workspace control over automatic image descriptions."""

from app.api.errors import InsufficientPermissionsError, ResourceConflictError
from app.config import settings
from app.schemas.attachment_analysis import (
    AttachmentAnalysisChoice,
    AttachmentAnalysisWrite,
)
from app.services.retrieval.embedding_configuration import owner_subject
from app.services.workspace_model_configuration import configuration_record
from app.services.workspace_permissions import can_access_workspace


def supported() -> bool:
    """Do not fund analysis without a serving passage adapter and effect fence."""
    from jvspatial.core.context import get_default_context

    from app.services.app_operations.transaction_scope import (
        graph_transaction_available,
    )
    from app.services.retrieval import get_embedding_store
    from app.services.retrieval.pgvector_driver import PgVectorEmbeddingStore

    return isinstance(
        get_embedding_store(), PgVectorEmbeddingStore
    ) and graph_transaction_available(get_default_context().database)


async def enabled(workspace_id: str) -> bool:
    """Read current rooted configuration at every provider admission."""
    record = await configuration_record(workspace_id)
    return (
        settings.ATTACHMENT_IMAGE_DESCRIPTIONS_ENABLED
        and record is not None
        and AttachmentAnalysisChoice.model_validate(
            record.attachment_settings or {}
        ).enabled
    )


async def describe(user_id: str, workspace_id: str) -> dict:
    """Expose the selected processing behavior, never provider credentials."""
    if await can_access_workspace(user_id, workspace_id) == "none":
        raise InsufficientPermissionsError(message="Workspace is unavailable")
    record = await configuration_record(workspace_id)
    if record is None:
        raise InsufficientPermissionsError(message="Workspace is unavailable")
    return {
        "workspace_id": workspace_id,
        "editable": await owner_subject(workspace_id) == user_id,
        "revision": record.attachment_settings_revision,
        "configuration": AttachmentAnalysisChoice.model_validate(
            record.attachment_settings or {}
        ).model_dump(),
        "available": settings.ATTACHMENT_IMAGE_DESCRIPTIONS_ENABLED and supported(),
    }


async def save(user_id: str, workspace_id: str, body: AttachmentAnalysisWrite) -> dict:
    """Save policy and its audit intent together; no parsing/model call on save."""
    from jvspatial.core.context import get_default_context

    from app.services.app_operations.event_outbox import (
        deliver_operation_event,
        insert_revision_event,
    )
    from app.services.change_event import emit_change_event
    from app.services.entry_write_scope import (
        entry_write_scope,
        is_postgres_entry_transaction,
    )

    caller = get_default_context()
    outbox = None
    async with entry_write_scope(workspace_id) as graph:
        if await owner_subject(workspace_id) != user_id:
            raise InsufficientPermissionsError(
                message="Only the workspace owner can change image processing"
            )
        record = await configuration_record(workspace_id, create=True)
        if record is None or record.attachment_settings_revision != body.revision:
            raise ResourceConflictError(
                message="Image processing settings changed. Reload before saving."
            )
        if await owner_subject(workspace_id) != user_id:
            raise InsufficientPermissionsError(message="Workspace ownership changed")
        match = (
            {"context.attachment_settings_revision": body.revision}
            if body.revision
            else {
                "$or": [
                    {"context.attachment_settings_revision": 0},
                    {"context.attachment_settings_revision": {"$exists": False}},
                ]
            }
        )
        updated = await graph.database.find_one_and_update(
            "node",
            {"id": record.id, "entity": "WorkspaceModelConfiguration", **match},
            {
                "$set": {
                    "context.attachment_settings": {"enabled": body.enabled},
                    "context.attachment_settings_revision": body.revision + 1,
                }
            },
        )
        if updated is None:
            raise ResourceConflictError(
                message="Image processing settings changed. Reload before saving."
            )
        event = {
            "actor_kind": "human",
            "actor_id": user_id,
            "action": "workspace.update",
            "resource_type": "Workspace",
            "resource_id": workspace_id,
            "scope": f"workspace:{workspace_id}",
            "before": None,
            "after": {
                "automatic_image_descriptions": body.enabled,
                "attachment_settings_revision": body.revision + 1,
            },
        }
        if is_postgres_entry_transaction(graph.database):
            outbox = await insert_revision_event(
                transaction=graph.database,
                workspace_id=workspace_id,
                namespace="attachment-analysis-v1",
                subject_id=workspace_id,
                revision=body.revision + 1,
                event=event,
            )
        else:
            await emit_change_event(**event)
    if outbox and graph is not caller:
        try:
            await deliver_operation_event(outbox_id=outbox)
        except Exception:
            import logging

            logging.getLogger(__name__).exception(
                "Image processing audit awaits recovery"
            )
    return await describe(user_id, workspace_id)
