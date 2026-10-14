"""Schemas for per-user model credential (BYOK) API — primary model and voice input."""

from __future__ import annotations

from typing import FrozenSet, Literal, Optional, get_args

from pydantic import BaseModel, ConfigDict, Field, model_validator

ModelProvider = Literal[
    "openai", "anthropic", "openrouter", "ollama", "ollama_local", "custom_openai"
]

# Providers that can back the ``speech`` (voice input) slot. A subset of
# ``ModelProvider``: only vendors with a streaming speech-to-text API qualify.
SpeechProvider = Literal["openai"]
SPEECH_CAPABLE_PROVIDERS: FrozenSet[str] = frozenset(get_args(SpeechProvider))


class ModelCredentialUpsertRequest(BaseModel):
    """BYOK upsert. The primary provider and model are required.

    ``api_key`` is required on first create; omit or send empty on update to keep
    the stored default key.
    """

    model_config = ConfigDict(extra="forbid")

    provider: ModelProvider
    model: str = Field(..., min_length=1, max_length=128)
    api_key: Optional[str] = Field(default=None, max_length=512)
    # Custom OpenAI-compatible endpoint. Required when provider is
    # ``custom_openai``; must be empty otherwise. Stored as plaintext base URL
    # (not a secret) and passed as LiteLLM ``api_base``.
    base_url: Optional[str] = Field(default=None, max_length=512)

    # Speech-to-text (voice input) slot. Opt-in: an empty ``speech_model`` means off.
    speech_model: Optional[str] = Field(default=None, max_length=128)
    speech_provider: Optional[ModelProvider] = None
    speech_api_key: Optional[str] = Field(default=None, min_length=8, max_length=512)

    @model_validator(mode="after")
    def _custom_base_url_rules(self) -> "ModelCredentialUpsertRequest":
        base = (self.base_url or "").strip()
        if self.provider == "custom_openai":
            if not base:
                raise ValueError("base_url is required for custom_openai")
            normalized = base.rstrip("/")
            if len(normalized) > 512:
                raise ValueError("base_url is too long")
            lowered = normalized.lower()
            if not (lowered.startswith("http://") or lowered.startswith("https://")):
                raise ValueError("base_url must start with http:// or https://")
            if "@" in normalized.split("://", 1)[-1].split("/", 1)[0]:
                raise ValueError("base_url must not contain credentials")
            self.base_url = normalized
        elif base:
            raise ValueError("base_url is only supported for custom_openai")
        return self

    @model_validator(mode="after")
    def _speech_provider_is_capable(self) -> "ModelCredentialUpsertRequest":
        if not (self.speech_model or "").strip():
            return self
        effective = self.speech_provider or self.provider
        if effective not in SPEECH_CAPABLE_PROVIDERS:
            capable = ", ".join(sorted(SPEECH_CAPABLE_PROVIDERS))
            raise ValueError(
                f"voice input needs a speech-to-text provider ({capable}); "
                "set speech_provider"
            )
        return self

    @model_validator(mode="after")
    def _speech_provider_requires_key(self) -> "ModelCredentialUpsertRequest":
        if (
            (self.speech_model or "").strip()
            and self.speech_provider
            and self.speech_provider != self.provider
            and not (self.speech_api_key or "").strip()
        ):
            raise ValueError(
                "speech_api_key is required when speech_provider differs from provider"
            )
        return self


class ModelCredentialResponse(BaseModel):
    provider: ModelProvider
    model: str
    base_url: Optional[str] = None
    speech_provider: Optional[ModelProvider] = None
    speech_model: Optional[str] = None
    key_fingerprint: str
    speech_key_fingerprint: Optional[str] = None
    is_active: bool = True
    validated_at: Optional[str] = None
    last_used_at: Optional[str] = None
    updated_at: Optional[str] = None


class ModelCredentialValidateRequest(BaseModel):
    provider: ModelProvider
    api_key: Optional[str] = Field(default=None, max_length=512)
    base_url: Optional[str] = Field(default=None, max_length=512)


class ModelCredentialValidateResponse(BaseModel):
    valid: bool
    message: str = ""
