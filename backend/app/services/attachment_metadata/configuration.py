"""Revision-bound workspace control over automatic image descriptions."""

import asyncio

from app.api.errors import (
    BadRequestError,
    InsufficientPermissionsError,
    ResourceConflictError,
)
from app.config import settings
from app.models.credentials import (
    UserModelCredential,
    UserModelProfile,
)
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


async def active_choice(workspace_id: str) -> AttachmentAnalysisChoice | None:
    """Capture the enabled flag and selection from the same rooted revision."""
    if not settings.ATTACHMENT_IMAGE_DESCRIPTIONS_ENABLED:
        return None
    record = await configuration_record(workspace_id)
    if record is None:
        return None
    choice = AttachmentAnalysisChoice.model_validate(record.attachment_settings or {})
    return choice if choice.enabled else None


async def recommended_profile(workspace_id: str):
    """Follow the active connection only when its recommended vision profile exists."""
    from app.services.model_credential_resolver import _workspace_owner_credential
    from app.services.model_harness_policy import SPECIALIST_MODELS

    if settings.INTEGRAL_AGENT_KEY_MODE == "platform_only":
        return None
    record = await _workspace_owner_credential(workspace_id)
    if not record or not record.active_profile_id:
        return None
    model = SPECIALIST_MODELS.get(record.provider, {}).get("vision")
    active = await UserModelProfile.get(record.active_profile_id)
    if not model or not active or active.user_id != record.user_id:
        return None
    return await _matching_profile(record.user_id, active.connection_id, model)


async def _matching_profile(owner: str, connection_id: str, model: str):
    from app.services.ai_configuration import _find

    profiles = await _find(
        UserModelProfile,
        {
            "context.user_id": owner,
            "context.connection_id": connection_id,
            "context.model": model,
        },
        limit=101,
    )
    return min(
        (p for p in profiles if not p.embedding_only), key=lambda p: p.id, default=None
    )


async def owned_vision_credential(owner: str, profile_id: str):
    """Validate the selected private profile independently of the chat model."""
    from app.services.ai_configuration import profile_credential
    from app.services.model_settings import model_capabilities

    if not owner or settings.INTEGRAL_AGENT_KEY_MODE == "platform_only":
        raise ValueError("Personal vision models are unavailable")
    # An in-memory credential envelope carries the profile/connection generation
    # into the same funding adapter as chat. It is never saved or made default.
    record = await profile_credential(
        owner, profile_id, UserModelCredential(id=profile_id, user_id=owner)
    )
    caps = await asyncio.to_thread(model_capabilities, record.provider, record.model)
    if caps.get("image_descriptions") is not True:
        raise ValueError("Choose a registered model that supports image input")
    return record


async def describe(user_id: str, workspace_id: str) -> dict:
    """Expose the selected processing behavior, never provider credentials."""
    if await can_access_workspace(user_id, workspace_id) == "none":
        raise InsufficientPermissionsError(message="Workspace is unavailable")
    record = await configuration_record(workspace_id)
    if record is None:
        raise InsufficientPermissionsError(message="Workspace is unavailable")
    choice = AttachmentAnalysisChoice.model_validate(record.attachment_settings or {})
    owner = await owner_subject(workspace_id) == user_id
    recommended = (
        await recommended_profile(workspace_id)
        if not choice.profile_id and owner
        else None
    )
    return {
        "recommended_model": recommended.model if recommended else "",
        "recommended_profile_id": recommended.id if recommended else "",
        "can_choose_profile": owner
        and settings.INTEGRAL_AGENT_KEY_MODE != "platform_only",
        "workspace_id": workspace_id,
        "editable": owner,
        "revision": record.attachment_settings_revision,
        "configuration": choice.model_dump() if owner else {"enabled": choice.enabled},
        "available": settings.ATTACHMENT_IMAGE_DESCRIPTIONS_ENABLED and supported(),
    }


async def save(user_id: str, workspace_id: str, body: AttachmentAnalysisWrite) -> dict:
    """Save policy and its audit intent together; no parsing/model call on save."""
    from jvspatial.core.context import get_default_context

    from app.services.app_operations.event_outbox import (
        insert_revision_event,
        request_event_delivery,
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
        previous = AttachmentAnalysisChoice.model_validate(
            record.attachment_settings or {}
        )
        # Older clients only submit enabled; toggling processing must keep the binding.
        profile_id = (
            body.profile_id
            if "profile_id" in body.model_fields_set
            else previous.profile_id
        )
        try:
            if profile_id and (body.enabled or profile_id != previous.profile_id):
                await owned_vision_credential(user_id, profile_id)
        except ValueError as exc:
            raise BadRequestError(str(exc)) from None
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
                    "context.attachment_settings": {
                        "enabled": body.enabled,
                        "profile_id": profile_id,
                    },
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
            await request_event_delivery(outbox_id=outbox)
        except Exception:
            import logging

            logging.getLogger(__name__).exception(
                "Image processing audit awaits recovery"
            )
    return await describe(user_id, workspace_id)
