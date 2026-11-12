"""Durable steering API contracts."""

from pydantic import BaseModel, ConfigDict, Field


class SteeringCorrectionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    client_request_id: str = Field(min_length=1, max_length=128)
    text: str = Field(min_length=1, max_length=8000)
    from_follow_up_queue: bool = False
