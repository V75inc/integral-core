"""Deployment-wide native chat limits. Omitted fields follow the host."""

from pydantic import BaseModel, ConfigDict, Field


class ChatLimitOverrides(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid")
    request_limit: int | None = Field(default=None, ge=1, le=100)
    tool_call_limit: int | None = Field(default=None, ge=1, le=32)
    token_limit: int | None = Field(default=None, ge=1)
    run_seconds: int | None = Field(default=None, ge=30, le=3600)
    request_timeout: int | None = Field(default=None, ge=10, le=900)
    idle_timeout: int | None = Field(default=None, ge=5, le=180)


class ChatLimitsWrite(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid")
    revision: int = Field(ge=0)
    overrides: ChatLimitOverrides
