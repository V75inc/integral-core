"""Explicit consent to a displayed canonical invitation proposal."""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, StrictInt


class InvitationNotificationResponse(BaseModel):
    """No default action, coercion or client-supplied grant metadata."""

    model_config = ConfigDict(extra="forbid")
    action: Literal["accept", "decline"]
    invitation_revision: StrictInt = Field(ge=0)
