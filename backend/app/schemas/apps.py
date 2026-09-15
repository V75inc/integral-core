"""App Pydantic schemas (formerly Space — renamed for App Bundles v1)."""

from typing import Optional

from pydantic import BaseModel, ConfigDict


class AppCreate(BaseModel):
    name: str
    description: Optional[str] = None
    workspace_id: Optional[str] = None
    accent_color: Optional[str] = None

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "name": "Q4 Initiatives",
                "description": "All Q4 tracks in one place.",
                "accent_color": "#ff5a1f",
            }
        }
    )


class AppUpdate(BaseModel):
    name: Optional[str] = None
    description: Optional[str] = None
    accent_color: Optional[str] = None

    model_config = ConfigDict(
        json_schema_extra={
            "example": {"name": "Updated Name", "accent_color": "#3a7afe"}
        }
    )


class AppResponse(BaseModel):
    id: str
    name: str
    owner_user_id: Optional[str] = None
    description: Optional[str] = None
    workspace_id: Optional[str] = None
    accent_color: Optional[str] = None
    created_at: Optional[str] = None
    updated_at: Optional[str] = None

    model_config = ConfigDict(
        from_attributes=True,
        json_schema_extra={
            "example": {
                "id": "sp_123",
                "name": "Q4 Initiatives",
                "owner_user_id": "user_123",
                "description": "All Q4 tracks.",
                "accent_color": "#ff5a1f",
                "created_at": "2024-01-01T00:00:00Z",
                "updated_at": "2024-01-02T00:00:00Z",
            }
        },
    )


# Alias for plan-spec compatibility — `AppOut` per migration plan / 10-02 frontmatter
AppOut = AppResponse
