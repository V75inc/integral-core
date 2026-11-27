"""Strict anonymous full-page Web Chat request bodies."""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class FullPageWebChatStart(BaseModel):
    """Open or resume a conversation without accepting scope or credentials."""

    model_config = ConfigDict(extra="forbid", strict=True)
    mode: Literal["new", "resume"]


class FullPageWebChatSend(BaseModel):
    """Accept only a bounded idempotency ID and visitor text."""

    model_config = ConfigDict(extra="forbid", strict=True)
    id: str = Field(pattern=r"^[A-Za-z0-9_-]{1,128}$")
    text: str = Field(min_length=1, max_length=1000)


class EmbedWebChatBootstrap(FullPageWebChatStart):
    """Exchange one private embed seed, with no caller-selected authority."""

    bootstrap_seed: str = Field(pattern=r"^iweb_[A-Za-z0-9_-]{86}$")
