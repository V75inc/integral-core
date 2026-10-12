"""Typed lifecycle relationships; suspension is a retained deny, never a grant."""

from typing import Literal

from jvspatial.core import Edge, Node


class WorkspaceSuspension(Edge):
    """User to workspace retained block, independent of membership lifetime."""

    state: Literal["suspended", "resumed"]
    actor_id: str
    reason: str
    changed_at: str
    revision: int
    reviewed_access_digest: str = ""
    reviewed_revision: int = 0


WORKSPACE_SUSPENSION = WorkspaceSuspension


class ShareGrant(Edge):
    """ShareLink to recipient: independently revocable durable source."""

    role: Literal["admin", "editor", "commenter", "viewer"]
    workspace_id: str
    resource_type: Literal["app", "track", "entry"]
    resource_id: str
    state: Literal["active", "revoked"]
    redeemed_at: str
    changed_at: str
    revision: int
    membership_id: str


SHARE_GRANT = ShareGrant


class OwnershipProposal(Node):
    """Rooted consent proposal for one exact current ownership lifetime."""

    workspace_id: str
    resource_type: Literal["workspace", "app", "track"]
    resource_id: str
    issuer_id: str
    previous_owner_id: str
    recipient_id: str
    recipient_membership_id: str
    owner_edge_id: str
    expires_at: str
    created_at: str
    status: Literal["pending", "accepted", "declined", "revoked"] = "pending"
    revision: int = 0
    completed_at: str = ""


class HasOwnershipProposal(Edge):
    """Canonical resource to ownership-consent proposal."""


HAS_OWNERSHIP_PROPOSAL = HasOwnershipProposal


class OwnershipGrantHistory(Edge):
    """Retained User→resource ownership clock; never an access grant."""

    revision: int
    grant_id: str | None
    last_grant_id: str | None
    grant_fingerprint: str
    changed_at: str


OWNERSHIP_GRANT_HISTORY = OwnershipGrantHistory


class ResourceAccessBlock(Edge):
    """User to canonical subtree: absolute identified-audience lifecycle deny."""

    workspace_id: str
    resource_type: Literal["app", "track", "entry"]
    state: Literal["blocked", "released"]
    actor_id: str
    reason: str
    changed_at: str
    revision: int


RESOURCE_ACCESS_BLOCK = ResourceAccessBlock
