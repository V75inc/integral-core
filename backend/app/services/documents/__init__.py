"""Document generation services (entry-backed Document Templates app)."""

from app.services.documents.field_registry import (
    get_workspace_field_index,
    list_document_contexts,
    list_document_fields,
    rebuild_workspace_field_index,
)

__all__ = [
    "get_workspace_field_index",
    "list_document_contexts",
    "list_document_fields",
    "rebuild_workspace_field_index",
]
