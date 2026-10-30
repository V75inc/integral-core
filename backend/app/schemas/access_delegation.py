"""Bounded declarations for App action delegation; no executable expressions."""

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, StrictBool, field_validator


class ActionCondition(BaseModel):
    model_config = ConfigDict(extra="forbid")
    attribute: Literal["entry.author_id", "entry.status", "entry.custom_fields"]
    field: str = ""
    operator: Literal["eq", "ne", "in"] = "eq"
    value: Any = None
    principal: StrictBool = False

    @field_validator("value")
    @classmethod
    def bounded_value(cls, value: Any) -> Any:
        if isinstance(value, (dict, list)) and (
            isinstance(value, dict) or len(value) > 32
        ):
            raise ValueError("conditions accept scalars or at most 32 scalar values")
        values = value if isinstance(value, list) else [value]
        if any(isinstance(v, str) and len(v) > 4096 for v in values):
            raise ValueError("condition strings exceed evaluation budget")
        if any(not isinstance(v, (str, int, bool, type(None))) for v in values):
            raise ValueError("condition values must be scalar")
        return value


class ActionTarget(BaseModel):
    model_config = ConfigDict(extra="forbid")
    input: str = Field(min_length=1, max_length=64, pattern=r"^[a-z][a-z0-9_]*$")
    kind: Literal["entry", "track"]


class ActionMutation(BaseModel):
    model_config = ConfigDict(extra="forbid")
    entry_type: str = Field(min_length=1, max_length=64)
    kind: Literal["create", "update", "delete"] = "update"
    fields: list[str] = Field(default_factory=list, max_length=64)
    top_level: list[Literal["title", "body", "status"]] = Field(
        default_factory=list, max_length=3
    )


class ActionAuthorization(BaseModel):
    model_config = ConfigDict(extra="forbid")
    version: Literal[1] = 1
    roles: list[str] = Field(min_length=1, max_length=32)
    target_resolver: str | None = Field(
        default=None, min_length=1, max_length=64, pattern=r"^[a-z][a-z0-9_-]*$"
    )
    targets: list[ActionTarget] = Field(default_factory=list, max_length=16)
    conditions: list[ActionCondition] = Field(default_factory=list, max_length=16)
    mutations: list[ActionMutation] = Field(default_factory=list, max_length=32)
    effects: list[Literal["email.enqueue"]] = Field(default_factory=list, max_length=1)

    @field_validator("version", mode="before")
    @classmethod
    def exact_version(cls, value: int) -> int:
        """Only the reviewed declaration version can establish authority."""
        if type(value) is not int or value != 1:
            raise ValueError("unsupported declaration version")
        return value

    @field_validator("roles")
    @classmethod
    def exact_roles(cls, values: list[str]) -> list[str]:
        import re

        if len(values) != len(set(values)) or any(
            not re.fullmatch(r"[a-z][a-z0-9_-]{0,63}", v) for v in values
        ):
            raise ValueError("roles must be unique exact declared keys")
        return values


class GroupCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(min_length=1, max_length=120)


class ActionBindingCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    subject_kind: Literal["human", "group"]
    subject_id: str = Field(min_length=1)
    role: str = Field(min_length=1, max_length=64)
    conditions: list[ActionCondition] = Field(default_factory=list, max_length=16)
    expires_at: str | None = None

    @field_validator("expires_at")
    @classmethod
    def timezone_expiry(cls, value: str | None) -> str | None:
        """Expiry is an explicit timezone-aware instant, never local wall time."""
        if value is not None:
            from datetime import datetime

            if datetime.fromisoformat(value).tzinfo is None:
                raise ValueError("expiry requires timezone")
        return value


class EmptyDelegationMutation(BaseModel):
    """Target-only mutations accept no unreviewed JSON attributes."""

    model_config = ConfigDict(extra="forbid")


class ActionDecisionRequest(BaseModel):
    """Preview inputs are data only; actor and workspace come from the server."""

    model_config = ConfigDict(extra="forbid")
    input: dict[str, Any] = Field(default_factory=dict)
