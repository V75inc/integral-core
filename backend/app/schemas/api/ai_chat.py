"""Schemas for backend/app/api/ai_chat.py — chat thread + message bodies."""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field, field_validator, model_validator

from app.schemas.chat_entity_refs import EntityRef

# Image attachments the user drops in chat for the agent to SEE (vision).
# Passed inline as base64 into the turn's ``visitor.data["image_urls"]``; the
# orchestrator vision reflex handles them. Non-image files use a separate
# persisted-attachment path (not this schema).
ALLOWED_IMAGE_TYPES = {"image/png", "image/jpeg", "image/webp", "image/gif"}
MAX_IMAGES_PER_MESSAGE = 4
# Cap the base64 string length (~5 MB decoded; base64 inflates ~1.33x).
MAX_IMAGE_B64_LEN = 7_000_000


class ChatImage(BaseModel):
    """One inline image on a chat turn (base64, for vision)."""

    data: str = Field(..., min_length=1, max_length=MAX_IMAGE_B64_LEN)
    content_type: str
    model_config = {"extra": "forbid"}

    @field_validator("data")
    @classmethod
    def _strip_data_url_prefix(cls, v: str) -> str:
        # Tolerate a ``data:image/png;base64,<...>`` URL by keeping only the
        # base64 payload — the vision path wants raw base64.
        if v.startswith("data:"):
            _, _, b64 = v.partition(",")
            return b64 or v
        return v

    @field_validator("content_type")
    @classmethod
    def _valid_image_type(cls, v: str) -> str:
        if v not in ALLOWED_IMAGE_TYPES:
            raise ValueError(
                f"unsupported image type {v!r}; allowed: "
                f"{sorted(ALLOWED_IMAGE_TYPES)}"
            )
        return v


class CreateThreadRequest(BaseModel):
    """Body for POST /api/chat/threads — create a thread bound to a provider."""

    provider_id: str = Field(..., min_length=1, max_length=64)
    title: Optional[str] = Field(default=None, max_length=200)
    model_config = {"extra": "forbid"}


class RenameThreadRequest(BaseModel):
    """Body for PATCH /api/chat/threads/{id} — rename a thread."""

    title: str = Field(..., min_length=1, max_length=200)
    model_config = {"extra": "forbid"}


MAX_ATTACHMENTS_PER_MESSAGE = 10
MAX_VISIBLE_CONTEXT_ITEMS = 25


class PageContextBreadcrumb(BaseModel):
    label: str = Field(..., min_length=1, max_length=200)
    to: Optional[str] = Field(default=None, max_length=500)
    model_config = {"extra": "forbid"}


class PageContextVisibleEntry(BaseModel):
    id: str = Field(..., min_length=1, max_length=128)
    title: Optional[str] = Field(default=None, max_length=500)
    status: Optional[str] = Field(default=None, max_length=64)
    entry_type: Optional[str] = Field(default=None, max_length=128)
    model_config = {"extra": "forbid"}


class PageContextVisibleTrack(BaseModel):
    id: str = Field(..., min_length=1, max_length=128)
    title: Optional[str] = Field(default=None, max_length=500)
    model_config = {"extra": "forbid"}


class PageContextVisibleData(BaseModel):
    entries: Optional[List[PageContextVisibleEntry]] = Field(
        default=None, max_length=MAX_VISIBLE_CONTEXT_ITEMS
    )
    tracks: Optional[List[PageContextVisibleTrack]] = Field(
        default=None, max_length=MAX_VISIBLE_CONTEXT_ITEMS
    )
    total_count: Optional[int] = Field(default=None, ge=0)
    model_config = {"extra": "forbid"}


class PageContext(BaseModel):
    """Snapshotted UI context from the page the user was viewing."""

    url: str = Field(..., min_length=1, max_length=2000)
    route_path: str = Field(..., min_length=1, max_length=500)
    route_params: Optional[Dict[str, str]] = None
    search_params: Optional[Dict[str, str]] = None
    page_kind: Optional[str] = Field(default=None, max_length=64)
    breadcrumbs: Optional[List[PageContextBreadcrumb]] = Field(
        default=None, max_length=20
    )
    focused_track_id: Optional[str] = None
    focused_view_id: Optional[str] = None
    focused_app_id: Optional[str] = None
    focused_entry_id: Optional[str] = None
    visible_data: Optional[PageContextVisibleData] = None
    metadata: Optional[Dict[str, Any]] = None
    model_config = {"extra": "forbid"}


class SendMessageRequest(BaseModel):
    """Body for POST /api/chat/threads/{id}/messages — send a user turn.

    ``text`` is optional when ``images`` and/or ``attachment_ids`` are present
    (a file-only turn is valid — the agent reads the file on request). At
    least one of text/images/attachment_ids must be present.

    ``attachment_ids`` reference files already uploaded via
    ``POST /chat/threads/{id}/attachments`` (Slice B — general file
    persistence); the endpoint builds a per-turn context note from them.
    """

    text: str = Field(default="", max_length=8000)
    images: Optional[List[ChatImage]] = Field(
        default=None, max_length=MAX_IMAGES_PER_MESSAGE
    )
    attachment_ids: Optional[List[str]] = Field(
        default=None, max_length=MAX_ATTACHMENTS_PER_MESSAGE
    )
    focused_track_id: Optional[str] = None
    focused_space_id: Optional[str] = None
    focused_view_id: Optional[str] = None
    entity_refs: Optional[List[EntityRef]] = None
    page_context: Optional[PageContext] = None
    model_config = {"extra": "forbid"}

    @model_validator(mode="after")
    def _require_text_or_attachment(self) -> "SendMessageRequest":
        if (
            not (self.text and self.text.strip())
            and not self.images
            and not self.attachment_ids
        ):
            raise ValueError("a message must have text, an image, or an attachment")
        return self


class SystemMessageRequest(BaseModel):
    """Body for ``POST /api/chat/threads/{id}/system-message``.

    Persists an assistant-role message into a thread WITHOUT invoking
    the model.

    Used by surface-level affordances that want to record a deterministic
    note in the chat history that survives reload — currently the
    staging cards' "✓ Filed *X* in *Y*. Anything else?" confirmation,
    which we used to inject only into the runtime's local message
    state (lost on navigation). The text is generated client-side from
    the StagedChange title + track, so there's no need for a model
    call to produce it.
    """

    text: str = Field(..., min_length=1, max_length=4000)
    role: str = Field(default="assistant", pattern="^(assistant|system)$")


class QualificationRunStep(BaseModel):
    """Safe, content-free boundary receipt for qualification review."""

    step_key: str
    kind: str
    name: str
    status: str
    attempt: int = Field(ge=1)
    duration_ms: Optional[float] = Field(default=None, ge=0)
    error_code: Optional[str] = None
    policy_decision: str = ""
    denial_code: Optional[str] = None
    approval_ref: Optional[str] = None
    result_class: str = ""
    snapshot_divergence: bool = False
    model_config = {"extra": "forbid"}


class QualificationModelUse(BaseModel):
    model_id: str
    calls: int = Field(ge=0)
    input_tokens: int = Field(ge=0)
    output_tokens: int = Field(ge=0)
    finish_reasons: List[str] = Field(default_factory=list)
    model_config = {"extra": "forbid"}


class QualificationRunExport(BaseModel):
    """Redacted durable evidence used by the live-model qualification runner."""

    run_id: str
    status: str
    provider_configuration: Dict[str, str]
    metrics: Dict[str, Any]
    models: List[QualificationModelUse]
    redacted_trace_ref: str
    steps: List[QualificationRunStep]
    model_config = {"extra": "forbid"}
