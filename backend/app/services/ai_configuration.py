"""Same-user connection and model-profile records, composing the legacy route."""

from __future__ import annotations

import hashlib
import json
from typing import Any

from app.config import settings
from app.models.credentials import (
    UserModelConnection,
    UserModelCredential,
    UserModelProfile,
)
from app.schemas.ai_configuration import ConnectionWrite, ProfileWrite
from app.services.credential_crypto import (
    decrypt_secret_from_storage,
    encrypt_secret_for_storage,
)
from app.services.model_credentials import (
    compute_key_fingerprint,
    get_credential_for_user,
    validate_provider_api_key,
)
from app.services.model_provider_endpoints import normalize_custom_base_url
from app.services.model_settings import validate_model_settings
from app.utils.time import utc_now_iso


def _enabled() -> None:
    if settings.INTEGRAL_AGENT_KEY_MODE == "platform_only":
        raise ValueError(
            "This deployment uses shared model configuration; personal BYOK changes are disabled"
        )


async def _find(cls, query: dict[str, Any], *, limit: int):
    from jvspatial.core.context import get_default_context

    context = get_default_context()
    await context.ensure_indexes(cls)
    return await context.find(cls, query, limit=limit)


async def _save_revision(
    item, previous: str | None, *, only: set[str] | None = None
) -> None:
    """CAS private scalar records; a stale editor must never restore old keys."""
    if previous is None:
        await item.save()
        return
    from jvspatial.core.context import get_default_context

    context = get_default_context()
    from jvspatial.core import Object

    values = {
        key: getattr(item, key)
        for key in type(item).model_fields
        if key not in Object.model_fields
    }
    if only is not None:
        values = {key: value for key, value in values.items() if key in only}
    changed = await context.database.find_one_and_update(
        "object",
        {
            "id": item.id,
            "context.user_id": item.user_id,
            "context.updated_at": previous,
        },
        {"$set": {f"context.{key}": value for key, value in values.items()}},
    )
    if changed is None:
        raise ValueError("Configuration changed while editing. Reload and try again.")


async def _owned(cls, user_id: str, item_id: str):
    item = await cls.get(item_id)
    if item is None or not isinstance(item, cls) or item.user_id != user_id:
        raise ValueError("Configuration item is unavailable")
    return item


def _connection_view(item) -> dict[str, Any]:
    return {
        key: getattr(item, key)
        for key in (
            "id",
            "name",
            "provider",
            "base_url",
            "key_fingerprint",
            "validated_at",
            "updated_at",
            "last_test_ok",
            "last_tested_at",
        )
    }


def _profile_view(item) -> dict[str, Any]:
    return {
        key: getattr(item, key)
        for key in (
            "id",
            "name",
            "connection_id",
            "model",
            "generation_settings",
            "updated_at",
        )
    }


async def _legacy_profile(user_id: str, record) -> str:
    """Idempotently snapshot legacy state without changing execution or secrets.

    Object records have no graph/cascade role (I-GRAPH-02). Do not rewrite the
    legacy record during a GET: concurrent activation must not be overwritten.
    """
    identity = json.dumps(
        [
            record.id,
            record.provider,
            record.base_url,
            record.api_key_enc,
            record.model,
            record.generation_settings,
        ],
        sort_keys=True,
    )
    key = hashlib.sha256(identity.encode()).hexdigest()
    connection_id = f"o.UserModelConnection.legacy-{key}"
    profile_id = f"o.UserModelProfile.legacy-{key}"
    await UserModelConnection.create_if_absent(
        id=connection_id,
        user_id=user_id,
        name=f"{record.provider} connection",
        provider=record.provider,
        base_url=record.base_url,
        api_key_enc=record.api_key_enc,
        key_fingerprint=record.key_fingerprint,
        validated_at=record.validated_at or "",
        updated_at=record.updated_at or "",
    )
    await UserModelProfile.create_if_absent(
        id=profile_id,
        user_id=user_id,
        connection_id=connection_id,
        name="Current model",
        model=record.model,
        generation_settings=record.generation_settings,
        updated_at=record.updated_at or "",
    )
    return profile_id


async def configuration(user_id: str) -> dict[str, Any]:
    """List private connections, profiles, default selection, and host ceilings."""
    record = await get_credential_for_user(user_id)
    default_id = ""
    if record and record.is_active:
        default_id = record.active_profile_id or await _legacy_profile(user_id, record)
    connections = await _find(
        UserModelConnection, {"context.user_id": user_id}, limit=101
    )
    profiles = await _find(UserModelProfile, {"context.user_id": user_id}, limit=101)
    return {
        "connections": [_connection_view(c) for c in connections],
        "profiles": [_profile_view(p) for p in profiles],
        "default_profile_id": default_id,
        "key_policy": settings.INTEGRAL_AGENT_KEY_MODE,
        "harness_limits": {
            "request_limit": settings.INTEGRAL_NATIVE_TURN_REQUEST_LIMIT,
            "token_limit": settings.INTEGRAL_NATIVE_TURN_TOKEN_LIMIT,
            "run_seconds": settings.INTEGRAL_HARNESS_CHAT_TURN_TIMEOUT_SECONDS,
            "request_timeout": settings.INTEGRAL_NATIVE_MODEL_REQUEST_TIMEOUT_SECONDS,
            "idle_timeout": settings.INTEGRAL_NATIVE_MODEL_IDLE_TIMEOUT_SECONDS,
        },
    }


async def save_connection(user_id: str, body: ConnectionWrite) -> dict[str, Any]:
    """Validate and save an owned provider connection without exposing its secret."""
    _enabled()
    existing = await _owned(UserModelConnection, user_id, body.id) if body.id else None
    if (
        not existing
        and len(
            await _find(UserModelConnection, {"context.user_id": user_id}, limit=101)
        )
        >= 100
    ):
        raise ValueError("Remove an unused connection before adding another")
    base = (
        normalize_custom_base_url(body.base_url)
        if body.provider == "custom_openai"
        else ""
    )
    if body.provider != "custom_openai" and body.base_url:
        raise ValueError("Custom endpoints belong to an OpenAI-compatible connection")
    if body.without_key and body.provider not in {"custom_openai", "ollama_local"}:
        raise ValueError("This provider requires an API key")
    if existing and (existing.provider != body.provider or existing.base_url != base):
        raise ValueError("Add a new connection to change its provider or endpoint")
    previous = existing.updated_at if existing else None
    key = (body.api_key or "").strip()
    if body.provider == "ollama_local" and key:
        raise ValueError("Local Ollama does not use an API key")
    if not key and not body.without_key and body.provider != "ollama_local":
        if (
            existing
            and existing.provider == body.provider
            and existing.base_url == base
        ):
            key = (
                decrypt_secret_from_storage(existing.api_key_enc, aad=user_id)
                if existing.api_key_enc
                else ""
            )
        if not key:
            raise ValueError(
                "Supply a key, or explicitly choose a keyless local/custom endpoint"
            )
    if key and body.without_key:
        raise ValueError("Choose either a key or a keyless connection")
    valid, message = await validate_provider_api_key(body.provider, key, base or None)
    if not valid:
        raise ValueError(message)
    item = existing or UserModelConnection(user_id=user_id)
    # Rebinding an existing connection could send every referencing profile to
    # another endpoint. Require a distinct connection rather than moving keys.
    if existing and (item.provider != body.provider or item.base_url != base):
        raise ValueError("Add a new connection to change its provider or endpoint")
    item.name, item.provider, item.base_url = body.name.strip(), body.provider, base
    if not item.name:
        raise ValueError("Connection name cannot be blank")
    item.api_key_enc = encrypt_secret_for_storage(key, aad=user_id) if key else ""
    item.key_fingerprint = compute_key_fingerprint(key)
    item.validated_at = item.updated_at = item.last_tested_at = utc_now_iso()
    item.last_test_ok = True
    await _save_revision(item, previous)
    record = await get_credential_for_user(user_id)
    if record and record.is_active:
        default_id = record.active_profile_id or await _legacy_profile(user_id, record)
        profile = await _owned(UserModelProfile, user_id, default_id)
        if profile.connection_id == item.id:
            await activate_profile(user_id, profile.id, expected_profile_id=default_id)
    return _connection_view(item)


async def test_connection(user_id: str, item_id: str) -> dict[str, Any]:
    """Test the owned saved connection using its server-side secret."""
    connection = await _owned(UserModelConnection, user_id, item_id)
    previous = connection.updated_at
    key = (
        decrypt_secret_from_storage(connection.api_key_enc, aad=user_id)
        if connection.api_key_enc
        else ""
    )
    if connection.api_key_enc and not key:
        raise ValueError("The saved key could not be read; replace it")
    valid, message = await validate_provider_api_key(
        connection.provider, key, connection.base_url or None
    )
    connection.last_test_ok = valid
    connection.last_tested_at = utc_now_iso()
    if valid:
        connection.validated_at = connection.last_tested_at
    await _save_revision(
        connection, previous, only={"last_test_ok", "last_tested_at", "validated_at"}
    )
    return {"valid": valid, "message": message}


async def save_profile(user_id: str, body: ProfileWrite) -> dict[str, Any]:
    """Validate and save model options against the owned provider connection."""
    _enabled()
    connection = await _owned(UserModelConnection, user_id, body.connection_id)
    configured = validate_model_settings(
        connection.provider,
        body.model.strip(),
        body.generation_settings.model_dump(exclude_none=True),
    )
    profile = (
        await _owned(UserModelProfile, user_id, body.id)
        if body.id
        else UserModelProfile(user_id=user_id)
    )
    if (
        not body.id
        and len(await _find(UserModelProfile, {"context.user_id": user_id}, limit=101))
        >= 100
    ):
        raise ValueError("Remove an unused profile before adding another")
    if not body.name.strip() or not body.model.strip():
        raise ValueError("Profile name and model ID cannot be blank")
    record = await get_credential_for_user(user_id)
    default_id = (
        (record.active_profile_id or await _legacy_profile(user_id, record))
        if record and record.is_active
        else ""
    )
    previous = profile.updated_at if body.id else None
    profile.name, profile.connection_id, profile.model = (
        body.name.strip(),
        connection.id,
        body.model.strip(),
    )
    profile.generation_settings, profile.updated_at = configured, utc_now_iso()
    await _save_revision(profile, previous)
    record = await get_credential_for_user(user_id)
    if record and record.is_active and default_id == profile.id:
        await activate_profile(user_id, profile.id, expected_profile_id=default_id)
    return _profile_view(profile)


async def activate_profile(
    user_id: str, item_id: str, *, expected_profile_id: str | None = None
) -> None:
    """Select an owned default profile while preserving voice credentials."""
    _enabled()
    profile = await _owned(UserModelProfile, user_id, item_id)
    connection = await _owned(UserModelConnection, user_id, profile.connection_id)
    configured = validate_model_settings(
        connection.provider, profile.model, profile.generation_settings
    )
    if connection.api_key_enc and not decrypt_secret_from_storage(
        connection.api_key_enc, aad=user_id
    ):
        raise ValueError("The saved key could not be read; replace it")
    record = await get_credential_for_user(user_id)
    previous = record.updated_at if record else None
    if expected_profile_id is not None:
        if not record or not record.is_active:
            return
        current = record.active_profile_id or await _legacy_profile(user_id, record)
        if current != expected_profile_id:
            return
    record = record or UserModelCredential(user_id=user_id)
    if record.speech_model and not record.speech_api_key_enc:
        record.speech_provider = record.speech_provider or record.provider
        record.speech_api_key_enc = record.api_key_enc
        record.speech_key_fingerprint = record.key_fingerprint
    record.provider, record.base_url, record.model = (
        connection.provider,
        connection.base_url,
        profile.model,
    )
    record.api_key_enc, record.key_fingerprint = (
        connection.api_key_enc,
        connection.key_fingerprint,
    )
    record.generation_settings, record.active_profile_id = configured, profile.id
    record.is_active, record.validated_at = True, connection.validated_at or None
    record.updated_at = utc_now_iso()
    if not record.created_at:
        record.created_at = record.updated_at
    await _save_revision(
        record,
        previous,
        only={
            "provider",
            "base_url",
            "model",
            "api_key_enc",
            "key_fingerprint",
            "generation_settings",
            "active_profile_id",
            "is_active",
            "validated_at",
            "updated_at",
            "created_at",
            "speech_provider",
            "speech_api_key_enc",
            "speech_key_fingerprint",
        },
    )


async def remove_item(user_id: str, item_id: str, *, connection: bool) -> None:
    """Refuse removal of referenced private configuration."""
    _enabled()
    cls = UserModelConnection if connection else UserModelProfile
    item = await _owned(cls, user_id, item_id)
    if connection:
        profiles = await _find(
            UserModelProfile,
            {"context.user_id": user_id, "context.connection_id": item.id},
            limit=1,
        )
        if profiles:
            raise ValueError(
                "Remove or reassign the profiles using this connection first"
            )
    else:
        record = await get_credential_for_user(user_id)
        if (
            record
            and record.is_active
            and (
                record.active_profile_id == item.id
                or (
                    not record.active_profile_id
                    and item.id == await _legacy_profile(user_id, record)
                )
            )
        ):
            raise ValueError(
                "Choose another default model before removing this profile"
            )
        from app.models.nodes import ChatThread

        if await _find(
            ChatThread,
            {"context.user_id": user_id, "context.model_profile_id": item.id},
            limit=1,
        ):
            raise ValueError(
                "Reset conversation overrides using this profile before removing it"
            )
    await item.delete()


async def profile_credential(user_id: str, profile_id: str, record):
    """Resolve an owned profile without mutating the user's saved default."""
    profile = await _owned(UserModelProfile, user_id, profile_id)
    connection = await _owned(UserModelConnection, user_id, profile.connection_id)
    configured = validate_model_settings(
        connection.provider, profile.model, profile.generation_settings
    )
    return record.model_copy(
        update={
            "provider": connection.provider,
            "base_url": connection.base_url,
            "model": profile.model,
            "generation_settings": configured,
            "api_key_enc": connection.api_key_enc,
            "key_fingerprint": connection.key_fingerprint,
            "updated_at": profile.updated_at + ":" + connection.updated_at,
            "active_profile_id": profile.id,
        }
    )


async def conversation_model(
    user_id: str, thread_id: str, profile_id: str | None = None
) -> dict[str, Any]:
    """Read or change an owned idle conversation model selection."""
    from app.models.nodes import ChatThread
    from app.services.permissions import get_user_node
    from app.services.workspace_permissions import (
        can_access_workspace,
        get_workspace_owner_user_id,
    )

    thread = await ChatThread.get(thread_id)
    if (
        thread is None
        or thread.user_id != user_id
        or await can_access_workspace(user_id, thread.workspace_id) == "none"
    ):
        raise ValueError("Conversation is unavailable")
    owner_id = await get_workspace_owner_user_id(thread.workspace_id)
    owner = await get_user_node(owner_id) if owner_id else None
    editable = bool(
        owner
        and owner.user_id == user_id
        and settings.INTEGRAL_AGENT_KEY_MODE != "platform_only"
    )
    if profile_id is not None:
        _enabled()
        if not editable:
            raise ValueError(
                "This conversation uses its workspace owner's model settings"
            )
        if profile_id:
            record = await get_credential_for_user(user_id)
            if record is None or not record.is_active:
                raise ValueError(
                    "Choose a default model before setting a conversation override"
                )
            await profile_credential(user_id, profile_id, record)
        # Field-only CAS: never overwrite an admission pointer from a stale
        # hydrated thread while another request starts a turn.
        from jvspatial.core.context import get_default_context

        if thread.active_work_item_id:
            raise ValueError(
                "Wait for the current turn to finish before switching models"
            )
        changed = await get_default_context().database.find_one_and_update(
            "node",
            {
                "id": thread.id,
                "entity": "ChatThread",
                "context.user_id": user_id,
                "$or": [
                    {"context.active_work_item_id": ""},
                    {"context.active_work_item_id": {"$exists": False}},
                ],
            },
            {"$set": {"context.model_profile_id": profile_id}},
        )
        if changed is None:
            raise ValueError(
                "The conversation changed; wait for its current turn to finish"
            )
        thread.model_profile_id = profile_id
    return {
        "profile_id": thread.model_profile_id if editable else "",
        "editable": editable,
    }
