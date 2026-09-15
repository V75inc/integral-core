"""EntryType Pydantic schemas."""

from typing import Any, Dict, Optional

from pydantic import BaseModel, ConfigDict


class EntryTypeCreate(BaseModel):
    name: str
    icon: str = "document"
    form_schema: Optional[Dict[str, Any]] = None
    track_id: str

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "name": "Bug Report",
                "icon": "🐛",
                "track_id": "track_123",
                "form_schema": {
                    "fields": [
                        {
                            "name": "severity",
                            "label": "Severity",
                            "type": "select",
                            "required": True,
                            "options": [
                                {"value": "low", "label": "Low"},
                                {"value": "medium", "label": "Medium"},
                                {"value": "high", "label": "High"},
                            ],
                            "default": "medium",
                        }
                    ]
                },
            }
        }
    )


class EntryTypeUpdate(BaseModel):
    name: Optional[str] = None
    icon: Optional[str] = None
    form_schema: Optional[Dict[str, Any]] = None

    model_config = ConfigDict(json_schema_extra={"example": {"icon": "🔧"}})


class EntryTypeResponse(BaseModel):
    id: str
    name: str
    icon: str
    form_schema: Dict[str, Any] = {}
    track_id: str
    created_at: Optional[str] = None
    updated_at: Optional[str] = None

    model_config = ConfigDict(
        from_attributes=True,
        json_schema_extra={
            "example": {
                "id": "et_123",
                "name": "Bug Report",
                "icon": "🐛",
                "form_schema": {},
                "track_id": "track_123",
                "created_at": "2024-01-01T00:00:00Z",
                "updated_at": "2024-01-02T00:00:00Z",
            }
        },
    )
