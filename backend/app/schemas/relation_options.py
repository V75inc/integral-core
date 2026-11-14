"""Compact authenticated relation picker contracts."""

from pydantic import BaseModel, Field


class RelationOptionsRequest(BaseModel):
    track_id: str = Field(min_length=1, max_length=200)
    entry_type_id: str = Field(min_length=1, max_length=200)
    field_key: str = Field(min_length=1, max_length=200)
    entry_id: str | None = Field(default=None, max_length=200)
    q: str = Field(default="", max_length=200)
    cursor: str | None = Field(default=None, max_length=8192)
    limit: int = Field(default=50, ge=1, le=50)


class RelationOption(BaseModel):
    id: str
    label: str
    track_id: str
    track_title: str


class RelationOptionsResponse(BaseModel):
    options: list[RelationOption] = Field(default_factory=list)
    next_cursor: str | None = None
    has_more: bool = False
