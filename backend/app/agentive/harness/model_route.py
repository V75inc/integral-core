"""Resolve a native model route through Integral's existing credential plane."""

from __future__ import annotations

import os
from typing import Any

from app.agentive.harness.contracts import ResolvedModelRoute


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


async def resolve_native_model_route(
    *, workspace_id: str, default_model: str
) -> ResolvedModelRoute:
    """Resolve workspace BYOK or deployment LiteLLM route for one turn.

    ``default_model`` is supplied by the trusted resident model profile. It is
    never taken from chat text, thread metadata, or a browser field. In hybrid
    and platform-only modes LiteLLM resolves deployment credentials from its
    configured environment; Core does not copy those secrets into route state.
    """
    provider = _provider_for_model(default_model)
    from app.services.model_credential_resolver import (
        resolve_agent_model_override,
    )

    override: dict[str, Any] | None = await resolve_agent_model_override(workspace_id)
    if not override:
        local_route = provider == "ollama"
        model = default_model
        api_base = None
        ollama_num_ctx = ollama_num_predict = None
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
        return ResolvedModelRoute(
            provider="ollama_chat" if local_route else provider,
            model=model,
            api_base=api_base,
            ollama_num_ctx=ollama_num_ctx,
            ollama_num_predict=ollama_num_predict,
            credential_source="local" if local_route else "platform",
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
    else:
        ollama_num_ctx = ollama_num_predict = None
    return ResolvedModelRoute(
        provider=resolved_provider,
        model=model,
        api_base=api_base,
        api_key=api_key if api_key else None,
        ollama_num_ctx=ollama_num_ctx,
        ollama_num_predict=ollama_num_predict,
        credential_source="local" if local_route else "workspace_byok",
    )
