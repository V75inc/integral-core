"""Encrypted jvspatial Object backend for Pydantic AI Harness StepStore."""

from __future__ import annotations

import asyncio
import hashlib
import json
from datetime import datetime
from typing import Any
from uuid import uuid4

from app.agentive.harness.contracts import HarnessExecutionScope
from app.agentive.harness.pydantic_ai_compat import (
    ContinuableSnapshot,
    ModelMessagesTypeAdapter,
    RunRecord,
    StepEvent,
    ToolEffectRecord,
)
from app.models.harness_records import (
    HarnessEventRecord,
    HarnessMessageRecord,
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


# One snapshot can introduce many new messages. Each insert takes a pool
# connection, so the wave stays bounded instead of opening one per message.
_MESSAGE_INSERT_CONCURRENCY = 8


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

    New checkpoints follow the pinned Harness snapshot shape
    ``{"message_refs": [sha256, ...]}`` and store each canonical message once.
    ``PostgresStepStore`` already does that, but its table is plaintext and its
    message key is global; the helpers that build the shape are private. A
    direct switch would drop application ciphertext and scope. Message identity
    here is the execution scope plus the hash. Older checkpoints that still
    carry an inline ``messages`` list remain readable and are not rewritten.
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

    def _canonical_message_parts(self, messages: list[Any]) -> list[tuple[str, str]]:
        dumped = json.loads(ModelMessagesTypeAdapter.dump_json(messages))
        if not isinstance(dumped, list):
            raise HarnessPersistenceError("checkpoint messages are not a list")
        parts: list[tuple[str, str]] = []
        for message in dumped:
            text = json.dumps(message, sort_keys=True, separators=(",", ":"))
            digest = hashlib.sha256(text.encode()).hexdigest()
            parts.append((digest, text))
        return parts

    def _message_record_id(self, digest: str) -> str:
        return _stable_id(HarnessMessageRecord, self._record_key("message", digest))

    def _assert_stored_message(
        self, record: Any, *, digest: str, payload: dict[str, str]
    ) -> None:
        if record.scope_key != self._scope_key or record.content_hash != digest:
            raise HarnessPersistenceError(
                "checkpoint message scope does not match its record"
            )
        prior_payload = self._decrypt(record.payload_ciphertext, record_id=record.id)
        if prior_payload != payload:
            raise HarnessPersistenceError(
                "checkpoint message hash is already bound to different content"
            )

    async def _find_messages(self, record_ids: list[str]) -> dict[str, Any]:
        if not record_ids:
            return {}
        unique_ids = list(dict.fromkeys(record_ids))
        from jvspatial.core.context import get_default_context

        # Object.find rewrites ``id`` into ``context.id``. Message identity is
        # the top-level record id, so the driver bulk read is the one query.
        raw = await get_default_context().database.find_many("object", unique_ids)
        found: dict[str, Any] = {}
        if isinstance(raw, dict):
            for record_id, record in raw.items():
                if not isinstance(record, dict):
                    continue
                if record.get("entity") not in (None, HarnessMessageRecord.__name__):
                    continue
                context = record.get("context") or {}
                if context.get("scope_key") != self._scope_key:
                    continue
                found[str(record_id)] = HarnessMessageRecord(
                    id=str(record.get("id") or record_id),
                    scope_key=str(context.get("scope_key") or ""),
                    content_hash=str(context.get("content_hash") or ""),
                    payload_ciphertext=str(context.get("payload_ciphertext") or ""),
                )
        if len(found) == len(unique_ids):
            return found
        # In-memory doubles keep rows on the model finder, not the driver.
        if found:
            return found
        records = await HarnessMessageRecord.find(
            {"id": {"$in": unique_ids}, "scope_key": self._scope_key}
        )
        return {record.id: record for record in records}

    async def _store_messages(self, parts: list[tuple[str, str]]) -> None:
        unique: dict[str, str] = {}
        for digest, text in parts:
            prior = unique.get(digest)
            if prior is not None and prior != text:
                raise HarnessPersistenceError(
                    "checkpoint message hash is already bound to different content"
                )
            unique[digest] = text
        if not unique:
            return
        pending = [
            (digest, self._message_record_id(digest), {"message": text})
            for digest, text in sorted(unique.items())
        ]
        stored = await self._find_messages([record_id for _, record_id, _ in pending])
        missing = []
        for digest, record_id, payload in pending:
            record = stored.get(record_id)
            if record is not None:
                self._assert_stored_message(record, digest=digest, payload=payload)
            else:
                missing.append((digest, record_id, payload))

        limiter = asyncio.Semaphore(_MESSAGE_INSERT_CONCURRENCY)

        async def insert(digest: str, record_id: str, payload: dict[str, str]) -> None:
            async with limiter:
                created_row, created = await HarnessMessageRecord.create_if_absent(
                    id=record_id,
                    scope_key=self._scope_key,
                    content_hash=digest,
                    payload_ciphertext=self._encrypt(payload, record_id=record_id),
                )
                if not created:
                    self._assert_stored_message(
                        created_row, digest=digest, payload=payload
                    )

        if missing:
            await asyncio.gather(
                *(
                    insert(digest, record_id, payload)
                    for digest, record_id, payload in missing
                )
            )

    def _same_checkpoint(
        self,
        prior: dict[str, Any],
        payload: dict[str, Any],
        canonical_texts: list[str],
    ) -> bool:
        fields = (
            "run_id",
            "step_index",
            "conversation_id",
            "parent_run_id",
            "agent_name",
            "timestamp",
            "state",
            "idempotency_key",
        )
        if any(prior.get(field) != payload.get(field) for field in fields):
            return False
        if "message_refs" in prior and "messages" not in prior:
            return prior.get("message_refs") == payload["message_refs"]
        inline = prior.get("messages")
        if not isinstance(inline, list):
            return False
        prior_texts = [
            json.dumps(message, sort_keys=True, separators=(",", ":"))
            for message in inline
        ]
        return prior_texts == canonical_texts

    async def save_snapshot(self, snapshot: ContinuableSnapshot) -> None:
        identity = snapshot.idempotency_key or str(uuid4())
        key = self._record_key("snapshot", f"{snapshot.run_id}:{identity}")
        record_id = _stable_id(HarnessSnapshotRecord, key)
        parts = self._canonical_message_parts(snapshot.messages)
        await self._store_messages(parts)
        payload = {
            "run_id": snapshot.run_id,
            "step_index": snapshot.step_index,
            "message_refs": [digest for digest, _text in parts],
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
        if not created and not self._same_checkpoint(
            self._decrypt(stored.payload_ciphertext, record_id=record_id),
            payload,
            [text for _digest, text in parts],
        ):
            raise HarnessPersistenceError(
                "checkpoint idempotency key is already bound to different content"
            )

    def _payload_refs(self, payload: dict[str, Any]) -> list[str]:
        if "message_refs" not in payload or "messages" in payload:
            return []
        refs = payload["message_refs"]
        if not isinstance(refs, list) or not all(
            isinstance(ref, str) and ref for ref in refs
        ):
            raise HarnessPersistenceError(
                "checkpoint message references are not canonical"
            )
        return refs

    async def _load_message_texts(self, refs: list[str]) -> dict[str, str]:
        unique = list(dict.fromkeys(refs))
        if not unique:
            return {}
        stored = await self._find_messages(
            [self._message_record_id(ref) for ref in unique]
        )
        texts: dict[str, str] = {}
        for ref in unique:
            record = stored.get(self._message_record_id(ref))
            if record is None:
                raise HarnessPersistenceError(
                    "checkpoint message scope does not match its record"
                )
            body = self._decrypt(record.payload_ciphertext, record_id=record.id)
            text = body.get("message") if isinstance(body, dict) else None
            if not isinstance(text, str):
                raise HarnessPersistenceError("checkpoint message payload is not text")
            if record.content_hash != ref or record.scope_key != self._scope_key:
                raise HarnessPersistenceError(
                    "checkpoint message scope does not match its record"
                )
            texts[ref] = text
        return texts

    def _messages_from_payload(
        self, payload: dict[str, Any], texts: dict[str, str]
    ) -> list[Any]:
        if "message_refs" in payload and "messages" not in payload:
            refs = self._payload_refs(payload)
            if not refs:
                return []
            return json.loads("[" + ",".join(texts[ref] for ref in refs) + "]")
        inline = payload.get("messages")
        if isinstance(inline, list):
            return inline
        raise HarnessPersistenceError("checkpoint has no message history")

    def _snapshot_from_payload(
        self, payload: dict[str, Any], texts: dict[str, str]
    ) -> ContinuableSnapshot:
        return ContinuableSnapshot(
            run_id=payload["run_id"],
            step_index=payload["step_index"],
            messages=ModelMessagesTypeAdapter.validate_python(
                self._messages_from_payload(payload, texts)
            ),
            conversation_id=payload.get("conversation_id"),
            parent_run_id=payload.get("parent_run_id"),
            agent_name=payload.get("agent_name"),
            timestamp=_datetime(payload["timestamp"]),
            state=payload["state"],
            idempotency_key=payload.get("idempotency_key"),
        )

    async def _decoded_snapshots(self, records: list[Any]) -> list[ContinuableSnapshot]:
        decoded = [
            self._decrypt(record.payload_ciphertext, record_id=record.id)
            for record in records
        ]
        refs = [ref for payload in decoded for ref in self._payload_refs(payload)]
        texts = await self._load_message_texts(refs)
        return [self._snapshot_from_payload(payload, texts) for payload in decoded]

    async def list_snapshots(
        self, *, run_id: str, include_interrupted: bool = False
    ) -> list[ContinuableSnapshot]:
        records = await self._find(HarnessSnapshotRecord, run_key=run_id)
        if not include_interrupted:
            records = [record for record in records if record.state == "complete"]
        snapshots = await self._decoded_snapshots(records)
        return sorted(snapshots, key=lambda item: (item.step_index, item.timestamp))

    async def latest_snapshot(
        self, *, run_id: str, include_interrupted: bool = False
    ) -> ContinuableSnapshot | None:
        from jvspatial.core.context import get_default_context

        context = get_default_context()
        query: dict[str, Any] = {
            "entity": HarnessSnapshotRecord.__name__,
            "context.scope_key": self._scope_key,
            "context.run_key": run_id,
        }
        if not include_interrupted:
            query["context.state"] = "complete"
        # Ordering matches list_snapshots: highest step, then latest timestamp.
        rows = await context.database.find(
            "object",
            query,
            sort=[("context.step_index", -1), ("context.occurred_at", -1)],
            limit=1,
        )
        if not rows:
            return None
        record = await context._deserialize_entity(HarnessSnapshotRecord, rows[0])
        if record is None:
            raise HarnessPersistenceError("checkpoint record could not be read")
        return (await self._decoded_snapshots([record]))[0]

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
