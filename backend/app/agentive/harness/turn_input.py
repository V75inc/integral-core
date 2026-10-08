"""Encrypted, tenant-bound input capsules for durable native chat turns."""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timedelta, timezone

from pydantic import BaseModel, ConfigDict, Field

from app.models.harness_records import HarnessTurnInputRecord
from app.schemas.agentive.work import TERMINAL_WORK_STATUSES, ChatTurnExecutionContext
from app.services.credential_crypto import (
    CIPHER_PREFIX_V1,
    decrypt_secret_from_storage,
    encrypt_secret_for_storage,
)

_SCHEMA_VERSION = 1
_MAX_CIPHERTEXT_SOURCE_BYTES = 300_000


class HarnessTurnInputPayload(BaseModel):
    """Canonical encrypted capsule body; prompt content stays in ChatMessage."""

    schema_version: int = Field(default=_SCHEMA_VERSION, ge=1, le=1)
    principal_id: str = Field(min_length=1, max_length=255)
    workspace_id: str = Field(min_length=1, max_length=255)
    thread_id: str = Field(min_length=1, max_length=255)
    work_item_id: str = Field(min_length=1, max_length=255)
    accepted_message_id: str = Field(min_length=1, max_length=255)
    client_request_id: str = Field(min_length=1, max_length=128)
    execution_context: ChatTurnExecutionContext

    model_config = ConfigDict(extra="forbid", frozen=True)


def thread_scope_key(*, principal_id: str, workspace_id: str, thread_id: str) -> str:
    """Hash canonical owner/workspace/thread identity for private Object lookup."""
    if not principal_id or not workspace_id or not thread_id:
        raise ValueError("turn input scope identity is incomplete")
    identity = "\0".join((workspace_id, principal_id, thread_id))
    return hashlib.sha256(identity.encode("utf-8")).hexdigest()


def turn_input_record_id(*, scope_key: str, work_item_id: str) -> str:
    """Return a stable Object ID for one thread-scoped WorkItem input."""
    digest = hashlib.sha256(
        f"{scope_key}:turn-input:{work_item_id}".encode("utf-8")
    ).hexdigest()
    return f"o.HarnessTurnInputRecord.{digest}"


def _canonical_json(payload: HarnessTurnInputPayload) -> str:
    return json.dumps(
        payload.model_dump(mode="json"),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )


async def persist_turn_input_capsule(
    *,
    principal_id: str,
    workspace_id: str,
    thread_id: str,
    work_item_id: str,
    accepted_message_id: str,
    client_request_id: str,
    execution_context: ChatTurnExecutionContext,
    retention_days: int,
    now: datetime | None = None,
) -> dict[str, str]:
    """Persist/reuse the deterministic capsule in the caller's transaction.

    ``postgres_graph_transaction`` binds the default GraphContext to the
    transaction before this call. Callers must pass that transaction-scoped
    execution context and commit the Object with the matching WorkItem.
    """
    if retention_days < 1:
        raise ValueError("turn input retention must be at least one day")
    payload = HarnessTurnInputPayload(
        principal_id=principal_id,
        workspace_id=workspace_id,
        thread_id=thread_id,
        work_item_id=work_item_id,
        accepted_message_id=accepted_message_id,
        client_request_id=client_request_id,
        execution_context=execution_context,
    )
    encoded = _canonical_json(payload)
    encoded_bytes = encoded.encode("utf-8")
    if len(encoded_bytes) > _MAX_CIPHERTEXT_SOURCE_BYTES:
        raise ValueError("turn input capsule exceeds its storage size limit")
    digest = hashlib.sha256(encoded_bytes).hexdigest()
    scope_key = thread_scope_key(
        principal_id=principal_id, workspace_id=workspace_id, thread_id=thread_id
    )
    record_id = turn_input_record_id(scope_key=scope_key, work_item_id=work_item_id)
    try:
        ciphertext = encrypt_secret_for_storage(encoded, aad=record_id)
    except RuntimeError as exc:
        raise RuntimeError("native turn input requires storage encryption") from exc
    if not ciphertext.startswith(CIPHER_PREFIX_V1):
        raise RuntimeError("native turn input encryption returned an invalid format")

    created_at = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    record, created = await HarnessTurnInputRecord.create_if_absent(
        id=record_id,
        scope_key=scope_key,
        principal_id=principal_id,
        workspace_id=workspace_id,
        thread_id=thread_id,
        work_item_id=work_item_id,
        accepted_message_id=accepted_message_id,
        client_request_id=client_request_id,
        created_at=created_at.isoformat(),
        expires_at=(created_at + timedelta(days=retention_days)).isoformat(),
        schema_version=_SCHEMA_VERSION,
        payload_digest=digest,
        payload_ciphertext=ciphertext,
    )
    if not created:
        loaded = _decode_record(record, expected_scope_key=scope_key)
        if loaded != payload:
            raise RuntimeError("turn input capsule identity is already bound")
        # Retained schema-compatible JSON can omit newly added empty context
        # fields. Reuse the digest of the stored bytes, not a reserialization.
        digest = record.payload_digest
    return {"capsule_id": record_id, "capsule_digest": digest}


def _decode_record(
    record: HarnessTurnInputRecord, *, expected_scope_key: str
) -> HarnessTurnInputPayload:
    if (
        record.scope_key != expected_scope_key
        or record.schema_version != _SCHEMA_VERSION
        or not record.payload_ciphertext.startswith(CIPHER_PREFIX_V1)
    ):
        raise ValueError("turn input capsule storage scope or version mismatch")
    raw = decrypt_secret_from_storage(record.payload_ciphertext, aad=record.id)
    if not raw:
        raise ValueError("turn input capsule could not be authenticated")
    if hashlib.sha256(raw.encode("utf-8")).hexdigest() != record.payload_digest:
        raise ValueError("turn input capsule digest mismatch")
    payload = HarnessTurnInputPayload.model_validate_json(raw)
    if (
        payload.principal_id != record.principal_id
        or payload.workspace_id != record.workspace_id
        or payload.thread_id != record.thread_id
        or payload.work_item_id != record.work_item_id
        or payload.accepted_message_id != record.accepted_message_id
        or payload.client_request_id != record.client_request_id
    ):
        raise ValueError("turn input capsule identity mismatch")
    return payload


async def load_turn_input_capsule(
    *,
    capsule_id: str,
    expected_digest: str,
    principal_id: str,
    workspace_id: str,
    thread_id: str,
    work_item_id: str,
) -> HarnessTurnInputPayload:
    """Load and authenticate a capsule only within the exact execution scope."""
    scope_key = thread_scope_key(
        principal_id=principal_id, workspace_id=workspace_id, thread_id=thread_id
    )
    if capsule_id != turn_input_record_id(
        scope_key=scope_key, work_item_id=work_item_id
    ):
        raise ValueError("turn input capsule reference is outside execution scope")
    record = await HarnessTurnInputRecord.get(capsule_id)
    if record is None:
        raise LookupError("turn input capsule is missing")
    payload = _decode_record(record, expected_scope_key=scope_key)
    if record.payload_digest != expected_digest:
        raise ValueError("turn input capsule reference digest mismatch")
    if payload.work_item_id != work_item_id:
        raise ValueError("turn input capsule WorkItem mismatch")
    return payload


async def purge_turn_input_capsules_for_thread(
    *, principal_id: str, workspace_id: str, thread_id: str
) -> int:
    """Delete capsule Objects for the exact owner/workspace/thread scope."""
    scope_key = thread_scope_key(
        principal_id=principal_id, workspace_id=workspace_id, thread_id=thread_id
    )
    records = await HarnessTurnInputRecord.find({"scope_key": scope_key})
    deleted = 0
    for record in records:
        if (
            record.principal_id != principal_id
            or record.workspace_id != workspace_id
            or record.thread_id != thread_id
        ):
            raise ValueError("turn input capsule scope index is inconsistent")
        await record.delete()
        deleted += 1
    return deleted


async def purge_expired_turn_input_capsules(
    *, retention_days: int, batch_size: int = 100, now: datetime | None = None
) -> dict[str, int]:
    """Purge expired capsules only after their associated work is terminal/missing."""
    if retention_days < 1:
        raise ValueError("turn input retention must be at least one day")
    if batch_size < 1 or batch_size > 1000:
        raise ValueError("turn input retention batch size must be between 1 and 1000")
    from jvspatial.core.context import get_default_context

    from app.agentive.services.work_items import _object_id
    from app.agentive.work_models import WorkItem

    cutoff = now or datetime.now(timezone.utc)
    cutoff_iso = cutoff.astimezone(timezone.utc).isoformat()
    context = get_default_context()
    candidates = await context.find(
        HarnessTurnInputRecord,
        {"context.expires_at": {"$lt": cutoff_iso}},
        limit=batch_size,
    )
    deleted = skipped_active = 0
    for candidate in candidates:
        record = await HarnessTurnInputRecord.get(candidate.id)
        if record is None:
            continue
        work_item = await WorkItem.get(_object_id(record.work_item_id))
        if work_item is not None and work_item.status not in TERMINAL_WORK_STATUSES:
            skipped_active += 1
            continue
        await record.delete()
        deleted += 1
    return {"capsules_deleted": deleted, "active_work_skipped": skipped_active}


__all__ = [
    "HarnessTurnInputPayload",
    "load_turn_input_capsule",
    "persist_turn_input_capsule",
    "purge_expired_turn_input_capsules",
    "purge_turn_input_capsules_for_thread",
    "thread_scope_key",
    "turn_input_record_id",
]
