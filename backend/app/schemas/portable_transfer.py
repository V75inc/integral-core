"""Versioned, data-only App and Track portability contract."""

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator


class PortableModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class ExportSelection(PortableModel):
    kind: Literal["app", "track"]
    ids: list[str] = Field(min_length=1, max_length=100)
    mode: Literal["configuration", "full"] = "configuration"


class PortableType(PortableModel):
    ref: str
    name: str = Field(min_length=1, max_length=200)
    icon: str = "document"
    form_schema: dict[str, Any] = Field(default_factory=dict)


class PortableTag(PortableModel):
    ref: str
    name: str
    color: str = "#6B7280"
    group_key: str | None = None
    parent_ref: str | None = None
    aliases: list[str] = Field(default_factory=list)
    applies_to_entry_types: list[str] = Field(default_factory=list)


class PortableView(PortableModel):
    ref: str
    name: str
    type: str
    config: dict[str, Any] = Field(default_factory=dict)
    entry_type_keys: list[str] = Field(default_factory=list)
    default_entry_type_key: str = ""
    is_default: bool = False
    hidden: bool = False


class PortableFile(PortableModel):
    ref: str
    filename: str = Field(min_length=1, max_length=255)
    mime_type: str
    source_type: Literal["file", "url"]
    external_url: str = ""
    member: str = ""
    sha256: str = ""


class PortableEntry(PortableModel):
    ref: str
    type_ref: str
    title: str = ""
    body: str = ""
    status: str = "active"
    created_at: str | None = None
    updated_at: str | None = None

    @field_validator("created_at", "updated_at")
    @classmethod
    def valid_timestamp(cls, value: str | None) -> str | None:
        """Preserve source chronology without accepting malformed timestamps."""
        if value is not None:
            datetime.fromisoformat(value.replace("Z", "+00:00"))
        return value

    custom_fields: dict[str, Any] = Field(default_factory=dict)
    tags: list[str] = Field(default_factory=list)
    attachments: list[PortableFile] = Field(default_factory=list)


class PortableTrack(PortableModel):
    ref: str
    model_ref: str = ""
    title: str = Field(min_length=1, max_length=200)
    purpose: str = ""
    icon: str = ""
    accent_color: str = ""
    relation_type_key: str = ""
    nav_visible: bool = True
    manifest: dict[str, Any] = Field(default_factory=dict)
    search_index: dict[str, Any] = Field(default_factory=dict)
    entry_types: list[PortableType] = Field(default_factory=list)
    tags: list[PortableTag] = Field(default_factory=list)
    views: list[PortableView] = Field(default_factory=list)
    entries: list[PortableEntry] = Field(default_factory=list)


class PortableDashboard(PortableModel):
    ref: str
    name: str
    layout: dict[str, Any] = Field(default_factory=dict)
    widgets: list[dict[str, Any]] = Field(default_factory=list)
    is_default: bool = False


class PortableApp(PortableModel):
    ref: str
    model_ref: str = ""
    name: str = Field(min_length=1, max_length=200)
    description: str = ""
    icon: str = ""
    accent_color: str = ""
    manifest: dict[str, Any] = Field(default_factory=dict)
    tags: list[PortableTag] = Field(default_factory=list)
    dashboards: list[PortableDashboard] = Field(default_factory=list)
    home_composition: dict[str, Any] | None = None
    tracks: list[PortableTrack] = Field(default_factory=list)


class PortableArchive(PortableModel):
    format: Literal["integral-portable"] = "integral-portable"
    version: Literal[1] = 1
    mode: Literal["configuration", "full"]
    kind: Literal["app", "track"]
    apps: list[PortableApp] = Field(default_factory=list)
    tracks: list[PortableTrack] = Field(default_factory=list)
