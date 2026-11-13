"""Versioned harness role guidance, distinct from model transport capabilities.

Observations are not certification. Unknown/custom models remain an explicit
advanced choice; recognizable light models are reserved for the dual light role.
"""

from __future__ import annotations

import re
from copy import deepcopy
from typing import Any

POLICY_VERSION = "2026-11-13.2"
_NOTE = (
    "Preferred models are starting points, not certification. Validate your own "
    "workflows. Single/default and heavy roles need a capable model; lighter "
    "models belong only in the light role of automatic light/heavy routing."
)


def _entry(model: str, label: str, evidence: str, *, role: str = "heavy") -> dict:
    return {
        "id": model,
        "label": label,
        "role": role,
        "evidence": evidence,
        "recommended": role == "heavy",
    }


# Frontier defaults reviewed 2026-11-13 against provider catalogs.
# Anthropic contract: https://platform.claude.com/docs/en/models/opus-5-5/migration-guide
# Provider availability is distinct from end-to-end Integral qualification.
_PROVIDERS = {
    "openai": {
        "preferred_heavy": "gpt-6.1-sol",
        "models": [
            _entry("gpt-6.1-sol", "GPT-6.1 Sol", "observed"),
            _entry(
                "gpt-5.4-mini",
                "GPT-5.4 Mini",
                "unverified",
                role="light",
            ),
        ],
    },
    "openrouter": {
        "preferred_heavy": "openai/gpt-6.1-sol",
        "models": [
            _entry(
                "openai/gpt-6.1-sol",
                "GPT-6.1 Sol",
                "candidate",
            ),
            _entry(
                "anthropic/claude-opus-5.5",
                "Claude Opus 5.5",
                "candidate",
            ),
        ],
    },
    "anthropic": {
        "preferred_heavy": "claude-opus-5-5",
        "models": [
            _entry("claude-opus-5-5", "Claude Opus 5.5", "candidate"),
        ],
    },
    "ollama": {
        "preferred_heavy": "deepseek-v4.1-flash",
        "models": [
            _entry(
                "deepseek-v4.1-flash",
                "DeepSeek V4.1 Flash",
                "observed",
            ),
        ],
    },
    "ollama_local": {"preferred_heavy": "", "models": []},
    "custom_openai": {"preferred_heavy": "", "models": []},
}


def model_policy_catalog() -> dict[str, Any]:
    """Return a copy safe for API serialization without exposing credentials."""
    return {"version": POLICY_VERSION, "note": _NOTE, "providers": deepcopy(_PROVIDERS)}


def _identity(provider: str, model: str) -> tuple[str, str]:
    provider, model = provider.strip().lower(), model.strip().lower()
    if not provider and "/" in model:
        provider, model = model.split("/", 1)
    if model.startswith(provider + "/"):
        model = model[len(provider) + 1 :]
    return provider, model


def assess_model(provider: str, model: str) -> dict[str, Any]:
    """Classify role suitability without pretending a model has passed an eval."""
    provider, model = _identity(provider, model)
    # Strip routing prefixes only for light detection; custom models do not gain
    # preferred status by borrowing a model ID from another provider.
    bare = model.rsplit("/", 1)[-1].split(":", 1)[0]
    light = bool(
        re.fullmatch(
            r"gpt-[0-9][a-z0-9.\-]*-(?:mini|nano)(?:-\d{4}-\d{2}-\d{2})?", bare
        )
        or re.fullmatch(r"o[34]-mini(?:-\d{4}-\d{2}-\d{2})?", bare)
        or re.fullmatch(r"claude-[a-z0-9.\-]*haiku[a-z0-9.\-]*", bare)
        or re.fullmatch(r"gemma4:e2b(?:[-:].*)?", model.rsplit("/", 1)[-1])
    )
    if light:
        return {
            "tier": "light",
            "recommended": False,
            "evidence": "light_only",
            "message": "Use this model only as the light model in automatic light/heavy routing. Choose a preferred heavy model for single/default, heavy, or fixed conversation use.",
        }
    for entry in _PROVIDERS.get(provider, {}).get("models", []):
        if entry["id"] == model and entry["role"] == "heavy":
            return {
                "tier": "heavy",
                "recommended": True,
                "evidence": entry["evidence"],
                "message": (
                    "Preferred starting point based on limited workflow observations, not certification. Validate your workflow."
                    if entry["evidence"] == "observed"
                    else "Candidate heavy model; not yet validated in this harness. Test your workflow before relying on it."
                ),
            }
    return {
        "tier": "unverified",
        "recommended": False,
        "evidence": "unverified",
        "message": "Advanced model choice: harness suitability is unverified. Test scaffolding, tool use, and attachment workflows before relying on it.",
    }


class ModelHarnessPolicyError(ValueError):
    """An actionable configuration error, not an internal execution failure."""


def require_heavy_model(provider: str, model: str) -> None:
    """Reject known light models before persisting or running a heavy route."""
    assessment = assess_model(provider, model)
    if assessment["tier"] == "light":
        raise ModelHarnessPolicyError(assessment["message"])
