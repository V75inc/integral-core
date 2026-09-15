"""Comment Pydantic schemas."""

from typing import Optional

from pydantic import BaseModel, ConfigDict


class CommentCreate(BaseModel):
    text: str
    parent_id: Optional[str] = None

    model_config = ConfigDict(
        json_schema_extra={
            "example": {"text": "Great point, I agree.", "parent_id": None}
        }
    )


class CommentUpdate(BaseModel):
    text: str

    model_config = ConfigDict(
        json_schema_extra={"example": {"text": "Updated comment text."}}
    )


class CommentResponse(BaseModel):
    id: str
    author_id: str
    text: str
    parent_id: Optional[str] = None
    created_at: Optional[str] = None
    updated_at: Optional[str] = None

    model_config = ConfigDict(
        from_attributes=True,
        json_schema_extra={
            "example": {
                "id": "comment_123",
                "author_id": "user_123",
                "text": "Great point.",
                "parent_id": None,
                "created_at": "2024-01-01T00:00:00Z",
                "updated_at": "2024-01-01T00:00:00Z",
            }
        },
    )
