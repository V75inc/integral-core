"""Workspace member hire helpers (provision + onboarding prompt)."""

from __future__ import annotations

from typing import Optional

from pydantic import BaseModel, EmailStr, Field


class ProvisionWorkspaceMemberRequest(BaseModel):
    """Request body for ``POST /workspaces/{id}/members/provision``."""

    email: EmailStr
    display_name: str = Field(min_length=1)
    role: str = "member"
    send_credentials: bool = True
    welcome_message: Optional[str] = None
    credentials_email: Optional[EmailStr] = None


class SetMemberAssignedFormPromptRequest(BaseModel):
    """Request body for assigned-form prompt on a workspace member."""

    form_url: str = Field(min_length=1)
    send_email: bool = False
    recipient_email: Optional[EmailStr] = None
    recipient_name: Optional[str] = None
    form_title: Optional[str] = None


SetMemberOnboardingFormPromptRequest = SetMemberAssignedFormPromptRequest
