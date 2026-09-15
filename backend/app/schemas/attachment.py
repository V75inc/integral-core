"""Attachment Pydantic schemas."""

from typing import Optional

from pydantic import BaseModel, ConfigDict


class AttachmentResponse(BaseModel):
    """Legacy minimal projection — NOT the canonical attachment wire shape.

    The canonical shape is ``export_node(attachment)`` enriched by
    ``enrich_attachment_export`` (download/thumb/preview URLs, metadata,
    extracted-text summary). New readers — the FE entry view and the agent
    ``attachment_agent`` service — consume that enriched export. See
    ``docs/backend/attachment-agent-contract.md``. This model is retained only
    for back-compat re-export; do not extend routes to return it.
    """

    id: str
    filename: str
    mime_type: str
    size: int
    storage_key: str
    uploaded_by: str
    created_at: Optional[str] = None

    model_config = ConfigDict(
        from_attributes=True,
        json_schema_extra={
            "example": {
                "id": "att_123",
                "filename": "screenshot.png",
                "mime_type": "image/png",
                "size": 204800,
                "storage_key": "attachments/entry_123/screenshot.png",
                "uploaded_by": "user_123",
                "created_at": "2024-01-01T00:00:00Z",
            }
        },
    )
