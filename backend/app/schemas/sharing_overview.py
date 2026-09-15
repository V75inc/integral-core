"""Phase 8 Plan 08-05 — caller-scoped sharing aggregator response.

Per A2, this surface indexes the caller's OUTBOUND share state across all
owned resources; it does NOT duplicate per-resource controls (those live on
App/Track/Entry detail pages via ManageAccessModal).

Schema mirror for ``GET /api/me/sharing-overview``. Three buckets:

* ``share_links``  — every ShareLink the caller has minted that is still
                     active (not revoked, not expired implicit via service).
* ``exclusions``   — every EXCLUDED_FROM edge sourced from a resource the
                     caller owns.
* ``invitations``  — every pending resource-level Invitation the caller has
                     created (workspace-targeted invitations are deliberately
                     excluded — they have a separate surface under Settings ->
                     Workspace Members).
"""

from __future__ import annotations

from typing import List, Optional

from pydantic import BaseModel


class OutboundShareLink(BaseModel):
    id: str
    resource_type: str  # "app" | "track" | "entry"
    resource_id: str
    resource_label: str
    role: str
    created_at: Optional[str] = None
    expires_at: Optional[str] = None
    redemptions: int = 0
    model_config = {"extra": "forbid"}


class OutboundExclusion(BaseModel):
    resource_type: str
    resource_id: str
    resource_label: str
    excluded_user_id: str
    excluded_user_display: Optional[str] = None
    reason: Optional[str] = None
    created_at: Optional[str] = None
    model_config = {"extra": "forbid"}


class OutboundInvitation(BaseModel):
    id: str
    resource_type: str  # "app" | "track" | "entry"
    resource_id: str
    resource_label: str
    invitee_email: Optional[str] = None
    invitee_user_id: Optional[str] = None
    role: str
    status: str
    created_at: Optional[str] = None
    expires_at: Optional[str] = None
    model_config = {"extra": "forbid"}


class SharingOverviewResponse(BaseModel):
    share_links: List[OutboundShareLink]
    exclusions: List[OutboundExclusion]
    invitations: List[OutboundInvitation]
    model_config = {"extra": "forbid"}
