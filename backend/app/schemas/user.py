"""User Pydantic schemas."""

from typing import Any, Dict, Optional

from pydantic import BaseModel, ConfigDict, EmailStr


class UserBase(BaseModel):
    """Base user schema."""

    email: EmailStr
    display_name: str


class UserCreate(UserBase):
    """Schema for creating a user."""

    password: str

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "email": "user@example.com",
                "display_name": "John Doe",
                "password": "securepassword123",
            }
        }
    )


class UserUpdate(BaseModel):
    """Schema for updating a user."""

    display_name: Optional[str] = None
    avatar_url: Optional[str] = None
    preferences: Optional[Dict[str, Any]] = None

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "display_name": "Jane Doe",
                "avatar_url": "https://example.com/avatar.jpg",
                "preferences": {"theme": "dark", "notifications": True},
            }
        }
    )


class UserResponse(UserBase):
    """Schema for user response."""

    id: str
    avatar_url: Optional[str] = None
    preferences: Dict[str, Any] = {}
    created_at: Optional[str] = None
    updated_at: Optional[str] = None

    model_config = ConfigDict(
        from_attributes=True,
        json_schema_extra={
            "example": {
                "id": "user_123",
                "email": "user@example.com",
                "display_name": "John Doe",
                "avatar_url": "https://example.com/avatar.jpg",
                "preferences": {},
                "created_at": "2024-01-01T00:00:00Z",
                "updated_at": "2024-01-02T00:00:00Z",
            }
        },
    )
