"""Strict recipient consent and workspace suspension HTTP contracts."""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, StrictBool, StrictInt, StrictStr


class OwnershipResponse(BaseModel):
    """Client supplies only action and proposal revision."""

    model_config = ConfigDict(extra="forbid")
    action: Literal["accept", "decline"]
    revision: StrictInt = Field(ge=0)


class SuspensionRequest(BaseModel):
    """A block is explicit and distinct from member removal."""

    model_config = ConfigDict(extra="forbid")
    suspended: StrictBool
    reason: StrictStr = Field(default="", max_length=1000)
    expected_revision: StrictInt | None = Field(default=None, ge=1)
    reviewed_access_digest: StrictStr = ""


class OwnershipRequest(BaseModel):
    """A resource-bound destination never establishes current owner identity."""

    model_config = ConfigDict(extra="forbid")
    recipient_id: StrictStr = Field(min_length=1)


class WorkspaceRehomeRequest(BaseModel):
    """Explicit destination owner review; omitted destinations never unroot data."""

    model_config = ConfigDict(extra="forbid")
    destination_workspace_id: StrictStr = Field(min_length=1)


class ResourceBlockRequest(BaseModel):
    """Identified subtree block, distinct from inherited exclusions."""

    model_config = ConfigDict(extra="forbid")
    subject_id: StrictStr = Field(min_length=1)
    blocked: StrictBool
    reason: StrictStr = Field(default="", max_length=1000)
