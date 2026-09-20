"""Stable, transport-independent information contracts for WP-02."""

from __future__ import annotations

from enum import Enum
from typing import Any, Mapping

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


__all__ = [
    "FieldDefinition",
    "FieldNamespace",
    "PLATFORM_FIELD_KEYS",
    "RecordRevision",
    "resolve_field_value",
]
