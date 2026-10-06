"""WP-09.3 crash, codec, store-divergence and key-rotation proofs."""

from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from app.agentive.harness.contracts import HarnessExecutionScope
from app.api.errors import ResourceConflictError
from app.services.chat_providers.pydantic_ai_provider import _resume_history


def _scope(run_id: str = "run-old") -> HarnessExecutionScope:
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


def _manifest(scope: HarnessExecutionScope, snapshot: SimpleNamespace):
    from app.agentive.harness.contracts import HarnessCheckpointManifest

    return HarnessCheckpointManifest(
        scope=scope,
        execution_fence_id=scope.run_id,
        framework_snapshot_id=snapshot.idempotency_key,
        snapshot_step_index=snapshot.step_index,
        snapshot_state="complete",
        safe_for_resume=True,
        framework_codec_version="pydantic-ai-messages-v1",
        capability_fingerprint=scope.capability_version,
        capability_restore_policies={"conversation_messages": "restore"},
        plan_revision="plan-r1",
        usage_reconciled=True,
    )


def _no_approvals(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.agentive.work_models import WorkApproval

    async def find(_query):
        return []

    monkeypatch.setattr(WorkApproval, "find", find)


@pytest.mark.asyncio
async def test_turn_cancelled_after_run_claim_before_model_dispatch_is_recoverable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A stop at the first event preserves run identity without dispatching a model."""
    import tempfile

    from pydantic_ai_harness.step_persistence import InMemoryStepStore

    from app.services.chat_providers.base import ChatTurnContext
    from app.services.chat_providers.pydantic_ai_provider import PydanticAIProvider

    scope = _scope("run-claimed")
    session = SimpleNamespace(last_run_id=None)
    model_calls = []
    provider = PydanticAIProvider()

    class NeverRunAgent:
        def run_stream_events(self, *_args: object, **_kwargs: object):
            model_calls.append(True)
            raise AssertionError("model dispatch crossed the cancellation boundary")

    async def prepare(_ctx):
        return (
            scope,
            session,
            NeverRunAgent(),
            InMemoryStepStore(),
            [],
            tempfile.TemporaryDirectory(),
        )

    async def claim(*, scope):
        session.last_run_id = scope.run_id
        return session

    monkeypatch.setattr(provider, "_prepare", prepare)
    monkeypatch.setattr(
        "app.services.chat_providers.pydantic_ai_provider.claim_harness_run", claim
    )
    ctx = ChatTurnContext(
        user_id="user-a",
        user_email="user@example.test",
        text="a persisted user turn",
        thread_id=scope.thread_id,
        session_id=scope.session_id,
    )
    stream = provider.stream_turn(ctx)

    first_event = await anext(stream)
    assert first_event == {
        "type": "_meta",
        "provider_session_id": scope.session_id,
    }
    await stream.aclose()

    assert session.last_run_id == scope.run_id
    assert model_calls == []
    assert scope.thread_id not in provider._active_tokens


@pytest.mark.asyncio
async def test_snapshot_without_manifest_after_dispatch_requires_reconciliation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A crash between the framework snapshot and Core manifest is not resumable."""
    from app.agentive.harness import model_observations
    from app.services.chat_providers import pydantic_ai_provider as provider_module

    now = datetime.now(timezone.utc)
    snapshot = SimpleNamespace(
        idempotency_key="snapshot-a", state="complete", step_index=4
    )
    observation = SimpleNamespace(
        request_id="request-a",
        outcome="responded",
        observed_at=now,
        dispatched_at=now,
    )
    resumed = []

    async def observations(*, scope):
        assert scope.run_id == "run-old"
        return [observation]

    async def missing_manifest(**_kwargs):
        return None

    async def continue_checkpoint(*_args: object, **_kwargs: object):
        resumed.append(True)
        return []

    class Store:
        async def list_unresolved_tool_effects(self, *, run_id):
            return []

        async def list_tool_effects(self, *, run_id):
            return []

        async def latest_snapshot(self, *, run_id):
            return snapshot

    monkeypatch.setattr(
        model_observations, "list_model_request_observations", observations
    )
    monkeypatch.setattr(provider_module, "load_checkpoint_manifest", missing_manifest)
    monkeypatch.setattr(provider_module, "continue_run", continue_checkpoint)
    _no_approvals(monkeypatch)

    with pytest.raises(ResourceConflictError) as error:
        await _resume_history(
            _scope("run-new"), SimpleNamespace(last_run_id="run-old"), Store()
        )

    assert error.value.details["reason"] == "harness_recovery_reconciliation_required"
    assert resumed == []


@pytest.mark.asyncio
async def test_manifest_pointer_without_framework_snapshot_fails_closed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A Core pointer cannot authorize a missing record in the Harness store."""
    from app.agentive.harness import contracts, model_observations
    from app.agentive.harness.checkpoint_manifests import manifest_record_id
    from app.services.chat_providers import pydantic_ai_provider as provider_module

    previous_scope = _scope()
    snapshot_id = "snapshot-a"
    pointer_id = manifest_record_id(scope=previous_scope, snapshot_id=snapshot_id)
    resume_calls = []

    async def observations(*, scope):
        return []

    async def load_manifest(**_kwargs):
        return contracts.HarnessCheckpointManifest(
            scope=previous_scope,
            execution_fence_id=previous_scope.run_id,
            framework_snapshot_id=snapshot_id,
            snapshot_step_index=4,
            snapshot_state="complete",
            safe_for_resume=True,
            framework_codec_version="pydantic-ai-messages-v1",
            capability_fingerprint=previous_scope.capability_version,
            capability_restore_policies={"conversation_messages": "restore"},
            plan_revision="plan-r1",
        )

    async def continue_checkpoint(*_args: object, **_kwargs: object):
        resume_calls.append(True)
        return []

    class Store:
        async def list_unresolved_tool_effects(self, *, run_id):
            return []

        async def list_tool_effects(self, *, run_id):
            return []

        async def list_snapshots(self, *, run_id):
            return []

        async def latest_snapshot(self, *, run_id):
            return None

    monkeypatch.setattr(
        model_observations, "list_model_request_observations", observations
    )
    monkeypatch.setattr(provider_module, "load_checkpoint_manifest", load_manifest)
    monkeypatch.setattr(provider_module, "continue_run", continue_checkpoint)
    _no_approvals(monkeypatch)

    with pytest.raises(ResourceConflictError) as error:
        await _resume_history(
            _scope("run-new"),
            SimpleNamespace(
                last_run_id=previous_scope.run_id,
                last_checkpoint_run_id=previous_scope.run_id,
                last_checkpoint_id=pointer_id,
            ),
            Store(),
        )

    assert error.value.details["reason"] == "harness_checkpoint_manifest_unavailable"
    assert resume_calls == []


@pytest.mark.asyncio
async def test_manifest_written_before_pointer_is_repaired_after_crash(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A complete two-store pair can repair a lost pointer-CAS after process death."""
    from app.agentive.harness import model_observations
    from app.services.chat_providers import pydantic_ai_provider as provider_module

    previous_scope = _scope()
    snapshot = SimpleNamespace(
        idempotency_key="snapshot-a", state="complete", step_index=4
    )
    manifest = _manifest(previous_scope, snapshot)
    pointer_repairs = []
    resume_calls = []

    async def observations(*, scope):
        return []

    async def load_manifest(*, scope, snapshot_id):
        assert scope == previous_scope
        assert snapshot_id == snapshot.idempotency_key
        return manifest

    async def advance(*, scope, snapshot_id):
        pointer_repairs.append((scope.run_id, snapshot_id))

    async def continue_checkpoint(_store, *, run_id):
        resume_calls.append(run_id)
        return ["restored-history"]

    class Store:
        async def list_unresolved_tool_effects(self, *, run_id):
            return []

        async def list_tool_effects(self, *, run_id):
            return []

        async def latest_snapshot(self, *, run_id):
            return snapshot

    monkeypatch.setattr(
        model_observations, "list_model_request_observations", observations
    )
    monkeypatch.setattr(provider_module, "load_checkpoint_manifest", load_manifest)
    monkeypatch.setattr(provider_module, "advance_harness_checkpoint_pointer", advance)
    monkeypatch.setattr(provider_module, "continue_run", continue_checkpoint)
    _no_approvals(monkeypatch)

    history = await _resume_history(
        _scope("run-new"), SimpleNamespace(last_run_id=previous_scope.run_id), Store()
    )

    assert history == ["restored-history"]
    assert pointer_repairs == [(previous_scope.run_id, snapshot.idempotency_key)]
    assert resume_calls == [previous_scope.run_id]


def test_framework_snapshot_codec_corruption_is_rejected(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Malformed encrypted JSON is a persistence failure, never empty history."""
    from app.agentive.harness.jvspatial_store import (
        HarnessPersistenceError,
        JvSpatialStepStore,
    )

    store = JvSpatialStepStore(scope=_scope())
    monkeypatch.setattr(
        "app.services.credential_crypto.decrypt_secret_from_storage",
        lambda *_args, **_kwargs: "{not-json",
    )

    with pytest.raises(HarnessPersistenceError, match="authenticated or decoded"):
        store._decrypt("v1:corrupt", record_id="snapshot-record")
