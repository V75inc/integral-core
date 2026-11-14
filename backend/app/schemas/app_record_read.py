"""Bounded App-owned record projections; input never supplies authority."""

from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class RecordTextSearch(BaseModel):
    model_config = ConfigDict(extra="forbid")
    value: str = Field(max_length=120)
    fields: list[str] = Field(min_length=1, max_length=5)


class AppRecordRead(BaseModel):
    model_config = ConfigDict(extra="forbid")
    track: str = Field(default="", max_length=200)
    track_key: str = Field(default="", max_length=160)
    entry_type: str = Field(default="", max_length=160)
    filters: dict[str, Any] = Field(default_factory=dict, max_length=20)
    exclude_values: dict[str, list[str | int | bool]] = Field(
        default_factory=dict, max_length=5
    )
    title_contains: str = Field(default="", max_length=120)
    text_search: RecordTextSearch | None = None
    ids: list[Annotated[str, Field(min_length=1, max_length=200)]] | None = Field(
        default=None, max_length=50
    )
    full_values: bool = False
    fields: list[str] = Field(default_factory=list, max_length=20)
    limit: int = Field(default=25, ge=1, le=50)
    count_only: bool = False
    cursor: str | None = Field(default=None, max_length=2048)
    sort_by: str = Field(default="", max_length=128)
    sort_direction: Literal["asc", "desc"] = "asc"
    group_by: str = Field(default="", max_length=128)

    @model_validator(mode="after")
    def one_track_selector(self):
        if bool(self.track.strip()) == bool(self.track_key.strip()):
            raise ValueError("Select exactly one track or track_key")
        if self.full_values and self.ids is None:
            raise ValueError("Full projected values require explicit selected IDs")
        return self
