"""Encrypted, immutable Core recovery manifest tests."""

from __future__ import annotations

import pytest

from app.agentive.harness.checkpoint_manifests import (
    load_checkpoint_manifest,
    manifest_record_id,
    persist_checkpoint_manifest,
)
from app.agentive.harness.contracts import (
    HarnessCheckpointManifest,
    HarnessExecutionScope,
)
from app.agentive.harness.jvspatial_store import HarnessPersistenceError
from app.models.harness_records import HarnessCheckpointManifestRecord


@pytest.fixture
def manifest_rows(monkeypatch: pytest.MonkeyPatch):
    """Provide fake Object persistence while exercising the real encryption."""
    rows = {}
    monkeypatch.setattr(
        "app.services.credential_crypto._resolve_key", lambda: (b"m" * 32, None)
    )

    async def create_if_absent(cls, **kwargs):
        existing = rows.get(kwargs["id"])
        if existing is not None:
            return existing, False
        record = cls(**kwargs)
        rows[record.id] = record
        return record, True

    async def get(cls, record_id):
        return rows.get(record_id)

    monkeypatch.setattr(
        HarnessCheckpointManifestRecord,
        "create_if_absent",
        classmethod(create_if_absent),
    )
    monkeypatch.setattr(HarnessCheckpointManifestRecord, "get", classmethod(get))
    return rows


def _scope(run_id: str = "run-a") -> HarnessExecutionScope:
    return HarnessExecutionScope(
        tenant_id="workspace-a",
        principal_id="user-a",
        workspace_id="workspace-a",
        thread_id="thread-a",
        session_id="session-a",
        run_id=run_id,
        permission_revision="permissions-v1",
        capability_version="capabilities-v1",
    )


def _manifest(scope: HarnessExecutionScope | None = None):
    scope = scope or _scope()
    return HarnessCheckpointManifest(
        scope=scope,
        execution_fence_id=scope.run_id,
        framework_snapshot_id="snapshot-a",
        snapshot_step_index=4,
        snapshot_state="complete",
        safe_for_resume=True,
        framework_codec_version="pydantic-ai-messages-v1",
        capability_fingerprint=scope.capability_version,
        capability_restore_policies={
            "conversation_messages": "restore",
            "brokered_tools": "rebuild",
        },
        plan_revision="plan-revision-a",
        tool_effect_receipt_ids=["effect-1:completed"],
        model_request_ids=["request-1"],
        usage_reconciled=True,
    )


@pytest.mark.asyncio
async def test_manifest_is_encrypted_idempotent_and_scope_checked(manifest_rows):
    """Persist once, round-trip under scope, and conceal the payload."""
    manifest = _manifest()
    record_id = await persist_checkpoint_manifest(manifest)
    assert record_id == manifest_record_id(
        scope=manifest.scope, snapshot_id=manifest.framework_snapshot_id
    )
    assert await persist_checkpoint_manifest(manifest) == record_id

    record = manifest_rows[record_id]
    assert record.payload_ciphertext.startswith("v1:")
    assert "workspace-a" not in record.payload_ciphertext
    assert (
        await load_checkpoint_manifest(
            scope=manifest.scope, snapshot_id=manifest.framework_snapshot_id
        )
        == manifest
    )
    assert (
        await load_checkpoint_manifest(
            scope=_scope("different-run"), snapshot_id=manifest.framework_snapshot_id
        )
        is None
    )


@pytest.mark.asyncio
async def test_manifest_id_cannot_be_rebound_to_different_recovery_facts(
    manifest_rows,
):
    """Reject immutable manifest identity reuse with changed facts."""
    manifest = _manifest()
    await persist_checkpoint_manifest(manifest)
    conflicting = manifest.model_copy(update={"plan_revision": "plan-revision-b"})

    with pytest.raises(HarnessPersistenceError, match="already bound"):
        await persist_checkpoint_manifest(conflicting)


@pytest.mark.asyncio
async def test_tampered_manifest_ciphertext_fails_authentication(manifest_rows):
    """Authenticated encryption detects stored payload modification."""
    manifest = _manifest()
    record_id = await persist_checkpoint_manifest(manifest)
    record = manifest_rows[record_id]
    record.payload_ciphertext = record.payload_ciphertext[:-2] + "aa"

    with pytest.raises(HarnessPersistenceError, match="authenticated or decoded"):
        await load_checkpoint_manifest(
            scope=manifest.scope, snapshot_id=manifest.framework_snapshot_id
        )


@pytest.mark.asyncio
async def test_manifest_key_rotation_reads_previous_and_writes_with_current(
    manifest_rows, monkeypatch: pytest.MonkeyPatch
):
    """Rotation overlap keeps old evidence readable while new rows use new key."""
    keys = {"current": b"o" * 32, "previous": None}
    monkeypatch.setattr(
        "app.services.credential_crypto._resolve_key",
        lambda: (keys["current"], None),
    )
    monkeypatch.setattr(
        "app.services.credential_crypto._previous_key", lambda: keys["previous"]
    )

    old_manifest = _manifest()
    old_record_id = await persist_checkpoint_manifest(old_manifest)
    old_ciphertext = manifest_rows[old_record_id].payload_ciphertext
    keys.update(current=b"n" * 32, previous=b"o" * 32)

    assert (
        await load_checkpoint_manifest(
            scope=old_manifest.scope, snapshot_id=old_manifest.framework_snapshot_id
        )
        == old_manifest
    )

    new_manifest = _manifest().model_copy(
        update={"framework_snapshot_id": "snapshot-after-rotation"}
    )
    new_record_id = await persist_checkpoint_manifest(new_manifest)
    new_ciphertext = manifest_rows[new_record_id].payload_ciphertext
    assert new_ciphertext != old_ciphertext

    keys["previous"] = None
    assert (
        await load_checkpoint_manifest(
            scope=new_manifest.scope, snapshot_id=new_manifest.framework_snapshot_id
        )
        == new_manifest
    )
    with pytest.raises(HarnessPersistenceError, match="authenticated or decoded"):
        await load_checkpoint_manifest(
            scope=old_manifest.scope, snapshot_id=old_manifest.framework_snapshot_id
        )


def test_manifest_rejects_duplicate_or_noncanonical_references():
    """Reference collections have canonical unique identities."""
    values = _manifest().model_dump()
    values["tool_effect_receipt_ids"] = ["effect-1", "effect-1"]

    with pytest.raises(ValueError, match="unique"):
        HarnessCheckpointManifest.model_validate(values)


def test_manifest_requires_core_run_id_as_execution_fence():
    """A manifest cannot claim authority from a different run fence."""
    values = _manifest().model_dump()
    values["execution_fence_id"] = "stale-run"

    with pytest.raises(ValueError, match="must match its Core run"):
        HarnessCheckpointManifest.model_validate(values)
