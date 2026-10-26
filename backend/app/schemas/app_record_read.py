"""Bounded App-owned record projections; input never supplies authority."""

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class AppRecordRead(BaseModel):
    model_config = ConfigDict(extra="forbid")
    track: str = Field(min_length=1, max_length=200)
    entry_type: str = Field(default="", max_length=160)
    filters: dict[str, Any] = Field(default_factory=dict, max_length=20)
    title_contains: str = Field(default="", max_length=120)
    fields: list[str] = Field(default_factory=list, max_length=20)
    limit: int = Field(default=25, ge=1, le=50)
    count_only: bool = False
    cursor: str | None = Field(default=None, max_length=2048)
    sort_by: str = Field(default="", max_length=128)
    sort_direction: Literal["asc", "desc"] = "asc"
    group_by: str = Field(default="", max_length=128)
