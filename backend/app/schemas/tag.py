"""Tag Pydantic schemas."""

from typing import Optional

from pydantic import BaseModel, ConfigDict


class TagCreate(BaseModel):
    name: str
    color: str = "#6B7280"
    track_id: str

    model_config = ConfigDict(
        json_schema_extra={
            "example": {"name": "urgent", "color": "#EF4444", "track_id": "track_123"}
        }
    )


class TagUpdate(BaseModel):
    name: Optional[str] = None
    color: Optional[str] = None

    model_config = ConfigDict(json_schema_extra={"example": {"color": "#10B981"}})


class TagResponse(BaseModel):
    id: str
    name: str
    color: str
    track_id: str
    created_at: Optional[str] = None

    model_config = ConfigDict(
        from_attributes=True,
        json_schema_extra={
            "example": {
                "id": "tag_123",
                "name": "urgent",
                "color": "#EF4444",
                "track_id": "track_123",
                "created_at": "2024-01-01T00:00:00Z",
            }
        },
    )
