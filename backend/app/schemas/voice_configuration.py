"""Workspace voice model selection reusing a private saved provider."""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class VoiceModelWrite(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    mode: Literal["inherit", "provider", "disabled"] = "inherit"
    connection_id: str = Field(default="", max_length=200)
    model: str = Field(default="", max_length=128)
    revision: int = Field(ge=0)

    @model_validator(mode="after")
    def complete_provider(self):
        if self.mode == "provider" and (
            not self.connection_id.strip() or not self.model.strip()
        ):
            raise ValueError("Choose a saved voice provider and model")
        return self


RealtimeVoiceModel = Literal[
    "", "gpt-realtime-2.1-mini", "gpt-realtime", "gpt-realtime-mini"
]


class RealtimeVoiceModelWrite(VoiceModelWrite):
    mode: Literal["provider", "disabled"] = "disabled"
    model: RealtimeVoiceModel = ""


class VoiceStart(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    ticket: str = Field(pattern=r"^[A-Za-z0-9_-]{43}$")
    workspace_id: str = Field(min_length=1, max_length=255)
    profile_key: str = Field(default="", max_length=64)
    thread_id: str = Field(default="", max_length=255)
