"""Workspace-owned embedding model choices, independent of chat generation."""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class EmbeddingModelChoice(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    mode: Literal["deployment", "provider"] = "deployment"
    connection_id: str = Field(default="", max_length=200)
    model: str = Field(default="", max_length=128)
    dimensions: int = Field(default=1536, ge=16, le=2000)
    allow_workspace_text: bool = False

    @model_validator(mode="after")
    def complete_provider(self):
        if self.mode == "provider" and (
            not self.connection_id.strip()
            or not self.model.strip()
            or not self.allow_workspace_text
        ):
            raise ValueError(
                "Choose a provider/model and allow indexed workspace text processing"
            )
        return self


class EmbeddingModelWrite(EmbeddingModelChoice):
    revision: int = Field(ge=0)
    continue_previous_provider: bool = False


class EmbeddingModelCancel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    revision: int = Field(ge=0)


class EmbeddingModelResponse(BaseModel):
    workspace_id: str
    editable: bool
    provider_overrides_supported: bool
    revision: int
    configuration: EmbeddingModelChoice
    effective_provider: str
    effective_model: str
    processing_location: Literal["server", "device", "provider"] = "server"
    dimensions: int
    profile_id: str | None = None
    rebuild_status: str = ""
    rebuild_phase: str = ""
    error: str = ""
    changed: bool = False
    pending_model: str = ""
    pending_profile_id: str | None = None
    enumerated: int | None = None
    verified: int | None = None
    rebuild_error: str = ""
    serving_available: bool = True


class EmbeddingModelTestResponse(BaseModel):
    valid: bool
    dimensions: int
    message: str
