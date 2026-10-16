"""Workspace member hire helpers (provision + onboarding prompt)."""

from __future__ import annotations

from typing import Annotated, Literal, Optional

from pydantic import BaseModel, ConfigDict, EmailStr, Field, Strict, StrictStr


class ProvisionWorkspaceMemberRequest(BaseModel):
    """Request body for ``POST /workspaces/{id}/members/provision``."""

    model_config = ConfigDict(extra="forbid")

    email: EmailStr
    display_name: StrictStr = Field(min_length=1)
    role: Literal["admin", "member", "guest"] = "member"
    send_credentials: Annotated[bool, Strict()] = True
    welcome_message: Optional[StrictStr] = None
    credentials_email: Optional[EmailStr] = None


class SetMemberAssignedFormPromptRequest(BaseModel):
    """Request body for assigned-form prompt on a workspace member."""

    form_url: str = Field(min_length=1)
    send_email: bool = False
    recipient_email: Optional[EmailStr] = None
    recipient_name: Optional[str] = None
    form_title: Optional[str] = None


SetMemberOnboardingFormPromptRequest = SetMemberAssignedFormPromptRequest
