"""I-GRAPH-02 durable records for the work kernel."""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from jvspatial.core import Object
from jvspatial.core.annotations import attribute
from pydantic import Field


class WorkItem(Object):
    """Queue-shaped execution control record."""

    work_item_id: str = attribute(default="", indexed=True)
    kind: str = ""
    origin: str = ""
    principal_id: str = attribute(default="", indexed=True)
    workspace_id: str = attribute(default="", indexed=True)
    thread_id: str = ""
    app_id: str = ""
    definition_id: str = ""
    parent_work_item_id: str = ""
    causation_id: str = ""
    idempotency_key: str = attribute(default="", indexed=True)
    input_payload: Dict[str, Any] = Field(default_factory=dict)
    input_fingerprint: str = ""
    # Revision-bound continuation state belongs to the durable work authority,
    # never to chat or staging presentation state.
    plan_revision: str = ""
    plan: Dict[str, Any] = Field(default_factory=dict)
    dependency_work_item_ids: List[str] = Field(default_factory=list)
    precommit_draft: Dict[str, Any] = Field(default_factory=dict)
    remaining_obligations: List[Dict[str, Any]] = Field(default_factory=list)
    status: str = attribute(default="queued", indexed=True)
    attempt: int = 0
    retry_policy: Dict[str, Any] = Field(default_factory=dict)
    next_attempt_at: str = attribute(default="", indexed=True)
    deadline_at: str = ""
    cancel_requested_at: str = ""
    lease_owner: str = ""
    lease_token: str = ""
    lease_fence: int = 0
    lease_expires_at: str = attribute(default="", indexed=True)
    transition_seq: int = 0
    run_id: str = ""
    result_refs: List[str] = Field(default_factory=list)
    receipt_refs: List[str] = Field(default_factory=list)
    result_fingerprint: str = ""
    failure: Optional[Dict[str, Any]] = None
    created_at: str = ""
    updated_at: str = ""


class WorkOutboxEntry(Object):
    """At-least-once transition / trigger fact."""

    outbox_id: str = attribute(default="", indexed=True)
    work_item_id: str = attribute(default="", indexed=True)
    topic: str = attribute(default="", indexed=True)
    status: str = attribute(default="pending", indexed=True)
    payload: Dict[str, Any] = Field(default_factory=dict)
    run_id: str = ""
    causation_id: str = ""
    attempt: int = 0
    available_at: str = attribute(default="", indexed=True)
    deadline_at: str = ""
    lease_owner: str = ""
    lease_token: str = ""
    lease_expires_at: str = attribute(default="", indexed=True)
    created_at: str = ""
    updated_at: str = ""


class WorkApproval(Object):
    """Fail-closed approval authority separate from presentation cards."""

    work_approval_id: str = attribute(default="", indexed=True)
    work_item_id: str = attribute(default="", indexed=True)
    # Exact effective App contract reviewed by the human. This snapshot is
    # deliberately duplicated from WorkItem so an approval remains auditable
    # even when a later definition revision becomes active.
    definition_id: str = attribute(default="", indexed=True)
    status: str = attribute(default="pending", indexed=True)
    staging_token: str = attribute(default="", indexed=True)
    policy_approval_id: str = attribute(default="", indexed=True)
    run_id: str = ""
    run_step_id: str = ""
    authority_digest: str = ""
    decision: str = ""
    decider_id: str = ""
    expires_at: str = attribute(default="", indexed=True)
    decided_at: str = ""
    created_at: str = ""
    updated_at: str = ""


class EventTriggerDeclaration(Object):
    """Generic persisted ChangeEvent → work mapping."""

    trigger_key: str = attribute(default="", indexed=True)
    action: str = attribute(default="", indexed=True)
    resource_type: str = ""
    scope_prefix: str = ""
    capability_key: str = ""
    turn_template: str = ""
    enabled: bool = True
    created_at: str = ""
    updated_at: str = ""


class ChangeEventTriggerCheckpoint(Object):
    """Prime-DB cursor over logging-DB ChangeEvent DBLog rows."""

    consumer_name: str = attribute(default="", indexed=True)
    last_logged_at: str = ""
    last_event_id: str = ""
    updated_at: str = ""


__all__ = [
    "ChangeEventTriggerCheckpoint",
    "EventTriggerDeclaration",
    "WorkApproval",
    "WorkItem",
    "WorkOutboxEntry",
]
