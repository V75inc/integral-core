"""Schemas for agentive/api/staging.py — staging primitive bodies."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class EntryProposalDraft(BaseModel):
    """Editable content only. Targets, identity and authority remain server-held."""

    model_config = ConfigDict(extra="forbid")

    title: str = Field(max_length=10000)
    body: str = Field(default="", max_length=1000000)
    fields: dict[str, Any] = Field(default_factory=dict, max_length=500)
    tags: list[str] = Field(default_factory=list, max_length=500)


class ReviseEntryProposalRequest(BaseModel):
    """One selected record in an immutable, still-pending proposal."""

    model_config = ConfigDict(extra="forbid")

    operation_index: int = Field(default=0, ge=0, le=499)
    revision: str = Field(min_length=64, max_length=64)
    draft: EntryProposalDraft


class BlessTokenRequest(BaseModel):
    """Body for POST /staging/bless-token."""

    token: str = Field(min_length=1, max_length=128)
    autonomy: Literal["single", "session"] = "single"


class RevokeTokenRequest(BaseModel):
    """Body for POST /staging/revoke-token."""

    token: str = Field(min_length=1, max_length=128)


class TextApproveRequest(BaseModel):
    """Body for ``POST /staging/text-approve``.

    Adapters POST the user's raw reply text + an optional channel
    hint. ``channel`` is informational (logged for trace); the parse
    itself is channel-agnostic.
    """

    text: str = Field(min_length=1, max_length=2000)
    channel: str = Field(default="text", max_length=32)


class DiscardDesignRequest(BaseModel):
    """The exact saved design selected from the owner's current workspace."""

    thread_id: str = Field(min_length=1, max_length=256)
    design_id: str = Field(max_length=128)
    blueprint_revision: int = Field(ge=0)
    proposed_at: str = Field(min_length=1, max_length=128)
