"""Shared schemas for chat @ / # entity references."""

from __future__ import annotations

from typing import List, Literal, Optional

from pydantic import BaseModel, Field


class EntityRef(BaseModel):
    """Client-supplied entity reference from chat composer picker."""

    kind: Literal["user", "app", "track"]
    id: str = Field(..., min_length=1, max_length=256)
    label: str = Field(..., min_length=1, max_length=200)
    # Composer matching token ("#Personal Expenses"). The UI sends it; the
    # agent uses kind/id/label. extra=forbid would 422 the whole turn.
    token: Optional[str] = Field(default=None, max_length=240)
    display_name: Optional[str] = Field(default=None, max_length=200)
    email: Optional[str] = Field(default=None, max_length=320)
    subtitle: Optional[str] = Field(default=None, max_length=200)

    model_config = {"extra": "forbid"}


class ResolvedEntityRef(BaseModel):
    """Validated entity reference passed to the agent harness."""

    kind: Literal["user", "app", "track"]
    entity_id: str
    label: str
    display_name: Optional[str] = None
    email: Optional[str] = None
    subtitle: Optional[str] = None


class EntityRefResolutionResult(BaseModel):
    """Outcome of resolving chat entity references for one turn."""

    resolved: List[ResolvedEntityRef] = Field(default_factory=list)
    ambiguous: List[dict] = Field(default_factory=list)
    context_preamble: str = ""
