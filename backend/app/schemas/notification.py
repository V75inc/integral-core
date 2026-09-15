"""Notification Pydantic schemas."""

from typing import Any, Dict, Optional

from pydantic import BaseModel, ConfigDict


class NotificationCreate(BaseModel):
    target_user_id: str
    type: str = "system"  # invite, update, system
    content: str
    action_url: Optional[str] = None
    metadata: Optional[Dict[str, Any]] = None

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "target_user_id": "user_456",
                "type": "invite",
                "content": "You have been invited to collaborate on 'Q4 Launch'.",
                "action_url": "/tracks/track_123",
                "metadata": {"track_id": "track_123"},
            }
        }
    )


class NotificationResponse(BaseModel):
    id: str
    user_id: str
    type: str
    content: str
    read: bool = False
    action_url: Optional[str] = None
    metadata: Dict[str, Any] = {}
    created_at: Optional[str] = None

    model_config = ConfigDict(
        from_attributes=True,
        json_schema_extra={
            "example": {
                "id": "notif_123",
                "user_id": "user_123",
                "type": "invite",
                "content": "You have been invited.",
                "read": False,
                "created_at": "2024-01-01T00:00:00Z",
            }
        },
    )
