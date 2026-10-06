"""Encrypted Core recovery manifests paired with Harness snapshots."""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone

from app.agentive.harness.contracts import (
    HarnessCheckpointManifest,
    HarnessExecutionScope,
)
from app.agentive.harness.jvspatial_store import HarnessPersistenceError
from app.models.harness_records import HarnessCheckpointManifestRecord
from app.schemas.agentive.work import WorkExecutionContext
from app.services.credential_crypto import (
    CIPHER_PREFIX_V1,
    decrypt_secret_from_storage,
    encrypt_secret_for_storage,
)


def _scope_key(scope: HarnessExecutionScope) -> str:
    identity = "\0".join(
        (scope.tenant_id, scope.principal_id, scope.thread_id, scope.session_id)
    )
    return hashlib.sha256(identity.encode("utf-8")).hexdigest()


def _record_id(scope_key: str, run_id: str, snapshot_id: str) -> str:
    identity = f"{scope_key}:checkpoint-manifest:{run_id}:{snapshot_id}"
    digest = hashlib.sha256(identity.encode("utf-8")).hexdigest()
    return f"o.HarnessCheckpointManifestRecord.{digest}"


def manifest_record_id(*, scope: HarnessExecutionScope, snapshot_id: str) -> str:
    """Return the stable Object ID for a scope/run/snapshot manifest tuple."""
    return _record_id(_scope_key(scope), scope.run_id, snapshot_id)


def _encode(manifest: HarnessCheckpointManifest, *, record_id: str) -> str:
    payload = json.dumps(
        manifest.model_dump(mode="json"),
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    )
    try:
        return encrypt_secret_for_storage(payload, aad=record_id)
    except RuntimeError as exc:
        raise HarnessPersistenceError(
            "Harness checkpoint manifests require configured storage encryption"
        ) from exc


def _decode(record: HarnessCheckpointManifestRecord) -> HarnessCheckpointManifest:
    try:
        stored = record.payload_ciphertext
        if not stored.startswith(CIPHER_PREFIX_V1):
            raise ValueError("checkpoint manifest is not encrypted")
        raw = decrypt_secret_from_storage(stored, aad=record.id)
        if not raw:
            raise ValueError("checkpoint manifest decryption returned no content")
        return HarnessCheckpointManifest.model_validate_json(raw)
    except Exception as exc:
        raise HarnessPersistenceError(
            "Harness checkpoint manifest could not be authenticated or decoded"
        ) from exc


async def persist_checkpoint_manifest(
    manifest: HarnessCheckpointManifest,
    *,
    work_execution_context: WorkExecutionContext | None = None,
) -> str:
    """Append one immutable recovery manifest and return its Object identity."""
    scope_key = _scope_key(manifest.scope)
    snapshot_id = manifest.framework_snapshot_id
    record_id = manifest_record_id(scope=manifest.scope, snapshot_id=snapshot_id)
    payload_ciphertext = _encode(manifest, record_id=record_id)

    async def persist() -> None:
        record, created = await HarnessCheckpointManifestRecord.create_if_absent(
            id=record_id,
            scope_key=scope_key,
            run_key=manifest.scope.run_id,
            checkpoint_key=snapshot_id,
            created_at=datetime.now(timezone.utc).isoformat(),
            schema_version=manifest.schema_version,
            payload_ciphertext=payload_ciphertext,
        )
        if not created and _decode(record) != manifest:
            raise HarnessPersistenceError(
                "checkpoint manifest identity is already bound to different evidence"
            )

    if work_execution_context is None:
        await persist()
    else:
        from app.agentive.services.work_items import authorized_work_item_effect

        async with authorized_work_item_effect(work_execution_context):
            await persist()
    return record_id


async def load_checkpoint_manifest(
    *,
    scope: HarnessExecutionScope,
    snapshot_id: str,
) -> HarnessCheckpointManifest | None:
    """Load a manifest only within its original Core execution namespace."""
    scope_key = _scope_key(scope)
    record_id = manifest_record_id(scope=scope, snapshot_id=snapshot_id)
    record = await HarnessCheckpointManifestRecord.get(record_id)
    if record is None:
        return None
    if (
        record.scope_key != scope_key
        or record.run_key != scope.run_id
        or record.checkpoint_key != snapshot_id
    ):
        raise HarnessPersistenceError("checkpoint manifest storage scope mismatch")
    manifest = _decode(record)
    if manifest.scope != scope or manifest.framework_snapshot_id != snapshot_id:
        raise HarnessPersistenceError("checkpoint manifest payload scope mismatch")
    if record.schema_version != manifest.schema_version:
        raise HarnessPersistenceError("checkpoint manifest version mismatch")
    return manifest
