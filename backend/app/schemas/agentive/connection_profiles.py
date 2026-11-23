"""Workspace-managed profile declarations; no caller-supplied authority."""

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, StrictBool

Audience = Literal["public", "staff"]
ProfileKey = Annotated[str, Field(pattern=r"^(public|staff|p_[a-f0-9]{32})$")]
PROFILE_AVATAR_MAX_BYTES = 512 * 1024
AvatarImage = Annotated[
    str, Field(max_length=4 * ((PROFILE_AVATAR_MAX_BYTES + 2) // 3) + 23)
]


class ProfileFields(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(min_length=1, max_length=80)
    audience: Audience | None = None
    description: str = Field(default="", max_length=120)
    icon: Literal["integral", "book", "search", "message"] = "integral"
    avatar: AvatarImage | None = None
    persona: str = Field(default="", max_length=3000)
    tools: list[str] = Field(default_factory=list, max_length=100)
    omissions: list[str] = Field(default_factory=list, max_length=100)


class ProfileDraft(ProfileFields):
    key: ProfileKey


class ProfileRevision(BaseModel):
    model_config = ConfigDict(extra="forbid")
    expected_revision: int = Field(ge=0)
    key: ProfileKey


class SaveProfileDraft(ProfileRevision):
    draft: ProfileDraft


class PublishProfile(ProfileRevision):
    offer_in_workspace_chat: bool = False
    enable_public_service_identity: StrictBool = False
    connection_ids: list[Annotated[str, Field(min_length=1, max_length=200)]] = Field(
        default_factory=list, max_length=100
    )


class BindProfile(ProfileRevision):
    connection_id: str = Field(min_length=1, max_length=200)


class CreateProfile(ProfileFields):
    expected_revision: int = Field(ge=0)


class NativeChatAllowlist(BaseModel):
    model_config = ConfigDict(extra="forbid")
    expected_revision: int = Field(ge=0)
    profiles: list[ProfileKey] = Field(max_length=100)


class PublicWebChatRoute(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    profile_key: ProfileKey | None = None
    enabled: bool = False
    generation: int = Field(default=0, ge=0)
    provision_id: str | None = Field(default=None, min_length=1, max_length=128)

    def persisted(self) -> dict:
        """Keep legacy storage shape when there is no explicit provision."""
        return self.model_dump(
            exclude={"provision_id"} if self.provision_id is None else None
        )


class PublicWebChatAssignment(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    expected_revision: int = Field(ge=0)
    profile_key: ProfileKey | None


class PublicWebChatEnablement(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    expected_revision: int = Field(ge=0)
    enabled: bool


class RenameWorkspaceAssistant(BaseModel):
    model_config = ConfigDict(extra="forbid")
    expected_revision: int = Field(ge=0)
    name: str = Field(min_length=1, max_length=80)
    icon: Literal["integral", "book", "search", "message"] | None = None
    avatar: AvatarImage | None = None
    persona: str | None = Field(default=None, max_length=3000)
