"""Provider parameter support and conservative generation-setting mapping."""

from __future__ import annotations

import os
from functools import lru_cache
from typing import Any

from app.schemas.model_settings import ModelGenerationSettings


@lru_cache(maxsize=256)
def model_capabilities(provider: str, model: str) -> dict[str, Any]:
    """Read conservative model capabilities from local LiteLLM metadata."""
    import litellm

    adapter = {
        "custom_openai": "openai",
        "ollama_local": "ollama_chat",
        "ollama": "ollama_chat",
    }.get(provider, provider)
    try:
        parameters = set(
            litellm.get_supported_openai_params(
                model=model, custom_llm_provider=adapter
            )
            or []
        )
    except Exception:
        parameters = set()
    # Custom endpoints have no verified model contract. Standard options can
    # be authored, but do not advertise reasoning based only on a model name.
    known = provider != "custom_openai"
    try:
        info = litellm.get_model_info(model=model, custom_llm_provider=adapter)
    except Exception:
        info = {}
    reasoning = (
        known
        and info.get("supports_reasoning") is True
        and "reasoning_effort" in parameters
    )
    efforts: list[str] = []
    if reasoning:
        from litellm.router_utils.reasoning_effort_capability import (
            resolve_supported_reasoning_efforts,
        )

        # Use the pinned adapter's declared levels/flags, including special
        # variants. A reasoning flag alone does not prove a universal ladder.
        declared = resolve_supported_reasoning_efforts(info, deployment_is_mapped=True)
        allowed = {"none", "minimal", "low", "medium", "high", "xhigh"}
        efforts = [level for level in (declared or ()) if level in allowed]
    return {
        "reasoning_efforts": efforts,
        "max_tokens": "max_tokens" in parameters
        or "max_completion_tokens" in parameters,
        "temperature": "temperature" in parameters and not reasoning,
        "top_p": "top_p" in parameters and not reasoning,
        "verbosity": "verbosity" in parameters,
        "thinking_budget": provider == "anthropic"
        and info.get("supports_reasoning") is True
        and "thinking" in parameters,
        "context_window": provider == "ollama_local",
        "verified": known and info.get("supports_reasoning") is not None,
        "output_limit": info.get("max_output_tokens"),
        "native_context": info.get("max_input_tokens"),
        "tool_calling": info.get("supports_function_calling") if known else None,
        "vision": info.get("supports_vision") if known else None,
    }


def validate_model_settings(
    provider: str, model: str, value: dict[str, Any] | None
) -> dict[str, Any]:
    """Reject unsupported options and values exceeding deployment ceilings."""
    settings = ModelGenerationSettings.model_validate(value or {}).model_dump(
        exclude_none=True
    )
    if not settings:
        return {}
    capabilities = model_capabilities(provider, model)
    for key in settings:
        if key in {
            "request_timeout",
            "idle_timeout",
            "model_request_limit",
            "tool_call_limit",
            "turn_token_limit",
            "turn_timeout",
        }:
            continue
        if key == "reasoning_effort":
            if settings[key] not in capabilities["reasoning_efforts"]:
                raise ValueError(
                    "Reasoning effort is not supported for this provider/model"
                )
        elif not capabilities.get(key):
            raise ValueError(f"{key} is not supported for this provider/model")
    output_limit = capabilities.get("output_limit")
    if (
        settings.get("max_tokens")
        and isinstance(output_limit, int)
        and output_limit > 0
        and settings["max_tokens"] > output_limit
    ):
        raise ValueError(
            f"Output limit cannot exceed this model's {output_limit} tokens"
        )
    if provider == "ollama_local" and any(
        k in settings for k in ("max_tokens", "context_window")
    ):
        context = settings.get(
            "context_window", int(os.getenv("INTEGRAL_NATIVE_OLLAMA_NUM_CTX", "32768"))
        )
        output = settings.get(
            "max_tokens", int(os.getenv("INTEGRAL_NATIVE_OLLAMA_NUM_PREDICT", "8192"))
        )
        if context < output + 1024:
            raise ValueError(
                "Local context must leave at least 1024 tokens beyond the effective output limit"
            )
    from app.config import settings as deployment

    if (
        settings.get("request_timeout", 0)
        > deployment.INTEGRAL_NATIVE_MODEL_REQUEST_TIMEOUT_SECONDS
    ):
        raise ValueError("Request timeout exceeds the deployment ceiling")
    if (
        settings.get("idle_timeout", 0)
        > deployment.INTEGRAL_NATIVE_MODEL_IDLE_TIMEOUT_SECONDS
    ):
        raise ValueError("Idle timeout exceeds the deployment ceiling")
    for key, ceiling in {
        "model_request_limit": deployment.INTEGRAL_NATIVE_TURN_REQUEST_LIMIT,
        "turn_token_limit": deployment.INTEGRAL_NATIVE_TURN_TOKEN_LIMIT,
        "turn_timeout": deployment.INTEGRAL_HARNESS_CHAT_TURN_TIMEOUT_SECONDS,
    }.items():
        if settings.get(key, 0) > ceiling:
            raise ValueError(f"{key} exceeds the deployment ceiling")
    return settings


def provider_model_options(settings: dict[str, Any]) -> dict[str, Any]:
    """Map validated generation options and exclude host-only budgets."""
    parsed = ModelGenerationSettings.model_validate(settings)
    result = parsed.model_dump(
        exclude_none=True,
        exclude={
            "model_request_limit",
            "tool_call_limit",
            "turn_token_limit",
            "turn_timeout",
            "context_window",
            "request_timeout",
            "idle_timeout",
            "thinking_budget",
        },
    )
    if parsed.thinking_budget is not None:
        result["thinking"] = {
            "type": "enabled",
            "budget_tokens": parsed.thinking_budget,
        }
    return result
