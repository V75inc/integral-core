"""Pydantic schemas for API request/response validation."""

from app.schemas.apps import AppCreate, AppOut, AppResponse, AppUpdate
from app.schemas.attachment import AttachmentResponse
from app.schemas.comment import CommentCreate, CommentResponse, CommentUpdate
from app.schemas.common import (
    CursorPaginatedResponse,
    ManifestValidationResponse,
    MessageResponse,
    ValidationIssue,
)
from app.schemas.entry import EntryCreate, EntryResponse, EntryUpdate
from app.schemas.entry_type import EntryTypeCreate, EntryTypeResponse, EntryTypeUpdate
from app.schemas.notification import NotificationCreate, NotificationResponse
from app.schemas.tag import TagCreate, TagResponse, TagUpdate
from app.schemas.track import TrackCreate, TrackResponse, TrackUpdate
from app.schemas.user import UserCreate, UserResponse, UserUpdate
from app.schemas.view import ViewCreate, ViewResponse, ViewUpdate

__all__ = [
    "MessageResponse",
    "CursorPaginatedResponse",
    "ValidationIssue",
    "ManifestValidationResponse",
    "UserResponse",
    "UserCreate",
    "UserUpdate",
    "TrackResponse",
    "TrackCreate",
    "TrackUpdate",
    "AppResponse",
    "AppOut",
    "AppCreate",
    "AppUpdate",
    "EntryResponse",
    "EntryCreate",
    "EntryUpdate",
    "EntryTypeResponse",
    "EntryTypeCreate",
    "EntryTypeUpdate",
    "CommentResponse",
    "CommentCreate",
    "CommentUpdate",
    "TagResponse",
    "TagCreate",
    "TagUpdate",
    "ViewResponse",
    "ViewCreate",
    "ViewUpdate",
    "AttachmentResponse",
    "NotificationResponse",
    "NotificationCreate",
]
