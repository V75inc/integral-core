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
            raise ValueError("Choose a saved voice provider and transcription model")
        return self
