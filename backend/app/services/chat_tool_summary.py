"""Bounded structural outcomes for public tool cards, never raw tool content."""

from __future__ import annotations

import json
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class PublicDraftReceipt(BaseModel):
    """Bounded locator only; the download endpoint reauthorizes every read."""

    model_config = ConfigDict(extra="forbid", strict=True)

    thread_id: str = Field(pattern=r"^n\.ChatThread\.[A-Za-z0-9_-]+$", max_length=200)
    key: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
    version: int = Field(ge=1)
    format: Literal["markdown", "docx", "pdf", "pptx"]


class PublicToolSummary(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    outcome: Literal["completed", "returned", "awaiting_approval", "failed"]
    returned_count: int | None = Field(default=None, ge=0, le=1_000_000)
    duration_ms: float | None = Field(default=None, ge=0, le=86_400_000)
    document_draft: PublicDraftReceipt | None = None


def normalize_tool_summary(value: Any) -> dict[str, Any]:
    """Validate the bounded public summary shape."""
    return PublicToolSummary.model_validate(value).model_dump(exclude_none=True)


def tool_summary(name: str, content: Any, *, failed: bool) -> dict[str, Any]:
    # Only a strictly typed, reauthorized draft locator crosses this boundary;
    # no document content, titles, arguments, URLs or free-form provider text do.
    """Project structural outcomes while excluding arbitrary tool content."""
    outcome = "failed" if failed else "completed"
    count = None
    draft = None
    if isinstance(content, str) and len(content) <= 65536:
        try:
            content = json.loads(content)
        except (ValueError, TypeError):
            pass
    if name.startswith("integral_") or name == "search_capabilities":
        if isinstance(content, dict) and not failed:
            if (
                name == "integral_upsert_artifact"
                and content.get("ok") is True
                and content.get("_kind") == "document_draft"
            ):
                try:
                    draft = PublicDraftReceipt.model_validate(
                        {
                            k: content.get(k)
                            for k in ("thread_id", "key", "version", "format")
                        }
                    )
                except ValueError:
                    pass
            if (
                content.get("requires_approval") is True
                or content.get("status") == "pending_approval"
            ):
                outcome = "awaiting_approval"
            else:
                for key in (
                    "entries",
                    "items",
                    "results",
                    "result",
                    "apps",
                    "tracks",
                    "tools",
                    "skills",
                    "resources",
                ):
                    if isinstance(content.get(key), list):
                        count = min(len(content[key]), 1_000_000)
                        outcome = "returned"
                        break
    return PublicToolSummary(
        outcome=outcome, returned_count=count, document_draft=draft
    ).model_dump(exclude_none=True)
