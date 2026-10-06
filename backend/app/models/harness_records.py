"""Encrypted, append-only records for native Harness persistence.

These are I-GRAPH-02 Objects: execution logs and checkpoints are queried by
stable scalar keys and do not participate in graph traversal or permissions.
"""

from __future__ import annotations

from jvspatial.core import Object
from jvspatial.core.annotations import attribute


class HarnessRunRecord(Object):
    """Durable run registration metadata."""

    scope_key: str = attribute(default="", indexed=True)
    run_key: str = attribute(default="", indexed=True)
    conversation_key: str = attribute(default="", indexed=True)
    parent_run_key: str = attribute(default="", indexed=True)
    payload_ciphertext: str = ""


class HarnessEventRecord(Object):
    """Append-only execution boundary event."""

    scope_key: str = attribute(default="", indexed=True)
    run_key: str = attribute(default="", indexed=True)
    record_key: str = attribute(default="", indexed=True)
    occurred_at: str = attribute(default="", indexed=True)
    step_index: int = 0
    payload_ciphertext: str = ""


class HarnessSnapshotRecord(Object):
    """Encrypted Pydantic AI message history checkpoint."""

    scope_key: str = attribute(default="", indexed=True)
    run_key: str = attribute(default="", indexed=True)
    record_key: str = attribute(default="", indexed=True)
    occurred_at: str = attribute(default="", indexed=True)
    step_index: int = 0
    state: str = attribute(default="complete", indexed=True)
    payload_ciphertext: str = ""


class HarnessCheckpointManifestRecord(Object):
    """Encrypted Core recovery manifest for one framework snapshot."""

    scope_key: str = attribute(default="", indexed=True)
    run_key: str = attribute(default="", indexed=True)
    checkpoint_key: str = attribute(default="", indexed=True)
    created_at: str = attribute(default="", indexed=True)
    schema_version: int = attribute(default=1, indexed=True)
    payload_ciphertext: str = ""


class HarnessToolEffectRecord(Object):
    """Append-only tool-effect lifecycle transition."""

    scope_key: str = attribute(default="", indexed=True)
    run_key: str = attribute(default="", indexed=True)
    tool_call_key: str = attribute(default="", indexed=True)
    occurred_at: str = attribute(default="", indexed=True)
    status: str = attribute(default="started", indexed=True)
    payload_ciphertext: str = ""


class HarnessPlanState(Object):
    """Encrypted PlanStore state scoped to one Integral Harness session."""

    scope_key: str = attribute(default="", indexed=True)
    revision: int = 0
    payload_ciphertext: str = ""


class HarnessModelRequestRecord(Object):
    """Append-only intent/outcome observation for one physical model call."""

    scope_key: str = attribute(default="", indexed=True)
    run_key: str = attribute(default="", indexed=True)
    request_key: str = attribute(default="", indexed=True)
    transition_key: str = attribute(default="", indexed=True)
    occurred_at: str = attribute(default="", indexed=True)
    outcome: str = attribute(default="dispatched", indexed=True)
    payload_ciphertext: str = ""


class HarnessTurnInputRecord(Object):
    """Encrypted server-authorized context for one durable native chat turn."""

    scope_key: str = attribute(default="", indexed=True)
    principal_id: str = attribute(default="", indexed=True)
    workspace_id: str = attribute(default="", indexed=True)
    thread_id: str = attribute(default="", indexed=True)
    work_item_id: str = attribute(default="", indexed=True)
    accepted_message_id: str = attribute(default="", indexed=True)
    client_request_id: str = attribute(default="", indexed=True)
    created_at: str = attribute(default="", indexed=True)
    expires_at: str = attribute(default="", indexed=True)
    schema_version: int = attribute(default=1, indexed=True)
    payload_digest: str = ""
    payload_ciphertext: str = ""


class HarnessTurnAdmissionSlot(Object):
    """Shared per-principal permit reserved for one interactive native turn."""

    principal_id: str = attribute(default="", indexed=True)
    slot_index: int = 0
    workspace_id: str = attribute(default="", indexed=True)
    thread_id: str = attribute(default="", indexed=True)
    work_item_id: str = attribute(default="", indexed=True)
    acquired_at: str = ""
    updated_at: str = ""


class HarnessChatEventCursor(Object):
    """Per-turn sequence head; updated in the same fenced transaction as append."""

    scope_key: str = attribute(default="", indexed=True)
    work_item_id: str = attribute(default="", indexed=True)
    thread_id: str = attribute(default="", indexed=True)
    last_sequence: int = 0


class HarnessChatEventRecord(Object):
    """Encrypted normalized event committed before it becomes replayable."""

    scope_key: str = attribute(default="", indexed=True)
    work_item_id: str = attribute(default="", indexed=True)
    thread_id: str = attribute(default="", indexed=True)
    event_key: str = attribute(default="", indexed=True)
    sequence: int = attribute(default=0, indexed=True)
    created_at: str = attribute(default="", indexed=True)
    payload_ciphertext: str = ""
