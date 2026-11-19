"""Request bodies for the personal WhatsApp channel binding."""

from __future__ import annotations

from urllib.parse import urlparse

from pydantic import BaseModel, Field, field_validator


class SaveWhatsAppChannelBindingRequest(BaseModel):
    """Body for PUT /agentive/channels/whatsapp/binding."""

    jvconnect_base_url: str
    api_key: str = Field(repr=False)

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
