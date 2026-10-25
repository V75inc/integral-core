"""Workspace-owned search model selection with transactional rebuild intent.

I-RET-06/07: configuration, durable rebuild and audit intent commit together;
remote vectors live in separate spaces. Keys never enter jobs or profiles.
I-GRAPH-02: rebuild cursors are scalar work records, not graph participants.
"""

from __future__ import annotations

import logging
import time
import uuid
from dataclasses import asdict, replace

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

from .index_definition import EmbeddingProfile, digest, embedding_profile

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
    """Pin the serving space, checking current owner, consent and host policy."""
    try:
        workspace = await configuration_record(workspace_id)
        if workspace is None:
            # Deleted sources and legacy local jobs may lack a workspace.
            return embedding_profile()
        saved = workspace.embedding_serving_settings
        if workspace.embedding_serving_profile is not None:
            from app.config import settings

            if settings.INTEGRAL_AGENT_KEY_MODE != "platform_only":
                # A pinned space remains readable lexically after credential
                # loss. Encoding still requires fresh explicit authority below.
                return EmbeddingProfile(**workspace.embedding_serving_profile)
        choice = EmbeddingModelChoice.model_validate(
            workspace.embedding_settings if saved is None else saved
        )
        return await selected_profile(workspace_id, _effective_choice(choice))
    except ValueError:
        from .profile_encoder import WorkspaceEmbeddingUnavailable

        raise WorkspaceEmbeddingUnavailable(
            "The workspace search model needs an update in AI Models"
        ) from None


async def indexing_profiles(workspace_id: str):
    """Return at most two current projection spaces; egress is checked separately."""
    from .profile_encoder import WorkspaceEmbeddingUnavailable

    workspace = await configuration_record(workspace_id)
    profiles = {}
    try:
        serving = await resolve_workspace_embedding_profile(workspace_id)
        profiles[serving.id] = serving
    except WorkspaceEmbeddingUnavailable:
        pass
    if workspace is not None:
        choice = EmbeddingModelChoice.model_validate(workspace.embedding_settings or {})
        try:
            pending = await selected_profile(workspace_id, _effective_choice(choice))
            profiles[pending.id] = pending
        except ValueError:
            pass
    if not profiles:
        raise WorkspaceEmbeddingUnavailable(
            "The workspace search model needs an update in AI Models"
        )
    return list(profiles.values())


async def encoding_authorized(profile) -> bool:
    """A readable old projection is never authority to keep funding its model."""
    workspace = (
        await configuration_record(profile.workspace_id)
        if profile.workspace_id
        else None
    )
    if workspace is None:
        return not profile.provider and profile.id == embedding_profile().id
    choices = [EmbeddingModelChoice.model_validate(workspace.embedding_settings or {})]
    prior = workspace.embedding_serving_settings
    if prior is not None:
        old = EmbeddingModelChoice.model_validate(prior)
        if old.mode == "deployment" or workspace.embedding_continue_previous_provider:
            choices.append(old)
    for choice in choices:
        try:
            if (
                await selected_profile(
                    workspace.workspace_id, _effective_choice(choice)
                )
            ).id == profile.id:
                return True
        except ValueError:
            continue
    return False


async def job_profile(workspace_id: str, profile_id: str):
    """Resolve a current projection space, without granting provider egress."""
    return next(
        (p for p in await indexing_profiles(workspace_id) if p.id == profile_id), None
    )


async def describe(user_id: str, workspace_id: str) -> dict:
    """Describe effective search configuration without disclosing private providers."""
    from app.config import settings
    from app.runtime.composition import runtime

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
        profile = await resolve_workspace_embedding_profile(workspace_id)
    except (ValueError, RuntimeError):
        error = "The embedding model is unavailable. Choose a working provider or restore the local default."
    available = bool(profile and await encoding_authorized(profile))
    if profile and not available:
        error = "Semantic search is temporarily unavailable. Existing keyword search remains available while the new model is prepared."
    pending = None
    try:
        pending = await selected_profile(workspace_id, _effective_choice(choice))
    except ValueError:
        pass
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
        "processing_location": (
            "provider"
            if profile and profile.provider
            else "device" if runtime().preset == "local" else "server"
        ),
        "dimensions": profile.dimensions if profile else choice.dimensions,
        "profile_id": profile.id if profile else None,
        "rebuild_status": rows[0]["context"]["status"] if rows else "",
        "rebuild_phase": rows[0]["context"].get("phase", "enumerating") if rows else "",
        "error": error,
        "serving_available": available,
        "pending_model": (
            pending.model
            if pending and (not profile or pending.id != profile.id)
            else ""
        ),
        "pending_profile_id": (
            pending.id
            if pending and (not profile or pending.id != profile.id)
            else None
        ),
        "enumerated": (
            (int(rows[0]["context"].get("enumerated", 0)) if rows else 0)
            if editable
            else None
        ),
        "verified": (
            (int(rows[0]["context"].get("verified", 0)) if rows else 0)
            if editable
            else None
        ),
        "rebuild_error": (
            "Search preparation will retry. Check the selected model if this continues."
            if editable and rows and rows[0]["context"].get("error")
            else ""
        ),
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
    choice = EmbeddingModelChoice.model_validate(
        body.model_dump(exclude={"revision", "continue_previous_provider"})
    )
    if choice.mode == "deployment":
        choice = EmbeddingModelChoice()
    async with entry_write_scope(workspace_id) as graph:
        from .migration_barrier import barrier

        if await owner_subject(workspace_id) != user_id:
            raise InsufficientPermissionsError(
                message="Only the workspace owner can choose its embedding model"
            )
        workspace = await configuration_record(workspace_id, create=True)
        if workspace is None:
            raise ResourceNotFoundError(message="Workspace is unavailable")
        await barrier(workspace_id, lock=True)
        if await owner_subject(workspace_id) != user_id:
            raise InsufficientPermissionsError(
                message="Workspace ownership changed. Reload before saving."
            )
        if workspace.embedding_settings_revision != body.revision:
            raise ResourceConflictError(
                message="Embedding settings changed. Reload before saving."
            )
        profile = await selected_profile(workspace_id, choice)
        try:
            serving = await resolve_workspace_embedding_profile(workspace_id)
        except (ValueError, RuntimeError):
            serving = None
        serving_choice = (
            workspace.embedding_settings
            if workspace.embedding_serving_settings is None
            else workspace.embedding_serving_settings
        )
        before = workspace.embedding_settings or {}
        if (
            EmbeddingModelChoice.model_validate(before) == choice
            and workspace.embedding_continue_previous_provider
            == body.continue_previous_provider
        ):
            existing_build = await graph.database.get(
                "object",
                "o.WorkspaceEmbeddingReindexRequest."
                + digest([workspace_id, body.revision]),
            )
            if (serving and serving.id == profile.id) or (
                existing_build
                and existing_build["context"].get("profile_id") == profile.id
                and existing_build["context"].get("status")
                in {"pending", "working", "queued"}
            ):
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
                    "context.embedding_serving_settings": serving_choice or {},
                    "context.embedding_serving_profile": (
                        asdict(serving) if serving else None
                    ),
                    "context.embedding_continue_previous_provider": body.continue_previous_provider,
                }
            },
        )
        if changed is None:
            raise ResourceConflictError(
                message="Embedding settings changed. Reload before saving."
            )
        await graph.database.find_one_and_update(
            "object",
            {
                "id": "o.WorkspaceEmbeddingReindexRequest."
                + digest([workspace_id, body.revision]),
                "context.status": {"$in": ["pending", "working", "queued"]},
            },
            {"$set": {"context.status": "superseded"}},
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
                    "enumerated": 0,
                    "verified": 0,
                    "phase": "enumerating",
                    "protocol_version": 2,
                    "stack": [],
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


async def cancel(user_id: str, workspace_id: str, revision: int):
    """Cancel a pending selection through the same reviewed revision contract."""
    from app.services.entry_write_scope import entry_write_scope

    from .migration_barrier import barrier

    async with entry_write_scope(workspace_id):
        if await owner_subject(workspace_id) != user_id:
            raise InsufficientPermissionsError(
                message="Only the workspace owner can cancel this change"
            )
        config = await configuration_record(workspace_id, create=True)
        await barrier(workspace_id, lock=True)
        if config is None or config.embedding_settings_revision != revision:
            raise ResourceConflictError(
                message="Embedding settings changed. Reload before cancelling."
            )
        previous = config.embedding_serving_settings
        if previous is None or previous == config.embedding_settings:
            raise ValueError("There is no pending embedding model change")
        result = await save(
            user_id, workspace_id, EmbeddingModelWrite(**previous, revision=revision)
        )
        # save() restores the audited, revision-bound choice. The serving
        # projection already exists: neither the cancelled selection nor a
        # redundant rebuild of the restored selection should be scheduled.
        for request_revision in (revision, result["revision"]):
            await get_default_context().database.find_one_and_update(
                "object",
                {
                    "id": "o.WorkspaceEmbeddingReindexRequest."
                    + digest([workspace_id, request_revision])
                },
                {"$set": {"context.status": "cancelled", "context.phase": "cancelled"}},
            )
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
