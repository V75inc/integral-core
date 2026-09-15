"""Common Pydantic schemas."""

from typing import Generic, List, Optional, TypeVar

from pydantic import BaseModel, ConfigDict

T = TypeVar("T")


class MessageResponse(BaseModel):
    """Standard message response."""

    message: str

    model_config = ConfigDict(
        json_schema_extra={"example": {"message": "Operation completed successfully"}}
    )


class CursorPaginatedResponse(BaseModel, Generic[T]):
    """Cursor-based paginated response per architecture Section 10.3."""

    items: List[T]
    next_cursor: Optional[str] = None
    has_more: bool = False
    total: Optional[int] = None

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "items": [],
                "next_cursor": "eyJpZCI6ICJ4eXoiLCAidHMiOiAiMjAyNC0wMSJ9",
                "has_more": True,
                "total": 200,
            }
        }
    )


class ValidationIssue(BaseModel):
    """Structured validation issue for manifest/agent feedback loops."""

    code: str
    message: str
    path: Optional[str] = None


class ManifestValidationResponse(BaseModel):
    """Response contract for manifest validation endpoints."""

    valid: bool
    canonical_manifest: Optional[dict] = None
    issues: List[ValidationIssue] = []
