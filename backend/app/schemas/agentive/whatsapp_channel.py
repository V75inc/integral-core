"""Request bodies for the personal WhatsApp channel binding."""

from __future__ import annotations

from urllib.parse import urlparse

from pydantic import BaseModel, Field, field_validator


class SaveWhatsAppChannelBindingRequest(BaseModel):
    """Body for PUT /agentive/channels/whatsapp/binding."""

    jvconnect_base_url: str
    api_key: str = Field(repr=False)
    # Explicit null means Add. An ID means Configure that existing line.
    # Omission retains the behavior of clients using the original PUT.
    expected_binding_id: str | None = Field(default=None, min_length=1)

    model_config = {"extra": "forbid"}

    @field_validator("jvconnect_base_url")
    @classmethod
    def _https_base_url(cls, value: str) -> str:
        parsed = urlparse(value.strip())
        if parsed.scheme != "https" or not parsed.netloc:
            raise ValueError("jvconnect base URL must be https")
        return value.strip().rstrip("/")

    @field_validator("api_key")
    @classmethod
    def _jvconnect_key(cls, value: str) -> str:
        if not value.startswith("jvk_") or len(value) <= 4:
            raise ValueError("API key must be a jvconnect key")
        return value


class SetWhatsAppCallingRequest(BaseModel):
    """Body for PUT /agentive/channels/whatsapp/binding/calling."""

    calling_enabled: bool
    expected_grant_id: str = Field(min_length=1)
    expected_revision: int = Field(ge=0)

    model_config = {"extra": "forbid"}


class SaveWhatsAppVoiceServiceRequest(BaseModel):
    """Body for PUT /agentive/channels/whatsapp/binding/calling/service."""

    jvvoice_base_url: str = ""
    api_key: str = Field(default="", repr=False)

    model_config = {"extra": "forbid"}

    @field_validator("jvvoice_base_url")
    @classmethod
    def _https_or_empty(cls, value: str) -> str:
        stripped = value.strip().rstrip("/")
        if not stripped:
            return ""
        parsed = urlparse(stripped)
        if (
            parsed.scheme != "https"
            or not parsed.hostname
            or parsed.username
            or parsed.password
            or parsed.query
            or parsed.fragment
        ):
            raise ValueError(
                "jvvoice base URL must be https without a user, query, or fragment"
            )
        return stripped
