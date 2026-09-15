"""Schemas for Prompt Sheet queue HTTP endpoints."""

from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field


class ResolveQuestionRequest(BaseModel):
    thread_id: str
    item_id: str
    choices: Optional[List[str]] = None
    free_text: str = ""
    skip: bool = False


class MarkWriteRequest(BaseModel):
    thread_id: str
    token: str
    status: str = Field(description="approved | rejected")


class CancelAllRequest(BaseModel):
    thread_id: str


class PromptQueueResponse(BaseModel):
    ok: bool = True
    open: bool = False
    queue: Dict[str, Any] = Field(default_factory=dict)
    resume_text: Optional[str] = None
    closed: bool = False
    cancelled: bool = False
    matched: bool = False
    revoked_tokens: List[str] = Field(default_factory=list)
