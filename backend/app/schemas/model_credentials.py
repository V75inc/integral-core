"""Schemas for per-user model credential (BYOK) API — slot-based."""

from __future__ import annotations

from typing import FrozenSet, Literal, Optional, get_args

from pydantic import BaseModel, Field, model_validator

ModelProvider = Literal["openai", "anthropic", "openrouter", "ollama"]

# Providers that can back the ``speech`` (voice input) slot. A subset of
# ``ModelProvider``: only vendors with a streaming speech-to-text API qualify.
SpeechProvider = Literal["openai"]
SPEECH_CAPABLE_PROVIDERS: FrozenSet[str] = frozenset(get_args(SpeechProvider))


class ModelCredentialUpsertRequest(BaseModel):
    """Slot-based BYOK upsert. ``default`` (provider+model) is required.

    ``api_key`` is required on first create; omit or send empty on update to keep
    the stored default key.
    """

    provider: ModelProvider
    model: str = Field(..., min_length=1, max_length=128)
    api_key: Optional[str] = Field(default=None, max_length=512)

    light_model: Optional[str] = Field(default=None, max_length=128)
    light_provider: Optional[ModelProvider] = None
    light_api_key: Optional[str] = Field(default=None, min_length=8, max_length=512)

    heavy_model: Optional[str] = Field(default=None, max_length=128)
    heavy_provider: Optional[ModelProvider] = None
    heavy_api_key: Optional[str] = Field(default=None, min_length=8, max_length=512)

    vision_model: Optional[str] = Field(default=None, max_length=128)
    vision_provider: Optional[ModelProvider] = None
    vision_api_key: Optional[str] = Field(default=None, min_length=8, max_length=512)

    # Speech-to-text (voice input) slot. Opt-in: an empty ``speech_model`` means off.
    speech_model: Optional[str] = Field(default=None, max_length=128)
    speech_provider: Optional[ModelProvider] = None
    speech_api_key: Optional[str] = Field(default=None, min_length=8, max_length=512)

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
    def _dual_provider_requires_keys(self) -> "ModelCredentialUpsertRequest":
        pairs = (
            ("light", self.light_provider, self.light_api_key),
            ("heavy", self.heavy_provider, self.heavy_api_key),
            ("vision", self.vision_provider, self.vision_api_key),
            ("speech", self.speech_provider, self.speech_api_key),
        )
        for label, alt_provider, alt_key in pairs:
            if not alt_provider or alt_provider == self.provider:
                continue
            if not (alt_key or "").strip():
                raise ValueError(
                    f"{label}_api_key is required when {label}_provider "
                    "differs from provider"
                )
        return self


class ModelCredentialResponse(BaseModel):
    provider: ModelProvider
    model: str
    light_provider: Optional[ModelProvider] = None
    light_model: Optional[str] = None
    heavy_provider: Optional[ModelProvider] = None
    heavy_model: Optional[str] = None
    vision_provider: Optional[ModelProvider] = None
    vision_model: Optional[str] = None
    speech_provider: Optional[ModelProvider] = None
    speech_model: Optional[str] = None
    key_fingerprint: str
    light_key_fingerprint: Optional[str] = None
    heavy_key_fingerprint: Optional[str] = None
    vision_key_fingerprint: Optional[str] = None
    speech_key_fingerprint: Optional[str] = None
    is_active: bool = True
    validated_at: Optional[str] = None
    last_used_at: Optional[str] = None
    updated_at: Optional[str] = None


class ModelCredentialValidateRequest(BaseModel):
    provider: ModelProvider
    api_key: str = Field(..., min_length=8, max_length=512)


class ModelCredentialValidateResponse(BaseModel):
    valid: bool
    message: str = ""
