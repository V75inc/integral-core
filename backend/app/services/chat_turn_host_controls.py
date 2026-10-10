"""Capture and recheck host-only chat continuations without user utterances."""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone

from app.models.nodes import ChatThread
from app.schemas.agentive.work import ChatTurnHostControl, WorkError


async def capture_chat_host_control(
    *, thread: ChatThread, principal_id: str, action: str
) -> ChatTurnHostControl:
    """Derive authority from current stored state, never client prose."""
    if thread.user_id != principal_id or thread.provider_id != "integral_native":
        raise WorkError("work.policy_denied", "host continuation scope mismatch")
    read_only = False
    if action == "prompt_sheet_resume":
        from app.services.prompt_queue import QUEUE_STATUS_CLOSED, get_queue

        source = get_queue(thread)
        if (
            source["status"] != QUEUE_STATUS_CLOSED
            or not source["closed_at"]
            or not source["items"]
            or any(
                item.get("status")
                not in {"approved", "rejected", "answered", "skipped", "cancelled"}
                for item in source["items"]
            )
        ):
            raise WorkError("work.policy_denied", "host continuation is not resolved")
        read_only = not any(
            item.get("status") == "approved" for item in source["items"]
        )
    elif action == "staging_follow_through":
        from app.agentive.staging_store import StagedChangeRecord

        # The staging convenience API hydrates a process-local cache and treats
        # persistence as best effort. Durable acceptance instead reads the
        # authoritative rows and lets storage failures abort its transaction.
        records = await StagedChangeRecord.find(
            {
                "user_id": principal_id,
                "workspace_id": thread.workspace_id,
                "session_id": thread.id,
                "state": "blessed",
            }
        )
        source = []
        for record in sorted(records, key=lambda item: item.id):
            if record.kind == "design_proposal":
                continue
            try:
                expiry = datetime.fromisoformat(
                    record.expires_at.replace("Z", "+00:00")
                )
                if expiry.tzinfo is None or expiry <= datetime.now(timezone.utc):
                    raise ValueError("expired")
            except (TypeError, ValueError):
                raise WorkError(
                    "work.policy_denied", "approved change is unavailable"
                ) from None
            if not record.blessed_at:
                raise WorkError(
                    "work.policy_denied", "approved change lacks a decision"
                )
            source.append(record.model_dump(mode="json"))
        if not source:
            raise WorkError(
                "work.policy_denied", "no approved change awaits continuation"
            )
    else:
        raise WorkError("work.policy_denied", "unsupported host continuation")
    digest = hashlib.sha256(
        json.dumps(
            source,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
            allow_nan=False,
        ).encode("utf-8")
    ).hexdigest()
    return ChatTurnHostControl(action=action, source_digest=digest, read_only=read_only)


async def assert_chat_host_control(
    *, thread: ChatThread, principal_id: str, expected: ChatTurnHostControl | None
) -> None:
    """Fail closed if a queued host continuation's source has changed."""
    if expected is None:
        return
    current = await capture_chat_host_control(
        thread=thread, principal_id=principal_id, action=expected.action
    )
    if current != expected:
        raise WorkError("work.policy_denied", "accepted host continuation changed")
