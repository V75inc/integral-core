"""Wire contracts for the Integral Desktop capability host."""

from typing import List

from pydantic import BaseModel, Field, field_validator


class DesktopEnvironmentSessionRequest(BaseModel):
    """Identify one installed desktop shell without granting it capabilities."""

    device_id: str = Field(min_length=8, max_length=200)
    device_name: str = Field(default="Integral Desktop", max_length=120)

    @field_validator("device_id")
    @classmethod
    def validate_device_id(
        cls: type["DesktopEnvironmentSessionRequest"], value: str
    ) -> str:
        """Restrict identifiers to a log- and lookup-safe alphabet."""
        value = value.strip()
        if not value or any(
            ch
            not in (
                "abcdefghijklmnopqrstuvwxyz" "ABCDEFGHIJKLMNOPQRSTUVWXYZ" "0123456789-_"
            )
            for ch in value
        ):
            raise ValueError("device_id must contain only letters, numbers, '-' or '_'")
        return value


class DesktopEnvironmentSessionResponse(BaseModel):
    """Single-use host connection material returned to the renderer."""

    ticket: str
    websocket_path: str
    expires_in: int
    capabilities: List[str]


class DesktopEnvironmentStatusResponse(BaseModel):
    """Safe diagnostics for the current principal/workspace host binding."""

    enabled: bool
    selector_present: bool
    binding_live: bool
    reason: str
    live_bindings_for_scope: int
    capabilities: List[str]
