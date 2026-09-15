"""Track Pydantic schemas."""

from typing import Optional

from pydantic import BaseModel, ConfigDict


class TrackBase(BaseModel):
    """Base track schema."""

    title: str = ""
    visibility: str = "private"


class TrackCreate(TrackBase):
    """Schema for creating a track."""

    purpose: Optional[str] = None
    icon: Optional[str] = None
    template_id: Optional[str] = None

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "title": "Q4 Product Launch",
                "purpose": "Track everything related to the Q4 product launch.",
                "icon": "🚀",
                "visibility": "private",
            }
        }
    )


class TrackUpdate(BaseModel):
    """Schema for updating a track."""

    title: Optional[str] = None
    purpose: Optional[str] = None
    icon: Optional[str] = None
    visibility: Optional[str] = None

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "title": "Updated Project Name",
                "visibility": "public",
            }
        }
    )


class TrackMessageResponse(BaseModel):
    """Wrapper for track create/update responses."""

    track: dict
    message: str


class TrackResponse(TrackBase):
    """Schema for track response."""

    id: str
    owner_id: str
    purpose: Optional[str] = None
    icon: Optional[str] = None
    template_id: Optional[str] = None
    created_at: Optional[str] = None
    updated_at: Optional[str] = None

    model_config = ConfigDict(
        from_attributes=True,
        json_schema_extra={
            "example": {
                "id": "track_123",
                "title": "Q4 Product Launch",
                "owner_id": "user_123",
                "purpose": "Track everything related to the Q4 product launch.",
                "icon": "🚀",
                "visibility": "private",
                "created_at": "2024-01-01T00:00:00Z",
                "updated_at": "2024-01-02T00:00:00Z",
            }
        },
    )
