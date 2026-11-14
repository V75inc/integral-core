"""Single-use email mailbox verification proofs (I-GRAPH-02 Object)."""

from __future__ import annotations

from typing import ClassVar, Optional

from jvspatial.core import Object

from app.models.indexing import attribute, compound_index


@compound_index(
    [("subject_entry_id", 1), ("workspace_id", 1), ("purpose", 1), ("verified_at", 1)],
    name="mailbox_verify_subject_workspace_purpose",
)
@compound_index(
    [("subject_entry_id", 1), ("workspace_id", 1), ("email_norm", 1)],
    name="mailbox_verify_subject_workspace_email",
)
class MailboxVerificationProof(Object):
    """Pre-account OTP and one-time proof for a scoped subject and email."""

    # Preserve the entity identity used by already-issued proofs.
    __entity_name__: ClassVar[Optional[str]] = "ApplicationMailboxVerification"

    subject_entry_id: str = attribute(default="", indexed=True)
    # Compatibility read for proof records created before the generic subject
    # field was introduced. New records write only subject_entry_id.
    candidate_entry_id: Optional[str] = attribute(default=None, indexed=True)
    workspace_id: str = attribute(default="", indexed=True)
    email_norm: str = attribute(default="", indexed=True)
    purpose: str = attribute(default="", indexed=True)
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
