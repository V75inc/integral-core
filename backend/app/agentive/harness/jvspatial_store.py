"""Encrypted jvspatial Object backend for Pydantic AI Harness StepStore."""

from __future__ import annotations

import hashlib
import json
from datetime import datetime
from typing import Any
from uuid import uuid4

from pydantic_ai.messages import ModelMessagesTypeAdapter
from pydantic_ai_harness.step_persistence import (
    ContinuableSnapshot,
    RunRecord,
    StepEvent,
    ToolEffectRecord,
)

from app.agentive.harness.contracts import HarnessExecutionScope
from app.models.harness_records import (
    HarnessEventRecord,
    HarnessRunRecord,
    HarnessSnapshotRecord,
    HarnessToolEffectRecord,
)
from app.services.credential_crypto import (
    CIPHER_PREFIX_V1,
    decrypt_secret_from_storage,
    encrypt_secret_for_storage,
)


class HarnessPersistenceError(RuntimeError):
    """Raised when durable Harness state cannot be safely read or written."""


def _iso(value: datetime) -> str:
    return value.isoformat()


def _datetime(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def _stable_id(model: type[Any], key: str) -> str:
    digest = hashlib.sha256(key.encode("utf-8")).hexdigest()
    return f"o.{model._entity_name()}.{digest}"


class JvSpatialStepStore:
    """Persist Harness state as tenant-scoped, encrypted, append-only Objects.

    The store deliberately uses no Node records: transcripts and execution
    receipts are log-shaped. All records carry a keyed execution namespace,
    and each record payload is AES-GCM encrypted with its deterministic record
    ID as associated data. Encryption is fail-closed when Integral's storage
    key is unavailable.
    """

    supports_transactional_effects = True

    def __init__(self, *, scope: HarnessExecutionScope) -> None:
        self._scope = scope
        namespace = "\0".join(
            (
                scope.tenant_id,
                scope.principal_id,
                scope.thread_id,
                scope.session_id,
            )
        )
        self._scope_key = hashlib.sha256(namespace.encode("utf-8")).hexdigest()

    def _encrypt(self, payload: dict[str, Any], *, record_id: str) -> str:
        raw = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
        try:
            return encrypt_secret_for_storage(raw, aad=record_id)
        except RuntimeError as exc:
            raise HarnessPersistenceError(
                "Harness persistence requires configured Integral storage encryption"
            ) from exc

    def _decrypt(self, stored: str, *, record_id: str) -> dict[str, Any]:
        try:
            if not stored.startswith(CIPHER_PREFIX_V1):
                raise ValueError("Harness payload is not encrypted")
            raw = decrypt_secret_from_storage(stored, aad=record_id)
            if not raw:
                raise ValueError("decryption returned no payload")
            payload = json.loads(raw)
        except Exception as exc:
            raise HarnessPersistenceError(
                "Harness record could not be authenticated or decoded"
            ) from exc
        if not isinstance(payload, dict):
            raise HarnessPersistenceError("Harness record payload is not an object")
        return payload

    def _record_key(self, kind: str, key: str) -> str:
        return f"{self._scope_key}:{kind}:{key}"

    async def _find(self, model: type[Any], **filters: Any) -> list[Any]:
        return await model.find({"scope_key": self._scope_key, **filters})

    async def register_run(self, record: RunRecord) -> None:
        key = self._record_key("run", record.run_id)
        record_id = _stable_id(HarnessRunRecord, key)
        payload = {
            "run_id": record.run_id,
            "conversation_id": record.conversation_id,
            "parent_run_id": record.parent_run_id,
            "agent_name": record.agent_name,
            "metadata": record.metadata,
            "started_at": _iso(record.started_at),
            "registration_id": record.registration_id,
        }
        stored, created = await HarnessRunRecord.create_if_absent(
            id=record_id,
            scope_key=self._scope_key,
            run_key=record.run_id,
            conversation_key=record.conversation_id or "",
            parent_run_key=record.parent_run_id or "",
            payload_ciphertext=self._encrypt(payload, record_id=record_id),
        )
        if not created:
            prior = self._decrypt(stored.payload_ciphertext, record_id=record_id)
            if prior != payload:
                raise HarnessPersistenceError(
                    "run ID is already registered with different lineage metadata"
                )

    async def get_run(self, *, run_id: str) -> RunRecord | None:
        records = await self._find(HarnessRunRecord, run_key=run_id)
        if not records:
            return None
        record = records[0]
        payload = self._decrypt(record.payload_ciphertext, record_id=record.id)
        return RunRecord(
            run_id=payload["run_id"],
            conversation_id=payload.get("conversation_id"),
            parent_run_id=payload.get("parent_run_id"),
            agent_name=payload.get("agent_name"),
            metadata=dict(payload.get("metadata") or {}),
            started_at=_datetime(payload["started_at"]),
            registration_id=payload.get("registration_id"),
        )

    async def list_runs(
        self,
        *,
        parent_run_id: str | None = None,
        conversation_id: str | None = None,
    ) -> list[RunRecord]:
        filters: dict[str, Any] = {}
        if parent_run_id is not None:
            filters["parent_run_key"] = parent_run_id
        if conversation_id is not None:
            filters["conversation_key"] = conversation_id
        records = await self._find(HarnessRunRecord, **filters)
        runs = []
        for record in records:
            payload = self._decrypt(record.payload_ciphertext, record_id=record.id)
            runs.append(
                RunRecord(
                    run_id=payload["run_id"],
                    conversation_id=payload.get("conversation_id"),
                    parent_run_id=payload.get("parent_run_id"),
                    agent_name=payload.get("agent_name"),
                    metadata=dict(payload.get("metadata") or {}),
                    started_at=_datetime(payload["started_at"]),
                    registration_id=payload.get("registration_id"),
                )
            )
        return sorted(runs, key=lambda item: item.started_at)

    async def append_event(self, event: StepEvent) -> None:
        identity = event.idempotency_key or str(uuid4())
        key = self._record_key("event", f"{event.run_id}:{identity}")
        record_id = _stable_id(HarnessEventRecord, key)
        payload = {
            "run_id": event.run_id,
            "kind": event.kind,
            "step_index": event.step_index,
            "timestamp": _iso(event.timestamp),
            "conversation_id": event.conversation_id,
            "parent_run_id": event.parent_run_id,
            "agent_name": event.agent_name,
            "tool_call_id": event.tool_call_id,
            "tool_name": event.tool_name,
            "error": event.error,
            "metadata": event.metadata,
            "idempotency_key": event.idempotency_key,
        }
        stored, created = await HarnessEventRecord.create_if_absent(
            id=record_id,
            scope_key=self._scope_key,
            run_key=event.run_id,
            record_key=identity,
            occurred_at=_iso(event.timestamp),
            step_index=event.step_index,
            payload_ciphertext=self._encrypt(payload, record_id=record_id),
        )
        if (
            not created
            and self._decrypt(stored.payload_ciphertext, record_id=record_id) != payload
        ):
            raise HarnessPersistenceError(
                "event idempotency key is already bound to different content"
            )

    async def list_events(self, *, run_id: str) -> list[StepEvent]:
        records = await self._find(HarnessEventRecord, run_key=run_id)
        events = []
        for record in records:
            payload = self._decrypt(record.payload_ciphertext, record_id=record.id)
            events.append(
                StepEvent(
                    run_id=payload["run_id"],
                    kind=payload["kind"],
                    step_index=payload["step_index"],
                    timestamp=_datetime(payload["timestamp"]),
                    conversation_id=payload.get("conversation_id"),
                    parent_run_id=payload.get("parent_run_id"),
                    agent_name=payload.get("agent_name"),
                    tool_call_id=payload.get("tool_call_id"),
                    tool_name=payload.get("tool_name"),
                    error=payload.get("error"),
                    metadata=dict(payload.get("metadata") or {}),
                    idempotency_key=payload.get("idempotency_key"),
                )
            )
        return sorted(events, key=lambda item: (item.step_index, item.timestamp))

    async def save_snapshot(self, snapshot: ContinuableSnapshot) -> None:
        identity = snapshot.idempotency_key or str(uuid4())
        key = self._record_key("snapshot", f"{snapshot.run_id}:{identity}")
        record_id = _stable_id(HarnessSnapshotRecord, key)
        payload = {
            "run_id": snapshot.run_id,
            "step_index": snapshot.step_index,
            "messages": json.loads(
                ModelMessagesTypeAdapter.dump_json(snapshot.messages)
            ),
            "conversation_id": snapshot.conversation_id,
            "parent_run_id": snapshot.parent_run_id,
            "agent_name": snapshot.agent_name,
            "timestamp": _iso(snapshot.timestamp),
            "state": snapshot.state,
            "idempotency_key": snapshot.idempotency_key,
        }
        stored, created = await HarnessSnapshotRecord.create_if_absent(
            id=record_id,
            scope_key=self._scope_key,
            run_key=snapshot.run_id,
            record_key=identity,
            occurred_at=_iso(snapshot.timestamp),
            step_index=snapshot.step_index,
            state=snapshot.state,
            payload_ciphertext=self._encrypt(payload, record_id=record_id),
        )
        if (
            not created
            and self._decrypt(stored.payload_ciphertext, record_id=record_id) != payload
        ):
            raise HarnessPersistenceError(
                "checkpoint idempotency key is already bound to different content"
            )

    def _snapshot_from(self, record: Any) -> ContinuableSnapshot:
        payload = self._decrypt(record.payload_ciphertext, record_id=record.id)
        return ContinuableSnapshot(
            run_id=payload["run_id"],
            step_index=payload["step_index"],
            messages=ModelMessagesTypeAdapter.validate_python(payload["messages"]),
            conversation_id=payload.get("conversation_id"),
            parent_run_id=payload.get("parent_run_id"),
            agent_name=payload.get("agent_name"),
            timestamp=_datetime(payload["timestamp"]),
            state=payload["state"],
            idempotency_key=payload.get("idempotency_key"),
        )

    async def list_snapshots(
        self, *, run_id: str, include_interrupted: bool = False
    ) -> list[ContinuableSnapshot]:
        records = await self._find(HarnessSnapshotRecord, run_key=run_id)
        if not include_interrupted:
            records = [record for record in records if record.state == "complete"]
        snapshots = [self._snapshot_from(record) for record in records]
        return sorted(snapshots, key=lambda item: (item.step_index, item.timestamp))

    async def latest_snapshot(
        self, *, run_id: str, include_interrupted: bool = False
    ) -> ContinuableSnapshot | None:
        snapshots = await self.list_snapshots(
            run_id=run_id, include_interrupted=include_interrupted
        )
        return snapshots[-1] if snapshots else None

    async def record_tool_effect(self, record: ToolEffectRecord) -> None:
        payload = {
            "tool_call_id": record.tool_call_id,
            "tool_name": record.tool_name,
            "run_id": record.run_id,
            "status": record.status,
            "started_at": _iso(record.started_at),
            "ended_at": _iso(record.ended_at) if record.ended_at else None,
            "idempotency_key": record.idempotency_key,
            "effect_summary": record.effect_summary,
        }
        identity = hashlib.sha256(
            json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
        ).hexdigest()
        key = self._record_key(
            "tool-effect", f"{record.run_id}:{record.tool_call_id}:{identity}"
        )
        record_id = _stable_id(HarnessToolEffectRecord, key)
        occurred_at = _iso(record.ended_at or record.started_at)
        await HarnessToolEffectRecord.create_if_absent(
            id=record_id,
            scope_key=self._scope_key,
            run_key=record.run_id,
            tool_call_key=record.tool_call_id,
            occurred_at=occurred_at,
            status=record.status,
            payload_ciphertext=self._encrypt(payload, record_id=record_id),
        )

    def _tool_effect_from(self, record: Any) -> ToolEffectRecord:
        payload = self._decrypt(record.payload_ciphertext, record_id=record.id)
        return ToolEffectRecord(
            tool_call_id=payload["tool_call_id"],
            tool_name=payload["tool_name"],
            run_id=payload["run_id"],
            status=payload["status"],
            started_at=_datetime(payload["started_at"]),
            ended_at=(
                _datetime(payload["ended_at"]) if payload.get("ended_at") else None
            ),
            idempotency_key=payload.get("idempotency_key"),
            effect_summary=payload.get("effect_summary"),
        )

    async def get_tool_effect(
        self, *, run_id: str, tool_call_id: str
    ) -> ToolEffectRecord | None:
        records = await self._find(
            HarnessToolEffectRecord,
            run_key=run_id,
            tool_call_key=tool_call_id,
        )
        effects = [self._tool_effect_from(record) for record in records]
        effects.sort(
            key=lambda item: (
                item.ended_at or item.started_at,
                item.status != "started",
            )
        )
        return effects[-1] if effects else None

    async def list_unresolved_tool_effects(
        self, *, run_id: str
    ) -> list[ToolEffectRecord]:
        records = await self._find(HarnessToolEffectRecord, run_key=run_id)
        latest: dict[str, ToolEffectRecord] = {}
        for record in records:
            effect = self._tool_effect_from(record)
            prior = latest.get(effect.tool_call_id)
            effect_order = (
                effect.ended_at or effect.started_at,
                effect.status != "started",
            )
            prior_order = (
                (prior.ended_at or prior.started_at, prior.status != "started")
                if prior is not None
                else None
            )
            if prior_order is None or effect_order >= prior_order:
                latest[effect.tool_call_id] = effect
        return sorted(
            (effect for effect in latest.values() if effect.status == "started"),
            key=lambda item: item.started_at,
        )

    async def list_tool_effects(self, *, run_id: str) -> list[ToolEffectRecord]:
        """Return the append-only effect transitions for one run."""
        records = await self._find(HarnessToolEffectRecord, run_key=run_id)
        effects = [self._tool_effect_from(record) for record in records]
        return sorted(
            effects,
            key=lambda item: (
                item.started_at,
                item.ended_at or item.started_at,
                item.tool_call_id,
            ),
        )
