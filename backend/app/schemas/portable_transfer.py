"""Versioned App and Track portability contract.

Version 1 is data-only. Version 2 may bind one or more admitted package
payloads so packaged Apps round-trip with their runtime extensions.
"""

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


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
    settings: dict[str, Any] = Field(default_factory=dict)
    package_slug: str = ""
    tags: list[PortableTag] = Field(default_factory=list)
    dashboards: list[PortableDashboard] = Field(default_factory=list)
    home_composition: dict[str, Any] | None = None
    tracks: list[PortableTrack] = Field(default_factory=list)


class PackagePayload(PortableModel):
    """Admitted package tree carried under package/<slug>/ in a v2 archive."""

    slug: str = Field(
        min_length=1, max_length=200, pattern=r"^[a-zA-Z0-9][a-zA-Z0-9_-]*$"
    )
    version: str = ""
    fingerprint: str = Field(min_length=1, max_length=128)
    member_prefix: str = Field(min_length=1, max_length=300)


class PortableArchive(PortableModel):
    format: Literal["integral-portable"] = "integral-portable"
    version: Literal[1, 2] = 1
    mode: Literal["configuration", "full"]
    kind: Literal["app", "track"]
    apps: list[PortableApp] = Field(default_factory=list)
    tracks: list[PortableTrack] = Field(default_factory=list)
    packages: list[PackagePayload] = Field(default_factory=list)
    # Legacy singular field from early v2 single-App archives.
    package: PackagePayload | None = None

    def wire_dict(self) -> dict[str, Any]:
        """Keep ordinary v1 exports readable by the original strict v1 models."""
        result = self.model_dump()
        if self.version == 1:
            result.pop("package", None)
            result.pop("packages", None)
            for app in result["apps"]:
                app.pop("package_slug", None)
                app.pop("settings", None)
        return result

    @model_validator(mode="after")
    def normalize_packages(self) -> "PortableArchive":
        packages = list(self.packages or [])
        if self.package is not None and not packages:
            packages = [self.package]
        object.__setattr__(self, "packages", packages)
        if packages and self.version != 2:
            raise ValueError("Package payloads require portable archive version 2")
        if self.version == 2 and not packages:
            raise ValueError("Version 2 archives require at least one package payload")
        if packages and self.kind != "app":
            raise ValueError("Package payloads are only valid for App archives")
        slugs = [payload.slug for payload in packages]
        if len(slugs) != len(set(slugs)):
            raise ValueError("Package payloads must have unique slugs")
        slug_set = set(slugs)
        apps = list(self.apps)
        # Legacy single-App v2 archives omit package_slug; bind the sole package.
        if len(packages) == 1 and len(apps) == 1 and not apps[0].package_slug:
            apps = [apps[0].model_copy(update={"package_slug": packages[0].slug})]
            object.__setattr__(self, "apps", apps)
        for app in apps:
            if app.package_slug and app.package_slug not in slug_set:
                raise ValueError(
                    f"App {app.name!r} references unknown package_slug {app.package_slug!r}"
                )
        # Keep singular package populated for single-package archives (legacy readers).
        if len(packages) == 1:
            object.__setattr__(self, "package", packages[0])
        elif not packages:
            object.__setattr__(self, "package", None)
        return self
