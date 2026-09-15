"""Wire shapes for ``/agentive/speech/*`` — voice input for the agent chat."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.schemas.speech_preferences import SpeechPreferences, validate_language_tag


class SpeechEngineOption(BaseModel):
    """One recognizer the composer may use, in preference order."""

    engine: str
    source: Literal["workspace", "browser"]
    display_name: str
    streaming: bool = True
    privacy_note: Optional[str] = None


class SpeechProviderStatus(BaseModel):
    """The workspace's provider, as far as the caller needs to know."""

    configured: bool
    #: False for workspace guests even when a provider is configured.
    available_to_you: bool
    provider: Optional[str] = None
    model: Optional[str] = None
    source: Optional[Literal["byok", "platform"]] = None


class SpeechLimits(BaseModel):
    max_session_seconds: int
    session_ttl_seconds: int


class SpeechConfigResponse(BaseModel):
    workspace_id: str
    workspace_role: str
    preferences: SpeechPreferences
    engines: List[SpeechEngineOption]
    #: Engine the caller's preference points at; the browser still checks
    #: support (e.g. no Web Speech on Firefox) before offering it.
    preferred_engine: Optional[str]
    provider: SpeechProviderStatus
    limits: SpeechLimits


class SpeechSessionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    #: Overrides the caller's preferred language for this session.
    language: Optional[str] = Field(default=None, max_length=35)

    @field_validator("language")
    @classmethod
    def _language(cls, value: Optional[str]) -> Optional[str]:
        return None if value is None else validate_language_tag(value)


class SpeechSessionResponse(BaseModel):
    """A short-lived browser credential — never the provider API key."""

    engine: str
    transport: Literal["webrtc", "websocket"]
    connect_url: str
    client_secret: str
    expires_at: datetime
    params: Dict[str, Any] = Field(default_factory=dict)
    max_session_seconds: int
