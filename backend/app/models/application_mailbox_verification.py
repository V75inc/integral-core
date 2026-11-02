"""Application-stage mailbox verification proof (I-GRAPH-02 Object)."""

from __future__ import annotations

from typing import ClassVar, Optional

from jvspatial.core import Object
from jvspatial.core.annotations import attribute, compound_index


@compound_index(
    [("candidate_entry_id", 1), ("purpose", 1), ("verified_at", 1)],
    name="app_mailbox_verify_candidate_purpose",
)
@compound_index(
    [("candidate_entry_id", 1), ("email_norm", 1)],
    name="app_mailbox_verify_candidate_email",
)
class ApplicationMailboxVerification(Object):
    """Pre-account OTP and hire-time consumable proof for one applicant inbox."""

    __entity_name__: ClassVar[Optional[str]] = "ApplicationMailboxVerification"

    candidate_entry_id: str = attribute(default="", indexed=True)
    workspace_id: str = attribute(default="", indexed=True)
    email_norm: str = attribute(default="", indexed=True)
    purpose: str = attribute(default="recruitment_application", indexed=True)
    otp_hash: str = ""
    otp_expires_at: str = ""
    otp_attempts: int = 0
    verified_at: Optional[str] = attribute(default=None)
    proof_expires_at: Optional[str] = None
    invalidated_at: Optional[str] = attribute(default=None)
    consumed_at: Optional[str] = attribute(default=None)
    consumed_for_user_id: Optional[str] = attribute(default=None)
    created_at: Optional[str] = None
    updated_at: Optional[str] = None
