"""Raw HTTP member rights validation before framework parameter coercion."""

from typing import Any, Literal

from pydantic import BaseModel, StrictBool, field_validator


class WorkspaceMembershipInput(BaseModel):
    """Optional create/patch fields; supplied creation grants must be booleans."""

    role: Literal["admin", "member", "guest"] | None = None
    can_create_apps: StrictBool | None = None
    can_create_tracks: StrictBool | None = None

    @field_validator("role", "can_create_apps", "can_create_tracks", mode="before")
    @classmethod
    def require_explicit_value(cls, value: Any) -> Any:
        """Omission means no patch; explicitly supplied null is not a right."""
        if value is None:
            raise ValueError("Supplied membership rights cannot be null")
        return value
