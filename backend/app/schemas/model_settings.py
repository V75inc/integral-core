"""Typed, capability-checked model options; no arbitrary provider payloads."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class ModelGenerationSettings(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    reasoning_effort: (
        Literal["none", "minimal", "low", "medium", "high", "xhigh"] | None
    ) = None
    max_tokens: int | None = Field(default=None, ge=1, le=131072)
    temperature: float | None = Field(default=None, ge=0, le=2)
    top_p: float | None = Field(default=None, gt=0, le=1)
    verbosity: Literal["low", "medium", "high"] | None = None
    thinking_budget: int | None = Field(default=None, ge=1024, le=131071)
    context_window: int | None = Field(default=None, ge=2048, le=131072)
    request_timeout: int | None = Field(default=None, ge=10, le=900)
    idle_timeout: int | None = Field(default=None, ge=5, le=180)

    model_request_limit: int | None = Field(default=None, ge=1, le=100)
    tool_call_limit: int | None = Field(default=None, ge=1, le=32)
    turn_token_limit: int | None = Field(default=None, ge=1024, le=1_200_000)
    turn_timeout: int | None = Field(default=None, ge=30, le=3600)

    @model_validator(mode="after")
    def compatible(self):
        if self.temperature is not None and self.top_p is not None:
            raise ValueError("Choose temperature or top-p, rather than both")
        if self.thinking_budget is not None:
            if self.reasoning_effort is not None:
                raise ValueError("Choose reasoning effort or a thinking budget")
            if self.max_tokens is None or self.thinking_budget >= self.max_tokens:
                raise ValueError(
                    "Thinking budget must be below the explicit output limit"
                )
            if self.temperature is not None or self.top_p is not None:
                raise ValueError(
                    "Leave sampling at provider default when using a thinking budget"
                )
        if (
            self.context_window
            and self.max_tokens
            and self.context_window < self.max_tokens + 1024
        ):
            raise ValueError(
                "Context must leave at least 1024 tokens beyond the output limit"
            )
        if (
            self.idle_timeout
            and self.request_timeout
            and self.idle_timeout > self.request_timeout
        ):
            raise ValueError("Idle timeout cannot exceed request timeout")
        if (
            self.max_tokens
            and self.turn_token_limit
            and self.max_tokens > self.turn_token_limit
        ):
            raise ValueError("Output limit cannot exceed the turn token budget")
        return self
