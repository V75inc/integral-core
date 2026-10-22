"""Owner-managed voice model references, separate from chat profile selection."""

from jvspatial.core.context import get_default_context

from app.api.errors import InsufficientPermissionsError, ResourceConflictError
from app.schemas.voice_configuration import VoiceModelWrite
from app.services.ai_configuration import _enabled
from app.services.retrieval.embedding_configuration import (
    owned_connection,
    owner_subject,
)
from app.services.workspace_model_configuration import configuration_record
from app.services.workspace_permissions import can_access_workspace


async def describe(user_id: str, workspace_id: str):
    """Describe effective voice selection without exposing provider keys."""
    from app.config import settings

    if await can_access_workspace(user_id, workspace_id) == "none":
        raise InsufficientPermissionsError(message="Workspace is unavailable")
    workspace = await configuration_record(workspace_id)
    editable = (
        await owner_subject(workspace_id) == user_id
        and settings.INTEGRAL_AGENT_KEY_MODE != "platform_only"
    )
    values = workspace.voice_settings or {
        "mode": "inherit",
        "model": "",
        "connection_id": "",
    }
    from app.services.model_credential_resolver import resolve_speech_credential

    effective = await resolve_speech_credential(workspace_id, touch=False)
    return {
        "workspace_id": workspace_id,
        "editable": editable,
        "revision": workspace.voice_settings_revision,
        "configuration": (
            values if editable else {"mode": values.get("mode", "inherit")}
        ),
        "effective_model": effective.model if effective else "",
        "effective_provider": effective.provider if effective else "",
    }


async def save(user_id: str, workspace_id: str, body: VoiceModelWrite):
    """Commit an owner-controlled voice choice and durable audit intent."""
    from app.services.app_operations.event_outbox import (
        deliver_operation_event,
        insert_revision_event,
    )
    from app.services.entry_write_scope import (
        entry_write_scope,
        is_postgres_entry_transaction,
    )

    _enabled()
    caller = get_default_context()
    outbox_id = None
    async with entry_write_scope(workspace_id) as graph:
        if await owner_subject(workspace_id) != user_id:
            raise InsufficientPermissionsError(
                message="Only the workspace owner can choose its voice model"
            )
        workspace = await configuration_record(workspace_id, create=True)
        if await owner_subject(workspace_id) != user_id:
            raise InsufficientPermissionsError(
                message="Workspace ownership changed. Reload before saving."
            )
        if workspace is None or workspace.voice_settings_revision != body.revision:
            raise ResourceConflictError(
                message="Voice settings changed. Reload before saving."
            )
        if body.mode == "provider":
            connection = await owned_connection(user_id, body.connection_id)
            if connection.provider != "openai":
                raise ValueError(
                    "Choose a saved OpenAI provider for voice transcription"
                )
        values = body.model_dump(exclude={"revision"})
        values["model"] = body.model.strip()
        if body.mode != "provider":
            values.update(connection_id="", model="")
        revision = body.revision + 1
        match = (
            {"context.voice_settings_revision": body.revision}
            if body.revision
            else {
                "$or": [
                    {"context.voice_settings_revision": 0},
                    {"context.voice_settings_revision": {"$exists": False}},
                ]
            }
        )
        changed = await graph.database.find_one_and_update(
            "node",
            {"id": workspace.id, "entity": "WorkspaceModelConfiguration", **match},
            {
                "$set": {
                    "context.voice_settings": values,
                    "context.voice_settings_revision": revision,
                }
            },
        )
        if changed is None:
            raise ResourceConflictError(
                message="Voice settings changed. Reload before saving."
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
                "voice_mode": body.mode,
                "voice_model": values["model"],
                "voice_settings_revision": revision,
            },
        }
        if is_postgres_entry_transaction(graph.database):
            outbox_id = await insert_revision_event(
                transaction=graph.database,
                workspace_id=workspace_id,
                namespace="voice-config-v1",
                subject_id=workspace_id,
                revision=revision,
                event=event,
            )
    if outbox_id and graph is not caller:
        try:
            await deliver_operation_event(outbox_id=outbox_id)
        except Exception:
            import logging

            logging.getLogger(__name__).exception(
                "Voice configuration audit awaits recovery"
            )
    elif not outbox_id:
        from app.services.change_event import emit_change_event

        await emit_change_event(**event)
    return await describe(user_id, workspace_id)


async def workspace_override(workspace_id: str):
    """Resolve current owner/key each use; never copy keys into workspace state."""
    workspace = await configuration_record(workspace_id)
    values = workspace.voice_settings if workspace else {}
    mode = values.get("mode", "inherit")
    if mode == "inherit":
        return False, None
    if mode == "disabled":
        return True, None
    from app.services.credential_crypto import decrypt_secret_from_storage
    from app.services.model_credential_resolver import SpeechCredential

    try:
        owner = await owner_subject(workspace_id)
        connection = await owned_connection(owner, values.get("connection_id", ""))
        if connection.provider != "openai" or not values.get("model", "").strip():
            return True, None
        key = decrypt_secret_from_storage(connection.api_key_enc, aad=owner)
        if not key:
            return True, None
        return True, SpeechCredential(
            provider="openai", model=values["model"], api_key=key, source="byok"
        )
    except (ValueError, RuntimeError):
        return True, None
