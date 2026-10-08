"""Durable coordination facts under I-GRAPH-02, owned by the work kernel."""

from typing import Any, Literal

from jvspatial.core import Object
from jvspatial.core.annotations import attribute
from pydantic import Field


class WorkBudgetReservation(Object):
    reservation_id: str = attribute(default="", indexed=True)
    root_work_item_id: str = attribute(default="", indexed=True)
    work_item_id: str = ""
    principal_id: str = ""
    workspace_id: str = ""
    thread_id: str = ""
    review_digest: str = ""
    request_fingerprint: str = ""
    request: dict[str, Any] = Field(default_factory=dict)
    status: Literal["reserved", "settled", "overrun", "released"] = "reserved"
    # Missing fields in old records mean unknown dispatch history, never proof
    # that a provider was not called.
    dispatch_state: Literal[
        "legacy_untracked",
        "not_started",
        "intent",
        "unknown",
        "completed",
        "not_dispatched",
    ] = "legacy_untracked"
    dispatch_ref: str = ""
    dispatched_at: str = ""
    outcome_ref: str = ""
    upper_units: int = Field(default=0, ge=0)
    charged_units: int = Field(default=0, ge=0)
    receipt_ref: str = ""
    created_at: str = ""
    settled_at: str = ""
