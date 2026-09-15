"""Entry Pydantic schemas."""

from typing import Any, Dict, List, Optional

from pydantic import BaseModel, ConfigDict


class EntryBase(BaseModel):
    """Base entry schema."""

    type_id: str = ""


class EntryCreate(EntryBase):
    """Schema for creating an entry."""

    track_id: str
    title: str = ""
    body: Optional[str] = None
    custom_fields: Optional[Dict[str, Any]] = None
    tags: Optional[List[str]] = None

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "track_id": "track_123",
                "type_id": "entry_type_456",
                "title": "Fix login bug",
                "body": "Users cannot log in when MFA is enabled.",
                "custom_fields": {"severity": "high", "assignee": "user_789"},
                "tags": ["tag_1", "tag_2"],
            }
        }
    )


class EntryUpdate(BaseModel):
    """Schema for updating an entry."""

    title: Optional[str] = None
    body: Optional[str] = None
    custom_fields: Optional[Dict[str, Any]] = None
    status: Optional[str] = None

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "title": "Updated task title",
                "body": "Updated description",
                "status": "completed",
                "custom_fields": {"severity": "medium"},
            }
        }
    )


class EntryResponse(EntryBase):
    """Schema for entry response."""

    id: str
    title: str = ""
    author_id: str
    body: Optional[str] = None
    track_id: Optional[str] = None
    tags: List[str] = []
    custom_fields: Dict[str, Any] = {}
    status: str = "active"
    reactions: Dict[str, List[str]] = {}
    created_at: Optional[str] = None
    updated_at: Optional[str] = None

    model_config = ConfigDict(
        from_attributes=True,
        json_schema_extra={
            "example": {
                "id": "entry_123",
                "type_id": "entry_type_456",
                "title": "Fix login bug",
                "author_id": "user_123",
                "tags": ["tag_1"],
                "custom_fields": {"severity": "high"},
                "status": "active",
                "created_at": "2024-01-01T00:00:00Z",
                "updated_at": "2024-01-02T00:00:00Z",
            }
        },
    )
