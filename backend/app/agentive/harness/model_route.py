"""Resolve a native model route through Integral's existing credential plane."""

from __future__ import annotations

import hashlib
import json
import os
from typing import Any

from app.agentive.harness.contracts import ResolvedModelRoute
from app.services.model_provider_endpoints import OLLAMA_CLOUD_API_BASE


async def resolve_native_model_route(
    *, workspace_id: str, default_model: str
) -> ResolvedModelRoute:
    """Reject malformed operator settings with a safe, actionable reason."""
    try:
        return await _resolve_native_model_route(
            workspace_id=workspace_id, default_model=default_model
        )
    except ValueError as exc:
        from app.api.errors import ServiceUnavailableError

        raise ServiceUnavailableError(
            message="Integral AI model configuration is invalid. Review model setup.",
            details={"reason": "native_model_configuration_invalid"},
        ) from exc


def _provider_for_model(model: str) -> str:
    provider, separator, name = model.partition("/")
    if not separator or not provider.strip() or not name.strip():
        raise ValueError("configured LiteLLM model must use provider/model form")
    return provider


def _local_ollama_num_ctx() -> int:
    """Return a usable per-request local Ollama context window."""
    # A single standards-compliant Integral skill can itself be several
    # thousand tokens. 16k leaves too little room for that skill, broker tool
    # schemas, and provider framing on real scaffold turns. Keep this scoped to
    # the Ollama route; hosted OpenAI-compatible routes manage context at the
    # provider.
    raw = os.getenv("INTEGRAL_NATIVE_OLLAMA_NUM_CTX", "32768").strip()
    try:
        value = int(raw)
    except ValueError as exc:
        raise ValueError("INTEGRAL_NATIVE_OLLAMA_NUM_CTX must be an integer") from exc
    if not 512 <= value <= 131072:
        raise ValueError(
            "INTEGRAL_NATIVE_OLLAMA_NUM_CTX must be between 512 and 131072"
        )
    return value


def _local_ollama_num_predict() -> int:
    """Return a useful local Ollama output budget, above LiteLLM's fallback."""
    raw = os.getenv("INTEGRAL_NATIVE_OLLAMA_NUM_PREDICT", "8192").strip()
    try:
        value = int(raw)
    except ValueError as exc:
        raise ValueError(
            "INTEGRAL_NATIVE_OLLAMA_NUM_PREDICT must be an integer"
        ) from exc
    if not 1 <= value <= 131072:
        raise ValueError(
            "INTEGRAL_NATIVE_OLLAMA_NUM_PREDICT must be between 1 and " "131072"
        )
    return value


def _local_ollama_generation_settings() -> tuple[int, int]:
    """Keep prompt/context capacity separate from the requested output budget."""
    num_ctx = _local_ollama_num_ctx()
    num_predict = _local_ollama_num_predict()
    if num_ctx < num_predict + 1024:
        raise ValueError(
            "INTEGRAL_NATIVE_OLLAMA_NUM_CTX must exceed "
            "INTEGRAL_NATIVE_OLLAMA_NUM_PREDICT by at least 1024 tokens "
            "to leave room for the prompt"
        )
    return num_ctx, num_predict


def _local_ollama_think_setting() -> str | bool | None:
    """Resolve Ollama's model-defined thinking setting from trusted config."""
    raw = os.getenv("INTEGRAL_NATIVE_OLLAMA_THINK", "").strip()
    if not raw:
        return None
    normalized = raw.lower()
    if normalized in {"true", "false"}:
        return normalized == "true"
    return raw


def _local_ollama_clear_thinking() -> bool | None:
    """Resolve an optional model-template setting without changing defaults."""
    raw = os.getenv("INTEGRAL_NATIVE_OLLAMA_CLEAR_THINKING", "").strip().lower()
    if not raw:
        return None
    if raw not in {"true", "false", "1", "0"}:
        raise ValueError("INTEGRAL_NATIVE_OLLAMA_CLEAR_THINKING must be true or false")
    return raw in {"true", "1"}


async def _resolve_native_model_route(
    *, workspace_id: str, default_model: str
) -> ResolvedModelRoute:
    """Resolve workspace BYOK or deployment LiteLLM route for one turn.

    ``default_model`` is supplied by the trusted resident model profile. It is
    never taken from chat text, thread metadata, or a browser field. In hybrid
    and platform-only modes LiteLLM resolves deployment credentials from its
    configured environment; Core does not copy those secrets into route state.
    """
    from app.services.model_credential_resolver import (
        resolve_agent_model_override,
    )

    override: dict[str, Any] | None = await resolve_agent_model_override(
        workspace_id, include_credential_identity=True
    )
    if not override:
        if not default_model.strip():
            from app.api.errors import ServiceUnavailableError

            raise ServiceUnavailableError(
                message=(
                    "Integral AI needs a model. Add a model in Settings, or set "
                    "INTEGRAL_NATIVE_MODEL and its provider credentials on the "
                    "server. Integral does not fall back to another harness."
                ),
                details={"reason": "native_model_not_configured"},
            )
        provider = _provider_for_model(default_model)
        local_route = provider == "ollama"
        model = default_model
        api_base = None
        ollama_num_ctx = ollama_num_predict = None
        ollama_think = ollama_clear_thinking = None
        if local_route:
            # The `ollama/` LiteLLM adapter flattens Gemma's native reasoning
            # and tool-call parts into content text. That makes valid native
            # tool calls look like malformed assistant JSON to Core's
            # translator. Use `ollama_chat/` for deployment routes too, so
            # Pydantic AI receives typed parts regardless of credential source.
            model = f"ollama_chat/{default_model.split('/', 1)[1]}"
            api_base = (
                os.getenv("OLLAMA_API_BASE", "http://localhost:11434").strip()
                or "http://localhost:11434"
            ).rstrip("/")
            ollama_num_ctx, ollama_num_predict = _local_ollama_generation_settings()
            ollama_think = _local_ollama_think_setting()
            ollama_clear_thinking = _local_ollama_clear_thinking()
        if not local_route:
            from app.services.host_hooks import assert_platform_quota

            await assert_platform_quota(workspace_id)
        return ResolvedModelRoute(
            provider="ollama_chat" if local_route else provider,
            model=model,
            api_base=api_base,
            ollama_num_ctx=ollama_num_ctx,
            ollama_num_predict=ollama_num_predict,
            ollama_think=ollama_think,
            ollama_clear_thinking=ollama_clear_thinking,
            credential_source="local" if local_route else "platform",
            # The operator supplies a versioned nonsecret reference for the
            # environment/remote daemon credential generation. Missing means
            # unattributed; never invent identity from a provider/model name.
            credential_ref=(
                _local_credential_ref("deployment", api_base)
                if local_route
                else _deployment_credential_ref()
            ),
        )

    slots = override.get("slots") or {}
    default = slots.get("default") if isinstance(slots, dict) else None
    if not isinstance(default, dict):
        raise ValueError("workspace model override has no default LiteLLM route")
    model = str(default.get("model") or "").strip()
    resolved_provider = _provider_for_model(model)
    raw_key = default.get("api_key")
    api_key = str(raw_key) if raw_key is not None else None
    local_route = resolved_provider == "ollama" and not api_key
    api_base = None
    if local_route:
        # The local `ollama/` LiteLLM adapter is OpenAI-compatible but flattens
        # Gemma's native reasoning/tool envelope into content text. Use
        # LiteLLM's native chat adapter so Pydantic AI receives tool calls and
        # thinking as distinct parts while retaining the same SDK accounting
        # and local Ollama endpoint.
        model_name = model.split("/", 1)[1]
        resolved_provider = "ollama_chat"
        model = f"ollama_chat/{model_name}"
        api_base = (
            os.getenv("OLLAMA_API_BASE", "http://localhost:11434").strip()
            or "http://localhost:11434"
        ).rstrip("/")
        ollama_num_ctx, ollama_num_predict = _local_ollama_generation_settings()
        ollama_think = _local_ollama_think_setting()
        ollama_clear_thinking = _local_ollama_clear_thinking()
    else:
        ollama_num_ctx = ollama_num_predict = None
        ollama_think = ollama_clear_thinking = None
        if resolved_provider == "ollama":
            # A saved Cloud credential was validated at ollama.com. Never let
            # an unrelated host's local-daemon setting reroute that credential.
            resolved_provider = "ollama_chat"
            model = f"ollama_chat/{model.split('/', 1)[1]}"
            api_base = OLLAMA_CLOUD_API_BASE
    return ResolvedModelRoute(
        provider=resolved_provider,
        model=model,
        api_base=api_base,
        api_key=api_key if api_key else None,
        ollama_num_ctx=ollama_num_ctx,
        ollama_num_predict=ollama_num_predict,
        ollama_think=ollama_think,
        ollama_clear_thinking=ollama_clear_thinking,
        credential_source="local" if local_route else "workspace_byok",
        credential_ref=(
            _local_credential_ref(override.get("credential_ref"), api_base)
            if local_route
            else override.get("credential_ref")
        ),
    )


def _deployment_credential_ref() -> str | None:
    raw = os.getenv("INTEGRAL_NATIVE_CREDENTIAL_REF")
    if raw is None or raw == "":
        return None
    if raw != raw.strip() or len(raw) > 255 or not raw.strip():
        raise ValueError(
            "deployment credential reference must be canonical and bounded"
        )
    return raw


def _local_credential_ref(profile_ref: str | None, api_base: str | None) -> str | None:
    # A keyless workspace model profile does not identify the remote daemon's
    # credentials. Bind both profile generation and operator-owned daemon
    # generation, including the endpoint. Cloud-on-Ollama is not free-by-default.
    deployment = _deployment_credential_ref()
    if not profile_ref or not deployment:
        return None
    identity = json.dumps([profile_ref, deployment, api_base], separators=(",", ":"))
    return "local-model-generation:" + hashlib.sha256(identity.encode()).hexdigest()
