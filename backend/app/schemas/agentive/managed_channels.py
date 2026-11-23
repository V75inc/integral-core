"""Optimistic mutation bodies for authorized workspace channel management."""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class ManageChannelInstall(BaseModel):
    """The v1 workspace-only scope; omission retains the same install behavior."""

    model_config = ConfigDict(extra="forbid", strict=True)
    connection_mode: Literal["shared"] = "shared"


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


class ManageChannelShareQuery(ManageChannelProvision):
    """Bind the read to the exact scoped publication."""

    workspace_id: str = Field(min_length=1, max_length=256)
    profile_key: str = Field(min_length=1, max_length=128)
    published_version: int = Field(ge=1, le=9007199254740991)


class ApprovedFullPageShare(ManageChannelShareQuery):
    """Allowlisted response for one issued share."""

    kind: Literal["approved-full-page-share-v1"] = "approved-full-page-share-v1"
    slug: Literal["public_web_chat"] = "public_web_chat"
    full_page_url: str


class ManageChannelEmbedShareQuery(ManageChannelShareQuery):
    """Pin one approved parent origin and independent embed policy revision."""

    embed_policy_revision: int = Field(ge=1, le=9007199254740991)
    parent_origin: str = Field(min_length=1, max_length=261)


class ApprovedWebChatEmbedShare(ManageChannelEmbedShareQuery):
    """Allowlisted proof of one already issued, currently authorized embed route."""

    kind: Literal["approved-web-chat-embed-v1"] = "approved-web-chat-embed-v1"
    slug: Literal["public_web_chat"] = "public_web_chat"
    embed_page_url: str
