"""Owner-managed voice model references, separate from chat profile selection."""

from typing import get_args

from jvspatial.core.context import get_default_context

from app.api.errors import InsufficientPermissionsError, ResourceConflictError
from app.schemas.voice_configuration import (
    RealtimeVoiceModel,
    RealtimeVoiceModelWrite,
    VoiceModelWrite,
)
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

    realtime = isinstance(body, RealtimeVoiceModelWrite)
    field = "realtime_voice_settings" if realtime else "voice_settings"
    revision_field = f"{field}_revision"
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
        if workspace is None or getattr(workspace, revision_field) != body.revision:
            raise ResourceConflictError(
                message="Voice settings changed. Reload before saving."
            )
        if body.mode == "provider":
            connection = await owned_connection(user_id, body.connection_id)
            if realtime:
                _require_realtime_connection(connection)
            elif connection.provider != "openai":
                raise ValueError(
                    "Choose a saved OpenAI provider for voice transcription"
                )
        values = body.model_dump(exclude={"revision"})
        values["model"] = body.model.strip()
        if body.mode != "provider":
            values.update(connection_id="", model="")
        revision = body.revision + 1
        match = (
            {f"context.{revision_field}": body.revision}
            if body.revision
            else {
                "$or": [
                    {f"context.{revision_field}": 0},
                    {f"context.{revision_field}": {"$exists": False}},
                ]
            }
        )
        changed = await graph.database.find_one_and_update(
            "node",
            {"id": workspace.id, "entity": "WorkspaceModelConfiguration", **match},
            {
                "$set": {
                    f"context.{field}": values,
                    f"context.{revision_field}": revision,
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
                ("realtime_voice_mode" if realtime else "voice_mode"): body.mode,
                ("realtime_voice_model" if realtime else "voice_model"): values[
                    "model"
                ],
                revision_field: revision,
            },
        }
        if is_postgres_entry_transaction(graph.database):
            outbox_id = await insert_revision_event(
                transaction=graph.database,
                workspace_id=workspace_id,
                namespace="realtime-voice-config-v1" if realtime else "voice-config-v1",
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
    describe_config = describe_realtime if realtime else describe
    return await describe_config(user_id, workspace_id)


def _require_realtime_connection(connection):
    if connection.provider != "openai" or (connection.base_url or "").rstrip(
        "/"
    ) not in ("", "https://api.openai.com/v1", "https://api.openai.com"):
        raise ValueError("Choose a saved provider using the official OpenAI API")


async def resolve_realtime_credential(workspace_id: str):
    """Resolve the workspace's configured live voice credential."""
    from app.config import settings
    from app.services.credential_crypto import decrypt_secret_from_storage
    from app.services.model_credential_resolver import SpeechCredential

    if settings.INTEGRAL_AGENT_KEY_MODE == "platform_only":
        return None
    config = await configuration_record(workspace_id)
    values = config.realtime_voice_settings if config else {}
    if (
        values.get("mode") != "provider"
        or not values.get("model")
        or (values["model"] not in get_args(RealtimeVoiceModel))
    ):
        return None
    try:
        owner = await owner_subject(workspace_id)
        connection = await owned_connection(owner, values.get("connection_id", ""))
        _require_realtime_connection(connection)
        key = decrypt_secret_from_storage(connection.api_key_enc, aad=owner)
        if key:
            return SpeechCredential(
                provider="openai", model=values["model"], api_key=key, source="byok"
            )
    except (ValueError, RuntimeError):
        pass
    return None


async def describe_realtime(user_id: str, workspace_id: str):
    """Describe live voice configuration without revealing its credential."""
    from app.config import settings

    if await can_access_workspace(user_id, workspace_id) == "none":
        raise InsufficientPermissionsError(message="Workspace is unavailable")
    config = await configuration_record(workspace_id)
    editable = (
        await owner_subject(workspace_id) == user_id
        and settings.INTEGRAL_AGENT_KEY_MODE != "platform_only"
    )
    selection = (config.realtime_voice_settings if config else {}) or {
        "mode": "disabled",
        "connection_id": "",
        "model": "",
    }
    effective = await resolve_realtime_credential(workspace_id)
    return {
        "editable": editable,
        "revision": config.realtime_voice_settings_revision if config else 0,
        "configuration": selection if editable else {"mode": selection["mode"]},
        "effective_model": effective.model if effective else "",
    }


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
