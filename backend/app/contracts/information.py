"""Stable, transport-independent information contracts for WP-02."""

from __future__ import annotations

from enum import Enum
from typing import Any, Mapping, Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

PLATFORM_FIELD_KEYS = frozenset(
    {
        "id",
        "title",
        "body",
        "status",
        "type_id",
        "author_id",
        "track_id",
        "created_at",
        "updated_at",
    }
)


class FieldNamespace(str, Enum):
    """Where a field is stored and who reserves its name."""

    PLATFORM = "platform"
    BUSINESS = "business"
    SYSTEM = "system"


class RelationTarget(str, Enum):
    """Graph targets supported by declarative relation fields."""

    ENTRY = "entry"
    TRACK = "track"


class TargetRemovalBehavior(str, Enum):
    """Source-record behavior when a cross-App relation target disappears."""

    BLOCK = "block"
    NULL = "null"
    ARCHIVE_SELF = "archive_self"


class RelationDefinition(BaseModel):
    """Declared graph semantics for a relation field, independent of storage."""

    target: RelationTarget
    many: bool = False
    allow_cross_track: bool = False
    on_target_removal: TargetRemovalBehavior = TargetRemovalBehavior.BLOCK

    model_config = ConfigDict(frozen=True, extra="forbid")


class FieldDefinition(BaseModel):
    """One stable field identity in a versioned record definition.

    ``id`` never changes after publication. ``key`` is the current storage
    mapping and ``label`` is presentation-only, so a rename cannot sever a
    field from its existing values, views, or relations.
    """

    id: str
    key: str
    label: str
    type: str
    namespace: FieldNamespace = FieldNamespace.BUSINESS
    owner: str = "application"
    schema_revision: int = Field(ge=1)
    relation: Optional[RelationDefinition] = None

    model_config = ConfigDict(frozen=True, extra="forbid")

    @field_validator("id", "key", "label", "type", "owner")
    @classmethod
    def require_nonempty_text(cls, value: str) -> str:
        """Reject ambiguous empty identity and presentation values."""
        value = str(value or "").strip()
        if not value:
            raise ValueError("field metadata must not be empty")
        return value

    @model_validator(mode="after")
    def prevent_platform_name_collision(self) -> "FieldDefinition":
        """Require business fields to use their own storage namespace."""
        if (
            self.namespace is FieldNamespace.BUSINESS
            and self.key in PLATFORM_FIELD_KEYS
        ):
            raise ValueError(
                f"business field key {self.key!r} collides with a platform field"
            )
        return self

    @model_validator(mode="after")
    def require_relation_metadata_for_relation_fields(self) -> "FieldDefinition":
        """Keep relation values anchored to an explicit graph declaration."""
        if self.type == "relation" and self.relation is None:
            raise ValueError("relation fields require relation metadata")
        if self.type != "relation" and self.relation is not None:
            raise ValueError("only relation fields may declare relation metadata")
        return self


class RecordRevision(BaseModel):
    """Version binding supplied by writes that need optimistic concurrency."""

    record_revision: int = Field(ge=1)
    schema_revision: int = Field(ge=1)

    model_config = ConfigDict(frozen=True, extra="forbid")


def resolve_field_value(
    field: FieldDefinition,
    *,
    platform_values: Mapping[str, Any],
    custom_fields: Mapping[str, Any],
) -> Any:
    """Read exactly one declared field without value-dependent fallback.

    A null business value is still a business value; it must never fall back to
    a same-named platform attribute. This makes API, form and query callers
    use identical field-addressing semantics.
    """
    if field.namespace is FieldNamespace.PLATFORM:
        return platform_values.get(field.key)
    return custom_fields.get(field.key)


def legacy_entry_value_maps(
    entry: Mapping[str, Any],
) -> tuple[Mapping[str, Any], Mapping[str, Any]]:
    """Map an exported legacy Entry into the stable field-addressing inputs.

    Platform attributes continue to live at the Entry top level while business
    values remain in ``custom_fields``.  This adapter is deliberately
    value-neutral: an empty or null custom value never changes its namespace.
    """
    platform_values = {key: entry.get(key) for key in PLATFORM_FIELD_KEYS}
    raw_custom_fields = entry.get("custom_fields")
    custom_fields = raw_custom_fields if isinstance(raw_custom_fields, Mapping) else {}
    return platform_values, custom_fields


def resolve_legacy_entry_field_value(
    field: FieldDefinition, entry: Mapping[str, Any]
) -> Any:
    """Resolve a stable field from the existing Entry/custom_fields shape."""
    platform_values, custom_fields = legacy_entry_value_maps(entry)
    return resolve_field_value(
        field,
        platform_values=platform_values,
        custom_fields=custom_fields,
    )


def schema_revision_from_profile_version(version_number: Any) -> int:
    """Return the valid write-contract revision for an effective profile.

    Content-profile publication owns the monotonic ``version_number``.  Entry
    writers use that value as their schema binding, while unprofiled and
    legacy records retain the explicit baseline revision of one.
    """
    try:
        revision = int(version_number)
    except (TypeError, ValueError):
        return 1
    return max(revision, 1)


__all__ = [
    "FieldDefinition",
    "FieldNamespace",
    "PLATFORM_FIELD_KEYS",
    "RecordRevision",
    "RelationDefinition",
    "RelationTarget",
    "TargetRemovalBehavior",
    "resolve_field_value",
    "legacy_entry_value_maps",
    "resolve_legacy_entry_field_value",
    "schema_revision_from_profile_version",
]
