"""Bodies for drop-in connector package installation."""

from typing import Any

from pydantic import BaseModel, Field


class ConnectorPackageValues(BaseModel):
    """Configuration submitted at Add or Configure. Secrets are write-only."""

    workspace_id: str
    values: dict[str, Any] = Field(default_factory=dict)

    model_config = {"extra": "forbid"}


class ConnectorPackageEnablement(BaseModel):
    enabled: bool

    model_config = {"extra": "forbid"}


class ConnectorPackageAction(BaseModel):
    input: dict[str, Any] = Field(default_factory=dict)

    model_config = {"extra": "forbid"}
