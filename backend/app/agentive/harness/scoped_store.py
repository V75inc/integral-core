"""Tenant/session namespace wrapper for Pydantic AI Harness StepStore."""

from __future__ import annotations

import hashlib
from contextlib import asynccontextmanager
from dataclasses import replace
from typing import AsyncIterator

from app.agentive.harness.contracts import HarnessExecutionScope
from app.agentive.harness.pydantic_ai_compat import (
    ContinuableSnapshot,
    RunRecord,
    StepEvent,
    StepStore,
    ToolEffectRecord,
)
from app.schemas.agentive.work import WorkExecutionContext


class HarnessScopeViolation(ValueError):
    """Raised when a framework identifier attempts to cross its Core scope."""


class ScopedStepStore:
    """Bind every Harness store operation to one Core tenant and session.

    The wrapped store is a trusted persistence dependency and must not be
    exposed to model-facing code. This class isolates records even when many
    tenants share one backend. It is not an encryption layer and does not make
    an in-memory or file store suitable for production persistence.
    """

    def __init__(
        self,
        *,
        store: StepStore,
        scope: HarnessExecutionScope,
        work_execution_context: WorkExecutionContext | None = None,
    ) -> None:
        if store is None:
            raise ValueError("a backing StepStore is required")
        if work_execution_context is not None and not getattr(
            store, "supports_transactional_effects", False
        ):
            raise ValueError(
                "durable WorkItem fencing requires a transaction-aware StepStore"
            )
        self._store = store
        self._scope = scope
        self._work_execution_context = work_execution_context
        value = "\0".join(
            (
                scope.tenant_id,
                scope.principal_id,
                scope.thread_id,
                scope.session_id,
            )
        ).encode("utf-8")
        self._prefix = f"integral_{hashlib.sha256(value).hexdigest()}_"

    @asynccontextmanager
    async def _effect_boundary(self) -> AsyncIterator[None]:
        if self._work_execution_context is None:
            yield
            return
        from app.agentive.services.work_items import authorized_work_item_effect

        async with authorized_work_item_effect(self._work_execution_context):
            yield

    def _map(self, value: str | None) -> str | None:
        if value is None:
            return None
        if not value or len(value) > 128:
            raise HarnessScopeViolation("invalid Harness identifier")
        return f"{self._prefix}{value}"

    def _map_required(self, value: str) -> str:
        mapped = self._map(value)
        assert mapped is not None
        return mapped

    def _unmap(self, value: str | None) -> str | None:
        if value is None:
            return None
        if not value.startswith(self._prefix):
            raise HarnessScopeViolation(
                "backing StepStore returned an out-of-scope identifier"
            )
        restored = value[len(self._prefix) :]
        if not restored:
            raise HarnessScopeViolation(
                "backing StepStore returned an empty identifier"
            )
        return restored

    def _unmap_required(self, value: str) -> str:
        restored = self._unmap(value)
        assert restored is not None
        return restored

    def _conversation(self, value: str | None) -> str:
        expected = self._scope.framework_conversation_id
        if value is not None and value != expected:
            raise HarnessScopeViolation(
                "conversation ID is outside the Integral session scope"
            )
        mapped = self._map(expected)
        assert mapped is not None
        return mapped

    def _restore_conversation(self, value: str | None) -> str:
        restored = self._unmap(value)
        if restored != self._scope.framework_conversation_id:
            raise HarnessScopeViolation(
                "backing StepStore returned a record outside the Integral session"
            )
        return restored

    def _run_record_in(self, record: RunRecord) -> RunRecord:
        if record.conversation_id not in (
            None,
            self._scope.framework_conversation_id,
        ):
            raise HarnessScopeViolation(
                "run record belongs to a different conversation"
            )
        return replace(
            record,
            run_id=self._map_required(record.run_id),
            conversation_id=self._conversation(record.conversation_id),
            parent_run_id=self._map(record.parent_run_id),
            registration_id=self._map(record.registration_id),
        )

    def _run_record_out(self, record: RunRecord) -> RunRecord:
        conversation_id = self._restore_conversation(record.conversation_id)
        return replace(
            record,
            run_id=self._unmap_required(record.run_id),
            conversation_id=conversation_id,
            parent_run_id=self._unmap(record.parent_run_id),
            registration_id=self._unmap(record.registration_id),
        )

    async def register_run(self, record: RunRecord) -> None:
        async with self._effect_boundary():
            await self._store.register_run(self._run_record_in(record))

    async def get_run(self, *, run_id: str) -> RunRecord | None:
        record = await self._store.get_run(run_id=self._map_required(run_id))
        return self._run_record_out(record) if record is not None else None

    async def list_runs(
        self,
        *,
        parent_run_id: str | None = None,
        conversation_id: str | None = None,
    ) -> list[RunRecord]:
        records = await self._store.list_runs(
            parent_run_id=self._map(parent_run_id),
            conversation_id=self._conversation(conversation_id),
        )
        return [self._run_record_out(record) for record in records]

    async def append_event(self, event: StepEvent) -> None:
        conversation_id = self._conversation(event.conversation_id)
        async with self._effect_boundary():
            await self._store.append_event(
                replace(
                    event,
                    run_id=self._map_required(event.run_id),
                    conversation_id=conversation_id,
                    parent_run_id=self._map(event.parent_run_id),
                    tool_call_id=self._map(event.tool_call_id),
                    idempotency_key=self._map(event.idempotency_key),
                )
            )

    async def list_events(self, *, run_id: str) -> list[StepEvent]:
        events = await self._store.list_events(run_id=self._map_required(run_id))
        restored = []
        for event in events:
            restored.append(
                replace(
                    event,
                    run_id=self._unmap_required(event.run_id),
                    conversation_id=self._restore_conversation(event.conversation_id),
                    parent_run_id=self._unmap(event.parent_run_id),
                    tool_call_id=self._unmap(event.tool_call_id),
                    idempotency_key=self._unmap(event.idempotency_key),
                )
            )
        return restored

    async def save_snapshot(self, snapshot: ContinuableSnapshot) -> None:
        conversation_id = self._conversation(snapshot.conversation_id)
        async with self._effect_boundary():
            await self._store.save_snapshot(
                replace(
                    snapshot,
                    run_id=self._map_required(snapshot.run_id),
                    conversation_id=conversation_id,
                    parent_run_id=self._map(snapshot.parent_run_id),
                    idempotency_key=self._map(snapshot.idempotency_key),
                )
            )

    async def latest_snapshot(
        self, *, run_id: str, include_interrupted: bool = False
    ) -> ContinuableSnapshot | None:
        snapshot = await self._store.latest_snapshot(
            run_id=self._map_required(run_id), include_interrupted=include_interrupted
        )
        if snapshot is None:
            return None
        conversation_id = self._restore_conversation(snapshot.conversation_id)
        return replace(
            snapshot,
            run_id=self._unmap_required(snapshot.run_id),
            conversation_id=conversation_id,
            parent_run_id=self._unmap(snapshot.parent_run_id),
            idempotency_key=self._unmap(snapshot.idempotency_key),
        )

    async def record_tool_effect(self, record: ToolEffectRecord) -> None:
        async with self._effect_boundary():
            await self._store.record_tool_effect(
                replace(
                    record,
                    run_id=self._map_required(record.run_id),
                    tool_call_id=self._map_required(record.tool_call_id),
                    idempotency_key=self._map(record.idempotency_key),
                )
            )

    async def get_tool_effect(
        self, *, run_id: str, tool_call_id: str
    ) -> ToolEffectRecord | None:
        record = await self._store.get_tool_effect(
            run_id=self._map_required(run_id),
            tool_call_id=self._map_required(tool_call_id),
        )
        return self._tool_effect_out(record) if record is not None else None

    async def list_unresolved_tool_effects(
        self, *, run_id: str
    ) -> list[ToolEffectRecord]:
        records = await self._store.list_unresolved_tool_effects(
            run_id=self._map_required(run_id)
        )
        return [self._tool_effect_out(record) for record in records]

    async def list_tool_effects(self, *, run_id: str) -> list[ToolEffectRecord]:
        """Return effect transitions after enforcing this store's run scope."""
        method = getattr(self._store, "list_tool_effects", None)
        if not callable(method):
            return []
        records = await method(run_id=self._map_required(run_id))
        return [self._tool_effect_out(record) for record in records]

    def _tool_effect_out(self, record: ToolEffectRecord) -> ToolEffectRecord:
        return replace(
            record,
            run_id=self._unmap_required(record.run_id),
            tool_call_id=self._unmap_required(record.tool_call_id),
            idempotency_key=self._unmap(record.idempotency_key),
        )

    async def list_snapshots(
        self, *, run_id: str, include_interrupted: bool = False
    ) -> list[ContinuableSnapshot]:
        """Scope the bundled store extension used by conversation search."""
        method = getattr(self._store, "list_snapshots", None)
        if not callable(method):
            return []
        snapshots = await method(
            run_id=self._map_required(run_id), include_interrupted=include_interrupted
        )
        result = []
        for snapshot in snapshots:
            conversation_id = self._restore_conversation(snapshot.conversation_id)
            result.append(
                replace(
                    snapshot,
                    run_id=self._unmap_required(snapshot.run_id),
                    conversation_id=conversation_id,
                    parent_run_id=self._unmap(snapshot.parent_run_id),
                    idempotency_key=self._unmap(snapshot.idempotency_key),
                )
            )
        return result
