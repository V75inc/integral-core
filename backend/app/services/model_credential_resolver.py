"""Resolve per-turn jvagent model override from workspace owner BYOK."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Dict, Literal, Optional

from app.config import settings
from app.models.credentials import UserModelCredential
from app.schemas.model_credentials import SPEECH_CAPABLE_PROVIDERS
from app.services.model_credentials import (
    decrypt_credential_api_key,
    decrypt_credential_heavy_api_key,
    decrypt_credential_light_api_key,
    decrypt_credential_speech_api_key,
    decrypt_credential_vision_api_key,
    get_active_credential_for_user,
    touch_credential_last_used,
)
from app.services.workspace_permissions import get_workspace_owner_user_id

logger = logging.getLogger(__name__)


class ModelKeyRequiredError(Exception):
    """Raised when BYO strict mode requires an owner credential that is missing."""


# Provider slugs Integral stores on a credential. Each is also a LiteLLM
# route prefix, so composing ``<slug>/<model>`` yields a valid LiteLLM model id.
_LITELLM_ROUTE_PREFIXES = frozenset({"openai", "anthropic", "openrouter", "ollama"})


def litellm_model_id(provider: str, model: str) -> str:
    """Compose a stored ``provider`` + bare ``model`` into LiteLLM's id form.

    The agent is LiteLLM-only (ADR-0045), so the provider must travel *inside*
    the model id -- LiteLLM infers the route from the prefix and rejects a bare
    id with "LLM Provider NOT provided". Integral stores the two separately
    because per-provider model actions used to pick the route.

    Idempotent on an id the user already prefixed with its own provider. Note
    the check is deliberately ``<provider>/`` and not merely "contains a slash":
    an OpenRouter model id is itself ``vendor/model``
    (``anthropic/claude-sonnet-4-5``) and still needs the ``openrouter/`` route
    prefix in front of it.
    """
    slug = (provider or "").strip().lower()
    model_id = (model or "").strip()
    if not model_id or not slug:
        return model_id
    if model_id.lower().startswith(f"{slug}/"):
        return model_id
    if slug not in _LITELLM_ROUTE_PREFIXES:
        return model_id
    return f"{slug}/{model_id}"


def _slot_entry(
    provider: str,
    model: str,
    api_key: str,
) -> Dict[str, str]:
    # ``litellm`` as the slot provider routes dispatch to
    # LiteLLMLanguageModelAction deterministically. Sending the stored slug
    # instead resolves to a per-provider action the agent no longer enables,
    # and dispatch falls through with the model id uncomposed.
    return {
        "provider": "litellm",
        "model": litellm_model_id(provider, model),
        "api_key": api_key,
    }


def _optional_slot(
    *,
    model: str,
    provider: str,
    default_provider: str,
    api_key: str,
) -> Optional[Dict[str, str]]:
    model_id = (model or "").strip()
    if not model_id:
        return None
    slug = (provider or default_provider or "").strip()
    return _slot_entry(slug, model_id, api_key)


async def resolve_agent_model_override(
    workspace_id: Optional[str],
) -> Optional[Dict[str, Any]]:
    """Return jvagent override dict (canonical ``slots`` map) for this turn."""
    mode = (settings.INTEGRAL_AGENT_KEY_MODE or "hybrid").strip().lower()
    if mode == "platform_only":
        return None

    if not workspace_id:
        if mode == "byo_strict":
            raise ModelKeyRequiredError("workspace scope required for agent turn")
        return None

    owner_user_node_id = await get_workspace_owner_user_id(workspace_id)
    if not owner_user_node_id:
        if mode == "byo_strict":
            raise ModelKeyRequiredError("workspace has no owner")
        return None

    from app.services.permissions import get_user_node

    owner = await get_user_node(owner_user_node_id)
    if not owner or not owner.user_id:
        if mode == "byo_strict":
            raise ModelKeyRequiredError("workspace owner not found")
        return None

    record = await get_active_credential_for_user(owner.user_id)
    if not record:
        if mode == "byo_strict":
            raise ModelKeyRequiredError("model_key_required")
        return None

    default_key = decrypt_credential_api_key(record)
    if not default_key:
        if mode == "byo_strict":
            raise ModelKeyRequiredError("stored credential could not be decrypted")
        logger.warning("BYOK credential decrypt failed for user_id=%s", owner.user_id)
        return None

    default_provider = record.provider
    slots: Dict[str, Dict[str, str]] = {
        "default": _slot_entry(
            default_provider,
            record.model,
            default_key,
        ),
    }

    light = _optional_slot(
        model=record.light_model,
        provider=record.light_provider or default_provider,
        default_provider=default_provider,
        api_key=decrypt_credential_light_api_key(record),
    )
    if light:
        slots["light"] = light

    heavy = _optional_slot(
        model=record.heavy_model,
        provider=record.heavy_provider or default_provider,
        default_provider=default_provider,
        api_key=decrypt_credential_heavy_api_key(record),
    )
    if heavy:
        slots["heavy"] = heavy

    vision = _optional_slot(
        model=record.vision_model,
        provider=record.vision_provider or default_provider,
        default_provider=default_provider,
        api_key=decrypt_credential_vision_api_key(record),
    )
    if vision:
        slots["vision"] = vision

    override: Dict[str, Any] = {"slots": slots}

    # Legacy flat keys — older jvagent builds still read these.
    override["provider"] = "litellm"
    override["model"] = litellm_model_id(default_provider, record.model)
    override["api_key"] = default_key
    if record.light_model:
        override["light_model"] = litellm_model_id(
            record.light_provider or default_provider, record.light_model
        )
        override["light_provider"] = "litellm"
        light_key = decrypt_credential_light_api_key(record)
        if light_key != default_key:
            override["light_api_key"] = light_key

    try:
        await touch_credential_last_used(record)
    except Exception:
        logger.exception("touch_credential_last_used failed")

    return override


@dataclass(frozen=True)
class SpeechCredential:
    """Resolved voice-input credential for one workspace.

    ``api_key`` is excluded from ``repr`` so a stray log line never prints it.
    """

    provider: str
    model: str
    api_key: str = field(repr=False)
    source: Literal["byok", "platform"]


def _platform_speech_credential() -> Optional[SpeechCredential]:
    """Deployment key for voice input — off unless the operator opts in.

    Dictation on the platform key bills the deployment for every workspace,
    so ``SPEECH_ALLOW_PLATFORM_KEY`` gates it independently of the chat-model
    key mode.
    """
    if not settings.SPEECH_ALLOW_PLATFORM_KEY:
        return None
    key = (settings.OPENAI_API_KEY or "").strip()
    if not key:
        return None
    return SpeechCredential(
        provider="openai",
        model=settings.SPEECH_STREAM_MODEL_DEFAULT,
        api_key=key,
        source="platform",
    )


async def _workspace_owner_credential(
    workspace_id: str,
) -> Optional[UserModelCredential]:
    owner_user_node_id = await get_workspace_owner_user_id(workspace_id)
    if not owner_user_node_id:
        return None

    from app.services.permissions import get_user_node

    owner = await get_user_node(owner_user_node_id)
    if not owner or not owner.user_id:
        return None
    return await get_active_credential_for_user(owner.user_id)


async def resolve_speech_credential(
    workspace_id: Optional[str],
    *,
    touch: bool = True,
) -> Optional[SpeechCredential]:
    """Return the voice-input credential for ``workspace_id``, or None.

    Same key-mode rules as the agent's model override:

    - ``platform_only`` — platform key only (if ``SPEECH_ALLOW_PLATFORM_KEY``).
    - ``hybrid`` — the workspace owner's speech slot, else the platform key.
    - ``byo_strict`` — the owner's speech slot only.

    Unlike chat, a missing credential is never an error here: the caller
    falls back to in-browser recognition. Pass ``touch=False`` for reads that
    don't spend the key (e.g. showing which engine is available) so
    ``last_used_at`` keeps meaning "last used".
    """
    mode = (settings.INTEGRAL_AGENT_KEY_MODE or "hybrid").strip().lower()
    if mode == "platform_only":
        return _platform_speech_credential()

    if workspace_id:
        record = await _workspace_owner_credential(workspace_id)
        speech_model = (record.speech_model or "").strip() if record else ""
        if record and speech_model:
            provider = (record.speech_provider or record.provider or "").lower()
            if provider in SPEECH_CAPABLE_PROVIDERS:
                try:
                    key = decrypt_credential_speech_api_key(record)
                except Exception:
                    logger.warning(
                        "speech credential decrypt failed for user_id=%s",
                        record.user_id,
                    )
                    key = ""
                if key:
                    if touch:
                        try:
                            await touch_credential_last_used(record)
                        except Exception:
                            logger.exception("touch_credential_last_used failed")
                    return SpeechCredential(
                        provider=provider,
                        model=speech_model,
                        api_key=key,
                        source="byok",
                    )

    if mode == "byo_strict":
        return None
    return _platform_speech_credential()
