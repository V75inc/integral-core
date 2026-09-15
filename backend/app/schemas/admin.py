"""Pydantic schemas for platform admin API."""

from __future__ import annotations

from typing import List, Optional

from pydantic import BaseModel, EmailStr, Field

from app.schemas.account_deletion import (
    AccountDeletionPreview,
    OrgMembershipSummary,
    PersonalWorkspaceSummary,
)


class AdminUserListItem(BaseModel):
    """Summary row for admin user directory."""

    id: str
    user_id: Optional[str] = None
    email: str = ""
    display_name: str = ""
    is_active: bool = True
    is_platform_admin: bool = False
    email_verified: bool = False
    created_at: Optional[str] = None
    last_accessed: Optional[str] = None


class AdminUserListResponse(BaseModel):
    """Paginated admin user directory."""

    users: List[AdminUserListItem]
    total: int
    page: int
    per_page: int
    total_pages: int
    has_previous: bool
    has_next: bool


class AdminUserDetail(BaseModel):
    """Full admin view of a user."""

    id: str
    user_id: Optional[str] = None
    email: str = ""
    display_name: str = ""
    avatar_url: str = ""
    is_active: bool = True
    is_platform_admin: bool = False
    roles: List[str] = Field(default_factory=list)
    email_verified: bool = False
    created_at: Optional[str] = None
    updated_at: Optional[str] = None
    last_accessed: Optional[str] = None
    org_memberships: List[OrgMembershipSummary] = Field(default_factory=list)
    personal_workspaces: List[PersonalWorkspaceSummary] = Field(default_factory=list)


class AdminUserUpdate(BaseModel):
    """Admin patch body for a user."""

    display_name: Optional[str] = None
    email_verified: Optional[bool] = None
    roles: Optional[List[str]] = None
    password: Optional[str] = None


class AdminUserCreate(BaseModel):
    """Admin create user with AuthUser + graph profile."""

    email: EmailStr
    password: str
    display_name: str


class AdminUserDeleteRequest(BaseModel):
    """Body for admin hard-delete."""

    confirm_email: str
    force: bool = False


class AdminUserDeleteResponse(BaseModel):
    """Successful admin hard-delete."""

    message: str
    deleted_user_id: str


class AdminOverview(BaseModel):
    """Platform-wide counts for admin dashboard."""

    total_users: int = 0
    active_users: int = 0
    inactive_users: int = 0
    platform_admins: int = 0
    personal_workspaces: int = 0
    organization_workspaces: int = 0
    total_apps: int = 0
    total_tracks: int = 0


class AdminOwnerSummary(BaseModel):
    """Resolved User node for admin owner display."""

    id: str
    display_name: str = ""
    email: str = ""


class AdminWorkspaceListItem(BaseModel):
    """Summary row for admin workspace directory."""

    id: str
    kind: str = ""
    name: str = ""
    workspace_type: str = ""
    member_count: int = 0
    app_count: int = 0
    track_count: int = 0
    created_at: Optional[str] = None
    owner: Optional[AdminOwnerSummary] = None


class AdminWorkspaceListResponse(BaseModel):
    """Paginated admin workspace directory."""

    workspaces: List[AdminWorkspaceListItem]
    total: int
    page: int
    per_page: int
    total_pages: int
    has_previous: bool
    has_next: bool


class AdminWorkspaceDetail(BaseModel):
    """Full admin view of a workspace."""

    id: str
    kind: str = ""
    name: str = ""
    workspace_type: str = ""
    description: str = ""
    member_count: int = 0
    app_count: int = 0
    track_count: int = 0
    created_at: Optional[str] = None
    updated_at: Optional[str] = None
    owner: Optional[AdminOwnerSummary] = None


class AdminWorkspaceUpdate(BaseModel):
    """Admin patch body for a workspace."""

    name: Optional[str] = None
    description: Optional[str] = None
    workspace_type: Optional[str] = None


class AdminResourceListItem(BaseModel):
    """Cross-workspace app or track summary."""

    id: str
    name: str = ""
    workspace_id: str = ""
    workspace_name: str = ""
    created_at: Optional[str] = None
    owner: Optional[AdminOwnerSummary] = None


class AdminResourceDetail(BaseModel):
    """Read-only admin view of an app or track."""

    id: str
    resource_type: str
    name: str = ""
    workspace_id: str = ""
    workspace_name: str = ""
    visibility: str = ""
    owner_id: Optional[str] = None
    owner: Optional[AdminOwnerSummary] = None
    created_at: Optional[str] = None
    updated_at: Optional[str] = None


class AdminResourceListResponse(BaseModel):
    """Paginated admin app/track directory."""

    items: List[AdminResourceListItem]
    total: int
    page: int
    per_page: int
    total_pages: int
    has_previous: bool
    has_next: bool


class AdminDeletionPreview(AccountDeletionPreview):
    """Admin deletion preview — same shape as self-service."""

    target_user_id: str = ""
    target_display_name: str = ""
    can_force_delete: bool = False


class AdminMemberListItem(BaseModel):
    """Workspace member row for admin roster."""

    id: str
    user_id: Optional[str] = None
    email: str = ""
    display_name: str = ""
    role: str = ""


class AdminMemberListResponse(BaseModel):
    """Paginated admin workspace member roster."""

    members: List[AdminMemberListItem]
    total: int
    page: int
    per_page: int
    total_pages: int
    has_previous: bool
    has_next: bool
