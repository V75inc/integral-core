"""Workspace-owned search model selection with transactional rebuild intent.

I-RET-06/07: configuration, durable rebuild and audit intent commit together;
remote vectors live in separate spaces. Keys never enter jobs or profiles.
I-GRAPH-02: rebuild cursors are scalar work records, not graph participants.
"""

from __future__ import annotations

import logging
import time
import uuid
from dataclasses import replace

from jvspatial.core.context import get_default_context

from app.api.errors import (
    InsufficientPermissionsError,
    ResourceConflictError,
    ResourceNotFoundError,
)
from app.models.credentials import UserModelConnection
from app.schemas.embedding_configuration import (
    EmbeddingModelChoice,
    EmbeddingModelWrite,
)
from app.services.ai_configuration import _enabled
from app.services.permissions import get_user_node
from app.services.workspace_model_configuration import configuration_record
from app.services.workspace_permissions import (
    can_access_workspace,
    get_workspace_owner_user_id,
)

from .index_definition import digest, embedding_profile

REBUILD = "WorkspaceEmbeddingReindexRequest"
PROVIDERS = {"openai", "openrouter", "ollama", "ollama_local", "custom_openai"}


def overrides_supported():
    """Report support for isolated provider vector spaces."""
    from . import get_embedding_store
    from .pgvector_driver import PgVectorEmbeddingStore

    return isinstance(get_embedding_store(), PgVectorEmbeddingStore)


async def owner_subject(workspace_id: str) -> str:
    """Resolve the current canonical owner to its authentication identity."""
    from app.middleware.permissions_cache import isolated_permissions_cache

    with isolated_permissions_cache():
        owner_id = await get_workspace_owner_user_id(workspace_id)
        owner = await get_user_node(owner_id) if owner_id else None
        return owner.user_id if owner else ""


async def owned_connection(owner: str, identity: str):
    """Read a fresh same-owner provider without ambient credential fallback."""
    # Fresh reads are required after key removal/rotation; never resolve ambient keys.
    context = get_default_context()
    row = await context.database.get("object", identity)
    connection = (
        await context._deserialize_entity(UserModelConnection, row)
        if row and row.get("entity") == "UserModelConnection"
        else None
    )
    if not owner or connection is None or connection.user_id != owner:
        raise ValueError(
            "The embedding provider is unavailable; choose an owned provider"
        )
    if connection.provider not in PROVIDERS:
        raise ValueError("This provider does not offer a supported embedding API")
    if (
        connection.provider not in {"ollama_local", "custom_openai"}
        and not connection.api_key_enc
    ):
        raise ValueError("The embedding provider needs an API key")
    return connection


async def selected_profile(workspace_id: str, choice: EmbeddingModelChoice):
    """Construct a provider-specific immutable search-space identity."""
    profile = embedding_profile()
    if choice.mode == "deployment":
        return profile
    _enabled()
    if not overrides_supported():
        raise ValueError("Provider embeddings require the PostgreSQL passage index")
    owner = await owner_subject(workspace_id)
    connection = await owned_connection(owner, choice.connection_id)
    return replace(
        profile,
        model=choice.model.strip(),
        dimensions=choice.dimensions,
        provider=connection.provider,
        connection_id=connection.id,
        owner_id=owner,
        workspace_id=workspace_id,
        format_version=3,
        revision=digest(
            [
                connection.provider,
                connection.base_url,
                choice.model.strip(),
                choice.dimensions,
            ]
        ),
    )


def _effective_choice(choice: EmbeddingModelChoice) -> EmbeddingModelChoice:
    from app.config import settings

    # Host policy can suspend a personal override without disabling the
    # deployment's local search or rewriting the owner's saved selection.
    return (
        EmbeddingModelChoice()
        if settings.INTEGRAL_AGENT_KEY_MODE == "platform_only"
        else choice
    )


async def resolve_workspace_embedding_profile(workspace_id: str):
    """Resolve the active workspace search space and fail closed on bad overrides."""
    try:
        workspace = await configuration_record(workspace_id)
        if workspace is None:
            # Deleted sources and legacy local jobs may lack a workspace.
            return embedding_profile()
        choice = EmbeddingModelChoice.model_validate(workspace.embedding_settings or {})
        return await selected_profile(workspace_id, _effective_choice(choice))
    except ValueError:
        from .profile_encoder import WorkspaceEmbeddingUnavailable

        raise WorkspaceEmbeddingUnavailable(
            "The workspace search model needs an update in AI Models"
        ) from None


async def describe(user_id: str, workspace_id: str) -> dict:
    """Describe effective search configuration without disclosing private providers."""
    from app.config import settings

    if await can_access_workspace(user_id, workspace_id) == "none":
        raise InsufficientPermissionsError(message="Workspace is unavailable")
    workspace = await configuration_record(workspace_id)
    if workspace is None:
        raise ResourceNotFoundError(message="Workspace is unavailable")
    editable = (
        await owner_subject(workspace_id) == user_id
        and settings.INTEGRAL_AGENT_KEY_MODE != "platform_only"
    )
    choice = EmbeddingModelChoice.model_validate(workspace.embedding_settings or {})
    profile, error = None, ""
    try:
        profile = await selected_profile(workspace_id, _effective_choice(choice))
    except ValueError:
        error = "The embedding model is unavailable. Choose a working provider or restore the local default."
    rows = await get_default_context().database.find(
        "object",
        {
            "entity": REBUILD,
            "context.workspace_id": workspace_id,
            "context.revision": workspace.embedding_settings_revision,
        },
        limit=1,
    )
    return {
        "workspace_id": workspace_id,
        "editable": editable,
        "provider_overrides_supported": overrides_supported(),
        "revision": workspace.embedding_settings_revision,
        "configuration": choice.model_dump() if editable else {"mode": "deployment"},
        "effective_provider": profile.provider or "local" if profile else "unavailable",
        "effective_model": profile.model if profile else choice.model,
        "dimensions": profile.dimensions if profile else choice.dimensions,
        "profile_id": profile.id if profile else None,
        "rebuild_status": rows[0]["context"]["status"] if rows else "",
        "error": error,
    }


async def save(user_id: str, workspace_id: str, body: EmbeddingModelWrite):
    """Commit the owner choice, rebuild cursor and audit intent together."""
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
    choice = EmbeddingModelChoice.model_validate(body.model_dump(exclude={"revision"}))
    if choice.mode == "deployment":
        choice = EmbeddingModelChoice()
    async with entry_write_scope(workspace_id) as graph:
        if await owner_subject(workspace_id) != user_id:
            raise InsufficientPermissionsError(
                message="Only the workspace owner can choose its embedding model"
            )
        workspace = await configuration_record(workspace_id, create=True)
        if workspace is None:
            raise ResourceNotFoundError(message="Workspace is unavailable")
        if await owner_subject(workspace_id) != user_id:
            raise InsufficientPermissionsError(
                message="Workspace ownership changed. Reload before saving."
            )
        if workspace.embedding_settings_revision != body.revision:
            raise ResourceConflictError(
                message="Embedding settings changed. Reload before saving."
            )
        profile = await selected_profile(workspace_id, choice)
        before = workspace.embedding_settings or {}
        if EmbeddingModelChoice.model_validate(before) == choice:
            return {**await describe(user_id, workspace_id), "changed": False}
        revision = body.revision + 1
        revision_filter = (
            {"context.embedding_settings_revision": body.revision}
            if body.revision
            else {
                "$or": [
                    {"context.embedding_settings_revision": 0},
                    {"context.embedding_settings_revision": {"$exists": False}},
                ]
            }
        )
        changed = await graph.database.find_one_and_update(
            "node",
            {
                "id": workspace.id,
                "entity": "WorkspaceModelConfiguration",
                **revision_filter,
            },
            {
                "$set": {
                    "context.embedding_settings": choice.model_dump(),
                    "context.embedding_settings_revision": revision,
                }
            },
        )
        if changed is None:
            raise ResourceConflictError(
                message="Embedding settings changed. Reload before saving."
            )
        await graph.database.save(
            "object",
            {
                "id": "o.WorkspaceEmbeddingReindexRequest."
                + digest([workspace_id, revision]),
                "entity": REBUILD,
                "context": {
                    "workspace_id": workspace_id,
                    "requested_by": user_id,
                    "revision": revision,
                    "profile_id": profile.id,
                    "generation": uuid.uuid4().hex,
                    "status": "pending",
                    "after": "",
                    "queued": 0,
                    "requested_at": time.time(),
                    "due": time.time(),
                    "attempt": 0,
                },
            },
        )
        event = {
            "actor_kind": "human",
            "actor_id": user_id,
            "action": "workspace.update",
            "resource_type": "Workspace",
            "resource_id": workspace_id,
            "scope": f"workspace:{workspace_id}",
            "before": {"embedding_mode": before.get("mode", "deployment")},
            "after": {
                "embedding_mode": choice.mode,
                "embedding_model": profile.model,
                "embedding_settings_revision": revision,
            },
        }
        if is_postgres_entry_transaction(graph.database):
            outbox_id = await insert_revision_event(
                transaction=graph.database,
                workspace_id=workspace_id,
                namespace="embedding-config-v1",
                subject_id=workspace_id,
                revision=revision,
                event=event,
            )
    if outbox_id and graph is not caller:
        try:
            await deliver_operation_event(outbox_id=outbox_id)
        except Exception:
            logging.getLogger(__name__).exception(
                "Embedding configuration audit awaits recovery"
            )
    elif not outbox_id:
        from app.services.change_event import emit_change_event

        await emit_change_event(**event)
    return {**await describe(user_id, workspace_id), "changed": True}


async def test(user_id: str, workspace_id: str, body: EmbeddingModelChoice):
    """Probe a draft with a fixed synthetic phrase without activating it."""
    if await owner_subject(workspace_id) != user_id:
        raise InsufficientPermissionsError(
            message="Only the workspace owner can test its embedding model"
        )
    from .profile_encoder import encode_profile

    profile = await selected_profile(workspace_id, body)
    await encode_profile(
        ["Integral embedding configuration test"], profile, require_active=False
    )
    return {
        "valid": True,
        "dimensions": profile.dimensions,
        "message": "Embedding model test passed.",
    }
