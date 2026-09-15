"""Schemas for agentive/api/questions.py — clarifying-question endpoints."""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field


class AnswerQuestionRequest(BaseModel):
    """Body for POST /agentive/questions/answer."""

    thread_id: str = Field(min_length=1, max_length=128)
    question_id: str = Field(default="", max_length=128)
    choices: Optional[List[str]] = None
    free_text: str = Field(default="", max_length=4000)


class AnswerQuestionResponse(BaseModel):
    """Success envelope after clearing a pending question."""

    ok: bool = True
    cleared: bool = False
    question_id: str = ""
    choices: List[str] = Field(default_factory=list)
    detail: Optional[str] = None


class PendingQuestionResponse(BaseModel):
    """GET /agentive/questions/pending success envelope."""

    ok: bool = True
    pending: Optional[Dict[str, Any]] = None
