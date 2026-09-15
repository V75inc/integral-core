"""Schemas for agentive/api/staging.py — staging primitive bodies."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


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
