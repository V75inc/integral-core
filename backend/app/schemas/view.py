"""View Pydantic schemas."""

from typing import Any, Dict, List, Optional

from pydantic import BaseModel, ConfigDict


class ViewCreate(BaseModel):
    name: str
    type: str = "feed"  # kanban, table, calendar, gallery, feed
    config: Optional[Dict[str, Any]] = None
    is_default: bool = False
    # Slug list (manifest-stable keys) restricting which entry types this
    # view surfaces. Empty = no constraint.
    entry_type_keys: Optional[List[str]] = None
    # Slug pre-selected when "New entry" is invoked from inside this view.
    default_entry_type_key: Optional[str] = None

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "name": "Dev Board",
                "type": "kanban",
                "config": {
                    "columns": [
                        {"id": "todo", "title": "To Do", "filter": {"status": "todo"}},
                        {
                            "id": "doing",
                            "title": "In Progress",
                            "filter": {"status": "doing"},
                        },
                        {"id": "done", "title": "Done", "filter": {"status": "done"}},
                    ]
                },
                "is_default": True,
            }
        }
    )


class ViewUpdate(BaseModel):
    name: Optional[str] = None
    type: Optional[str] = None
    config: Optional[Dict[str, Any]] = None
    is_default: Optional[bool] = None
    entry_type_keys: Optional[List[str]] = None
    default_entry_type_key: Optional[str] = None

    model_config = ConfigDict(
        json_schema_extra={
            "example": {"name": "Updated Board Name", "is_default": True}
        }
    )


class ViewResponse(BaseModel):
    id: str
    name: str
    type: str
    config: Dict[str, Any] = {}
    track_id: str
    is_default: bool = False
    entry_type_keys: List[str] = []
    default_entry_type_key: str = ""
    created_by: Optional[str] = None
    created_at: Optional[str] = None
    updated_at: Optional[str] = None

    model_config = ConfigDict(
        from_attributes=True,
        json_schema_extra={
            "example": {
                "id": "view_123",
                "name": "Dev Board",
                "type": "kanban",
                "config": {},
                "track_id": "track_123",
                "is_default": True,
                "created_by": "user_123",
                "created_at": "2024-01-01T00:00:00Z",
                "updated_at": "2024-01-02T00:00:00Z",
            }
        },
    )
