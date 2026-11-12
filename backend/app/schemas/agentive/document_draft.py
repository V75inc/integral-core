"""Public, owner-scoped document draft preview."""

from pydantic import BaseModel


class DocumentDraftPreview(BaseModel):
    """Text source for the exact requested draft revision."""

    title: str
    body: str
    format: str
    version: int
