"""Private BYOK connection/profile management requests."""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from app.schemas.model_credentials import ModelProvider
from app.schemas.model_settings import ModelGenerationSettings


class ConnectionWrite(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str | None = Field(default=None, max_length=200)
    name: str = Field(min_length=1, max_length=80)
    provider: ModelProvider
    base_url: str = Field(default="", max_length=512)
    api_key: str | None = Field(default=None, max_length=512, repr=False)
    without_key: bool = False


class ProfileWrite(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str | None = Field(default=None, max_length=200)
    connection_id: str = Field(min_length=1, max_length=200)
    name: str = Field(min_length=1, max_length=80)
    model: str = Field(min_length=1, max_length=128)
    generation_settings: ModelGenerationSettings = Field(
        default_factory=ModelGenerationSettings
    )


class ItemRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str = Field(min_length=1, max_length=200)


class CapabilitiesRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    provider: ModelProvider
    model: str = Field(min_length=1, max_length=128)


class ConversationModelRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    thread_id: str = Field(min_length=1, max_length=200)
    profile_id: str | None = Field(default=None, max_length=200)
    routing_mode: Literal["inherit", "heavy", "profile"] | None = None
    reasoning_efforts: dict[Literal["heavy", "light"], str | None] | None = None


class RoutingWrite(BaseModel):
    model_config = ConfigDict(extra="forbid")

    mode: Literal["single", "light_heavy"]
    heavy_profile_id: str = Field(min_length=1, max_length=200)
    light_profile_id: str = Field(default="", max_length=200)
    revision: str = Field(max_length=200)
