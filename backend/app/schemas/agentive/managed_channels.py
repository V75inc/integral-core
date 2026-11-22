"""Optimistic mutation bodies for authorized workspace channel management."""

from pydantic import BaseModel, ConfigDict, Field


class ManageChannelProvision(BaseModel):
    """Require exact installed provision and version; no client scope grants."""

    model_config = ConfigDict(extra="forbid", strict=True)
    provision_id: str = Field(min_length=1, max_length=256)
    route_epoch: str = Field(min_length=1, max_length=256)


class ManageChannelEnablement(ManageChannelProvision):
    """Enablement is separate from package installation."""

    enabled: bool


class ManageChannelProfileAssignment(ManageChannelProvision):
    """Select a nullable public profile at the exact observed revision."""

    expected_profile_revision: int = Field(ge=0)
    profile_key: str | None = Field(min_length=1, max_length=128)
