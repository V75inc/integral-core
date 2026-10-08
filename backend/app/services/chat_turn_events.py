"""Tenant-scoped durable event sequence for WorkItem-backed native chat."""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from typing import Any, cast

from app.agentive.services.work_items import authorized_work_item_effect
from app.agentive.work_models import WorkItem
from app.models.harness_records import (
    HarnessChatEventCursor,
    HarnessChatEventRecord,
)
from app.schemas.agentive.chat_events import ChatEventReplayPage
from app.schemas.agentive.work import WorkExecutionContext
from app.services.credential_crypto import (
    CIPHER_PREFIX_V1,
    decrypt_secret_from_storage,
    encrypt_secret_for_storage,
)

_EVENT_LIMIT = 16_000
_PUBLIC_FIELDS: dict[str, tuple[str, ...]] = {
    "text-delta": ("delta",),
    "text-replace": ("content",),
    "message-boundary": (),
    "message-finish": ("timing",),
    "step": (
        "name",
        "status",
        "modelId",
        "provider",
        "requestId",
        "attempt",
        "outcome",
        "costSource",
        "usage",
        "providerCostUsd",
        "durationMs",
    ),
    "tool-call": ("toolCallId", "name", "status"),
    "source": ("title", "url"),
    "status": ("status", "text"),
    "error": ("code", "message"),
}
_TIMING_FIELDS = frozenset({"totalMs", "firstTokenMs"})
_USAGE_FIELDS = frozenset({"inputTokens", "outputTokens"})
_OBJECT_COLLECTION = "object"


def _identity(*, workspace_id: str, principal_id: str, thread_id: str) -> str:
    raw = "\0".join((workspace_id, principal_id, thread_id))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _record_id(*, work_item_id: str, event_key: str) -> str:
    value = f"{work_item_id}\0{event_key}".encode()
    digest = hashlib.sha256(value).hexdigest()
    return f"o.HarnessChatEventRecord.{digest}"


def _cursor_id(work_item_id: str) -> str:
    digest = hashlib.sha256(work_item_id.encode()).hexdigest()
    return f"o.HarnessChatEventCursor.{digest}"


def normalize_public_event(event: dict[str, Any]) -> dict[str, Any]:
    """Project events to the public chat contract, excluding raw parts."""
    event_type = str(event.get("type") or "")
    allowed = _PUBLIC_FIELDS.get(event_type)
    if allowed is None:
        raise ValueError("event type is not part of the public chat contract")
    normalized: dict[str, Any] = {"type": event_type}
    for key in allowed:
        value = event.get(key)
        if value is not None:
            if key in {"timing", "usage"}:
                if not isinstance(value, dict):
                    raise ValueError(f"public event {key} must be an object")
                fields = _TIMING_FIELDS if key == "timing" else _USAGE_FIELDS
                if set(value).difference(fields):
                    raise ValueError(f"unsupported {key} fields")
                if any(
                    not isinstance(item, (int, float))
                    or isinstance(item, bool)
                    or item < 0
                    for item in value.values()
                ):
                    raise ValueError(f"{key} values must be non-negative")
                if key == "usage" and any(
                    not isinstance(item, int) or isinstance(item, bool)
                    for item in value.values()
                ):
                    raise ValueError("token usage must be integral")
            elif event_type == "step" and key == "attempt":
                valid_attempt = False
                if isinstance(value, int) and not isinstance(value, bool):
                    valid_attempt = value >= 1
                if not valid_attempt:
                    raise ValueError("attempt must be a positive integer")
            elif event_type == "step" and key in {
                "providerCostUsd",
                "durationMs",
            }:
                if (
                    not isinstance(value, (int, float))
                    or isinstance(value, bool)
                    or value < 0
                ):
                    raise ValueError(f"{key} must be non-negative")
            elif not isinstance(value, str):
                raise ValueError("public event text fields must be strings")
            normalized[key] = value
    encoded = json.dumps(
        normalized,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    )
    if len(encoded.encode("utf-8")) > _EVENT_LIMIT:
        raise ValueError("public chat event exceeds its size limit")
    return normalized


def _decode(record: HarnessChatEventRecord) -> dict[str, Any]:
    if not record.payload_ciphertext.startswith(CIPHER_PREFIX_V1):
        raise ValueError("chat event payload is not encrypted")
    raw = decrypt_secret_from_storage(record.payload_ciphertext, aad=record.id)
    if not raw:
        raise ValueError("chat event payload could not be authenticated")
    value = json.loads(raw)
    if not isinstance(value, dict):
        raise ValueError("chat event payload has an invalid shape")
    return value


async def append_work_item_chat_event(
    *,
    context: WorkExecutionContext,
    event_key: str,
    event: dict[str, Any],
) -> dict[str, Any]:
    """Fence, sequence, encrypt, and commit one normalized event atomically."""
    key = event_key.strip()
    if not key or key != event_key:
        raise ValueError("event key must be canonical and non-empty")
    payload = normalize_public_event(event)
    identity = _identity(
        workspace_id=context.workspace_id,
        principal_id=context.principal_id,
        thread_id=context.thread_id,
    )
    cursor_id = _cursor_id(context.work_item_id)
    record_id = _record_id(work_item_id=context.work_item_id, event_key=key)

    async with authorized_work_item_effect(context) as graph:
        existing = cast(
            HarnessChatEventRecord | None,
            await HarnessChatEventRecord.get(record_id),
        )
        if existing is not None:
            if (
                existing.scope_key != identity
                or existing.work_item_id != context.work_item_id
                or existing.thread_id != context.thread_id
                or existing.event_key != key
                or _decode(existing) != payload
            ):
                raise ValueError("event key is bound to other content")
            return {"sequence": existing.sequence, **payload}

        now = datetime.now(timezone.utc).isoformat()
        cursor = cast(
            HarnessChatEventCursor | None,
            await HarnessChatEventCursor.get(cursor_id),
        )
        if cursor is None:
            await HarnessChatEventCursor.create_if_absent(
                id=cursor_id,
                scope_key=identity,
                work_item_id=context.work_item_id,
                thread_id=context.thread_id,
                last_sequence=0,
            )
            cursor = cast(
                HarnessChatEventCursor | None,
                await HarnessChatEventCursor.get(cursor_id),
            )
        if (
            cursor is None
            or cursor.scope_key != identity
            or cursor.work_item_id != context.work_item_id
            or cursor.thread_id != context.thread_id
        ):
            raise ValueError("chat event cursor scope mismatch")

        sequence = int(cursor.last_sequence) + 1
        raw = json.dumps(
            payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )
        ciphertext = encrypt_secret_for_storage(raw, aad=record_id)
        if not ciphertext.startswith(CIPHER_PREFIX_V1):
            raise RuntimeError("event encryption format is invalid")
        await HarnessChatEventRecord.create_if_absent(
            id=record_id,
            scope_key=identity,
            work_item_id=context.work_item_id,
            thread_id=context.thread_id,
            event_key=key,
            sequence=sequence,
            created_at=now,
            payload_ciphertext=ciphertext,
        )
        # The authorized WorkItem row lock serializes appends for this turn;
        # writing its cursor and event in this same transaction prevents gaps.
        cursor.last_sequence = sequence
        await cursor.save()
        _ = graph
    return {"sequence": sequence, **payload}


async def replay_work_item_chat_events(
    *,
    principal_id: str,
    workspace_id: str,
    thread_id: str,
    work_item_id: str,
    after_sequence: int = 0,
    limit: int = 200,
) -> ChatEventReplayPage:
    """Read a fresh committed page without long-lived graph entity caches."""
    from app.services.app_operations.transaction_scope import (
        postgres_graph_transaction,
    )

    # Each SSE poll needs current shared-store status/cursor, not the cached
    # running row from acceptance. The transaction provides an isolated graph
    # context and bypasses observable database cache decorators.
    async with postgres_graph_transaction():
        return await _read_chat_event_page(
            principal_id=principal_id,
            workspace_id=workspace_id,
            thread_id=thread_id,
            work_item_id=work_item_id,
            after_sequence=after_sequence,
            limit=limit,
        )


async def _read_chat_event_page(
    *,
    principal_id: str,
    workspace_id: str,
    thread_id: str,
    work_item_id: str,
    after_sequence: int,
    limit: int,
) -> ChatEventReplayPage:
    """Read events, status, and cursor within the exact chat scope."""
    if after_sequence < 0 or not 1 <= limit <= 500:
        raise ValueError("invalid chat event replay cursor or limit")
    identity = _identity(
        workspace_id=workspace_id,
        principal_id=principal_id,
        thread_id=thread_id,
    )
    canonical_work_item_id = work_item_id.removeprefix("o.WorkItem.")
    item_id = f"o.WorkItem.{canonical_work_item_id}"
    work_item = cast(WorkItem | None, await WorkItem.get(item_id))
    if (
        work_item is None
        or work_item.work_item_id != canonical_work_item_id
        or work_item.principal_id != principal_id
        or work_item.workspace_id != workspace_id
        or work_item.thread_id != thread_id
    ):
        raise ValueError("chat replay scope mismatch")
    cursor_id = _cursor_id(canonical_work_item_id)
    cursor = cast(
        HarnessChatEventCursor | None,
        await HarnessChatEventCursor.get(cursor_id),
    )
    committed_through = 0
    if cursor is not None:
        if (
            cursor.scope_key != identity
            or cursor.work_item_id != canonical_work_item_id
            or cursor.thread_id != thread_id
        ):
            raise ValueError("chat event cursor scope mismatch")
        committed_through = int(cursor.last_sequence)
    records = cast(
        list[HarnessChatEventRecord],
        await HarnessChatEventRecord.find(
            {
                "scope_key": identity,
                "work_item_id": canonical_work_item_id,
                "thread_id": thread_id,
            }
        ),
    )
    records.sort(
        key=lambda record: record.sequence,
    )
    remaining = [r for r in records if r.sequence > after_sequence]
    gap = after_sequence > committed_through
    expected_sequence = after_sequence + 1
    for record in remaining:
        if record.sequence != expected_sequence:
            gap = True
        expected_sequence = record.sequence + 1
    if expected_sequence <= committed_through:
        gap = True
    page = remaining[:limit]
    events = [{"sequence": r.sequence, **_decode(r)} for r in page]
    return ChatEventReplayPage(
        events=events,
        committed_through=committed_through,
        next_after_sequence=(page[-1].sequence if page else after_sequence),
        has_more=len(remaining) > limit,
        gap=gap,
        work_status=work_item.status,
    )


__all__ = [
    "append_work_item_chat_event",
    "normalize_public_event",
    "replay_work_item_chat_events",
]
