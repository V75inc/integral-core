"""Safe credential / activation delivery responses for workspace admins."""

from __future__ import annotations

from typing import Literal, Optional

from pydantic import BaseModel, ConfigDict, StrictStr

ActivationDeliveryState = Literal[
    "sent",
    "pending_retry",
    "suppressed",
    "expired",
    "already_released",
    "not_pending",
    "sending",
]


class ActivationDeliveryStatusResponse(BaseModel):
    """Public activation delivery snapshot — no secrets or provider payloads."""

    model_config = ConfigDict(extra="forbid")

    state: ActivationDeliveryState
    recipient_email: Optional[StrictStr] = None
    intent_id: Optional[StrictStr] = None
    send_attempt_count: int = 0
    last_attempt_at: Optional[StrictStr] = None
    provider_message_id: Optional[StrictStr] = None
