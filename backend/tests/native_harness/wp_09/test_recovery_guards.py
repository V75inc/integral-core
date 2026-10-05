"""Safe-resume gates for interrupted native Harness work."""

from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace
from typing import Any

import pytest

from app.agentive.harness.contracts import HarnessExecutionScope
from app.api.errors import ResourceConflictError
from app.services.chat_providers.pydantic_ai_provider import (
    _load_recovery_user_message,
    _resume_history,
)


def _scope(run_id: str) -> HarnessExecutionScope:
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


def _mock_no_pending_approvals(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.agentive.work_models import WorkApproval

    async def find(_query):
        return []

    monkeypatch.setattr(WorkApproval, "find", find)


@pytest.mark.asyncio
async def test_unknown_model_outcome_blocks_resume_before_checkpoint_load(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A provider timeout after dispatch must not trigger a blind paid replay."""
    from app.agentive.harness import model_observations
    from app.services.chat_providers import pydantic_ai_provider as provider_module

    observed_at = datetime.now(timezone.utc)
    transition = SimpleNamespace(
        request_id="attempt-1",
        outcome="outcome_unknown",
        observed_at=observed_at,
        dispatched_at=observed_at,
    )
    load_calls = []

    async def observations(*, scope):
        assert scope.run_id == "previous-run"
        return [transition]

    async def continue_checkpoint(*_args: object, **_kwargs: object):
        load_calls.append(True)
        return []

    monkeypatch.setattr(
        model_observations, "list_model_request_observations", observations
    )
    monkeypatch.setattr(provider_module, "continue_run", continue_checkpoint)

    with pytest.raises(ResourceConflictError) as error:
        await _resume_history(
            _scope("next-run"),
            SimpleNamespace(last_run_id="previous-run"),
            SimpleNamespace(),
        )

    assert error.value.details["reason"] == "harness_model_request_unsettled"
    assert load_calls == []


@pytest.mark.asyncio
async def test_unresolved_tool_effect_blocks_resume_before_checkpoint_load(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An effect without a durable terminal receipt is never replayed by resume."""
    from app.agentive.harness import model_observations
    from app.services.chat_providers import pydantic_ai_provider as provider_module

    load_calls = []

    async def observations(*, scope):
        assert scope.run_id == "previous-run"
        return []

    async def continue_checkpoint(*_args: object, **_kwargs: object):
        load_calls.append(True)
        return []

    class Store:
        async def list_unresolved_tool_effects(self, *, run_id):
            assert run_id == "previous-run"
            return [SimpleNamespace(status="started")]

    monkeypatch.setattr(
        model_observations, "list_model_request_observations", observations
    )
    monkeypatch.setattr(provider_module, "continue_run", continue_checkpoint)

    with pytest.raises(ResourceConflictError) as error:
        await _resume_history(
            _scope("next-run"),
            SimpleNamespace(last_run_id="previous-run"),
            Store(),
        )

    assert error.value.details["reason"] == "harness_tool_effect_unresolved"
    assert load_calls == []


@pytest.mark.asyncio
async def test_resume_requires_manifest_matching_the_safe_complete_snapshot(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Only the manifest-backed settled snapshot is handed to the framework."""
    from app.agentive.harness import model_observations
    from app.agentive.harness.checkpoint_manifests import manifest_record_id
    from app.agentive.harness.contracts import HarnessCheckpointManifest
    from app.services.chat_providers import pydantic_ai_provider as provider_module

    previous_scope = _scope("previous-run")
    snapshot = SimpleNamespace(
        idempotency_key="safe-snapshot",
        state="complete",
        step_index=7,
    )
    manifest = HarnessCheckpointManifest(
        scope=previous_scope,
        execution_fence_id=previous_scope.run_id,
        framework_snapshot_id=snapshot.idempotency_key,
        snapshot_step_index=snapshot.step_index,
        snapshot_state="complete",
        safe_for_resume=True,
        framework_codec_version="pydantic-ai-messages-v1",
        capability_fingerprint=previous_scope.capability_version,
        capability_restore_policies={
            "conversation_messages": "restore",
            "brokered_tools": "rebuild",
        },
        plan_revision="plan-r1",
    )
    pointer_id = manifest_record_id(
        scope=previous_scope, snapshot_id=snapshot.idempotency_key
    )
    calls = []

    async def observations(*, scope):
        assert scope == previous_scope
        return []

    async def load_manifest(*, scope, snapshot_id):
        assert scope == previous_scope
        assert snapshot_id == snapshot.idempotency_key
        return manifest

    async def continue_checkpoint(store, *, run_id):
        calls.append((store, run_id))
        return ["safe-history"]

    class Store:
        async def list_unresolved_tool_effects(self, *, run_id):
            assert run_id == previous_scope.run_id
            return []

        async def list_snapshots(self, *, run_id):
            assert run_id == previous_scope.run_id
            return [snapshot]

        async def latest_snapshot(self, *, run_id):
            assert run_id == previous_scope.run_id
            return snapshot

    monkeypatch.setattr(
        model_observations, "list_model_request_observations", observations
    )
    monkeypatch.setattr(provider_module, "load_checkpoint_manifest", load_manifest)
    monkeypatch.setattr(provider_module, "continue_run", continue_checkpoint)
    _mock_no_pending_approvals(monkeypatch)
    store = Store()
    history = await _resume_history(
        _scope("next-run"),
        SimpleNamespace(
            last_run_id=previous_scope.run_id,
            last_checkpoint_run_id=previous_scope.run_id,
            last_checkpoint_id=pointer_id,
        ),
        store,
    )

    assert history == ["safe-history"]
    assert calls == [(store, previous_scope.run_id)]


@pytest.mark.asyncio
async def test_resume_blocks_manifest_pointer_mismatch(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A manifest that is not the session's safe pointer cannot be replayed."""
    from app.agentive.harness import model_observations
    from app.agentive.harness.contracts import HarnessCheckpointManifest
    from app.services.chat_providers import pydantic_ai_provider as provider_module

    previous_scope = _scope("previous-run")
    snapshot = SimpleNamespace(
        idempotency_key="safe-snapshot",
        state="complete",
        step_index=7,
    )
    manifest = HarnessCheckpointManifest(
        scope=previous_scope,
        execution_fence_id=previous_scope.run_id,
        framework_snapshot_id=snapshot.idempotency_key,
        snapshot_step_index=snapshot.step_index,
        snapshot_state="complete",
        safe_for_resume=True,
        framework_codec_version="pydantic-ai-messages-v1",
        capability_fingerprint=previous_scope.capability_version,
        capability_restore_policies={"conversation_messages": "restore"},
        plan_revision="plan-r1",
    )
    load_calls = []

    async def observations(*, scope):
        return []

    async def load_manifest(**_kwargs):
        return manifest

    async def continue_checkpoint(*_args: Any, **_kwargs: Any):
        load_calls.append(True)
        return []

    class Store:
        async def list_unresolved_tool_effects(self, *, run_id):
            return []

        async def list_snapshots(self, *, run_id):
            return [snapshot]

        async def latest_snapshot(self, *, run_id):
            return snapshot

    monkeypatch.setattr(
        model_observations, "list_model_request_observations", observations
    )
    monkeypatch.setattr(provider_module, "load_checkpoint_manifest", load_manifest)
    monkeypatch.setattr(provider_module, "continue_run", continue_checkpoint)
    _mock_no_pending_approvals(monkeypatch)

    with pytest.raises(ResourceConflictError) as error:
        await _resume_history(
            _scope("next-run"),
            SimpleNamespace(
                last_run_id=previous_scope.run_id,
                last_checkpoint_run_id=previous_scope.run_id,
                last_checkpoint_id="another-manifest",
            ),
            Store(),
        )

    assert error.value.details["reason"] == "harness_checkpoint_manifest_unavailable"
    assert load_calls == []


@pytest.mark.asyncio
async def test_rebuilds_text_turn_only_before_any_model_or_tool_activity(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A persisted text turn is recoverable before physical work starts."""
    from pydantic_ai.messages import ModelRequest, UserPromptPart

    from app.agentive.harness import model_observations
    from app.services.chat_providers import pydantic_ai_provider as provider_module

    async def observations(*, scope):
        assert scope.run_id == "previous-run"
        return []

    async def continue_checkpoint(*_args: object, **_kwargs: object):
        return []

    class Store:
        async def list_unresolved_tool_effects(self, *, run_id):
            assert run_id == "previous-run"
            return []

        async def list_tool_effects(self, *, run_id):
            assert run_id == "previous-run"
            return []

        async def latest_snapshot(self, *, run_id):
            assert run_id == "previous-run"
            return None

    monkeypatch.setattr(
        model_observations, "list_model_request_observations", observations
    )
    monkeypatch.setattr(provider_module, "continue_run", continue_checkpoint)
    _mock_no_pending_approvals(monkeypatch)
    prompt = ModelRequest(parts=[UserPromptPart(content="Resume my request")])

    history = await _resume_history(
        _scope("next-run"),
        SimpleNamespace(last_run_id="previous-run"),
        Store(),
        recovery_message=prompt,
    )

    assert history == [prompt]


@pytest.mark.asyncio
async def test_rebuild_blocks_when_any_terminal_tool_effect_exists(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Even a completed write cannot be replayed as a new agent turn."""
    from pydantic_ai.messages import ModelRequest, UserPromptPart

    from app.agentive.harness import model_observations
    from app.services.chat_providers import pydantic_ai_provider as provider_module

    async def observations(*, scope):
        return []

    class Store:
        async def list_unresolved_tool_effects(self, *, run_id):
            return []

        async def list_tool_effects(self, *, run_id):
            return [SimpleNamespace(status="succeeded")]

        async def latest_snapshot(self, *, run_id):
            return None

    monkeypatch.setattr(
        model_observations, "list_model_request_observations", observations
    )
    _mock_no_pending_approvals(monkeypatch)

    with pytest.raises(ResourceConflictError) as error:
        await _resume_history(
            _scope("next-run"),
            SimpleNamespace(last_run_id="previous-run"),
            Store(),
            recovery_message=ModelRequest(parts=[UserPromptPart(content="Do a write")]),
        )

    assert error.value.details["reason"] == "harness_recovery_reconciliation_required"


@pytest.mark.asyncio
async def test_recovery_message_requires_scoped_text_only_chat_record(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Recovery links cannot import images, attachments, or another user's turn."""
    from pydantic_ai.messages import ModelRequest

    from app.models.nodes import ChatMessage

    class Message:
        id = "message-a"
        thread_id = "thread-a"
        role = "user"
        parts = [{"type": "text", "text": "Continue safely"}]
        provider_metadata = None

    async def get(message_id):
        assert message_id == "message-a"
        return Message()

    monkeypatch.setattr(ChatMessage, "get", get)
    scope = _scope("next-run")
    run = SimpleNamespace(
        metadata={"chat_message_id": "message-a"},
        thread_id=scope.thread_id,
        user_id=scope.principal_id,
        workspace_id=scope.workspace_id,
        provider_id="integral_native",
    )

    prompt = await _load_recovery_user_message(run, scope)

    assert isinstance(prompt, ModelRequest)
    assert prompt.parts[0].content == "Continue safely"

    Message.parts = [
        {"type": "text", "text": "Continue safely"},
        {"type": "image", "data": "private"},
    ]
    assert await _load_recovery_user_message(run, scope) is None
