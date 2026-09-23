"""Optional, measured override of the shipped resident ``agent.yaml``.

The distro file ``agent.override.yaml`` (or ``INTEGRAL_AGENT_OVERLAY``)
may change the persona and a fixed set of orchestrator knobs. It cannot
add an action or replace the action list. Boot copies the shipped tree
and writes the merged file there so jvagent still reads one ``agent.yaml``.
"""

from __future__ import annotations

import logging
import os
import shutil
from pathlib import Path
from typing import Any, Callable

import yaml

logger = logging.getLogger("app.agentive")

_AGENT_YAML = Path("agents/integral/integral_agent/agent.yaml")
_ORCHESTRATOR = "jvagent/orchestrator"
_MODEL_RE_PARTS = 2

# name -> (check, lo, hi). Strings use length bounds. Numbers use value bounds.
_AGENT_CONTEXT: dict[str, tuple[str, float, float]] = {
    "alias": ("str", 1, 120),
    "role": ("str", 1, 8000),
    "interaction_limit": ("int", 1, 100),
}
_ORCHESTRATOR_CONTEXT: dict[str, tuple[str, float, float]] = {
    "model": ("model", 0, 0),
    "model_temperature": ("float", 0.0, 2.0),
    "model_max_tokens": ("int", 0, 128000),
    "light_model": ("model", 0, 0),
    "light_model_temperature": ("float", 0.0, 2.0),
    "light_model_max_tokens": ("int", 1, 128000),
    "activation_budget": ("int", 20, 40),
    "history_limit": ("int", 1, 50),
    "max_concurrent_tools": ("int", 1, 8),
    "observation_max_chars": ("int", 1000, 100000),
    "stale_observation_max_chars": ("int", 500, 50000),
    "observation_full_recent": ("int", 1, 20),
}


def overlay_path() -> Path | None:
    """Return the override file, if the operator provided one."""
    explicit = os.environ.get("INTEGRAL_AGENT_OVERLAY", "").strip()
    if explicit:
        path = Path(explicit).expanduser()
        if not path.is_file():
            raise FileNotFoundError(f"INTEGRAL_AGENT_OVERLAY is not a file: {path}")
        return path.resolve()
    candidate = Path.cwd() / "agent.override.yaml"
    if candidate.is_file():
        return candidate.resolve()
    return None


def apply_agent_override(
    base: dict[str, Any], overlay: dict[str, Any]
) -> dict[str, Any]:
    """Return ``base`` with the allowlisted overlay applied."""
    if not isinstance(overlay, dict):
        raise ValueError("agent.override.yaml must be a mapping")
    unknown = set(overlay) - {"context", "actions"}
    if unknown:
        raise ValueError(f"agent.override.yaml has unknown keys: {sorted(unknown)}")
    merged = yaml.safe_load(yaml.safe_dump(base))
    if "context" in overlay:
        _apply_context(
            merged.setdefault("context", {}), overlay["context"], _AGENT_CONTEXT
        )
    if "actions" in overlay:
        _apply_actions(merged.get("actions") or [], overlay["actions"])
    return merged


def prepare_resident_app_root(shipped: Path) -> Path:
    """Return the app root jvagent should boot. Shipped root when no overlay."""
    overlay = overlay_path()
    if overlay is None:
        return shipped
    patch = yaml.safe_load(overlay.read_text(encoding="utf-8"))
    if not patch:
        return shipped
    runtime = overlay.parent / ".integral" / "agent-runtime"
    if runtime.exists():
        shutil.rmtree(runtime)
    shutil.copytree(
        shipped,
        runtime,
        ignore=shutil.ignore_patterns("__pycache__", "*.pyc"),
    )
    agent_yaml = runtime / _AGENT_YAML
    base = yaml.safe_load(agent_yaml.read_text(encoding="utf-8"))
    merged = apply_agent_override(base, patch)
    agent_yaml.write_text(
        yaml.safe_dump(merged, sort_keys=False, allow_unicode=True),
        encoding="utf-8",
    )
    mode = os.getenv("JVAGENT_UPDATE_MODE", "source").strip().lower()
    if mode == "merge":
        logger.warning(
            "agent override %s is present but JVAGENT_UPDATE_MODE=merge "
            "keeps stored action context; set source for one boot to apply it",
            overlay,
        )
    else:
        logger.info("applied agent override %s", overlay)
    return runtime


def _apply_context(
    target: dict[str, Any],
    overlay: Any,
    allow: dict[str, tuple[str, float, float]],
) -> None:
    if not isinstance(overlay, dict):
        raise ValueError("override context must be a mapping")
    unknown = set(overlay) - set(allow)
    if unknown:
        raise ValueError(f"agent override has unknown context keys: {sorted(unknown)}")
    for key, value in overlay.items():
        kind, lo, hi = allow[key]
        target[key] = _check(key, value, kind, lo, hi)


def _apply_actions(base_actions: list[Any], overlay: Any) -> None:
    if not isinstance(overlay, list):
        raise ValueError("override actions must be a list")
    by_name = {
        item.get("action"): item
        for item in base_actions
        if isinstance(item, dict) and item.get("action")
    }
    for item in overlay:
        if not isinstance(item, dict) or "action" not in item:
            raise ValueError("each override action needs an action name")
        name = item["action"]
        if name != _ORCHESTRATOR:
            raise ValueError(
                f"agent override may only adjust {_ORCHESTRATOR}, not {name}"
            )
        if name not in by_name:
            raise ValueError(f"shipped agent.yaml has no action {name}")
        extra = set(item) - {"action", "context"}
        if extra:
            raise ValueError(f"agent override action has unknown keys: {sorted(extra)}")
        if "context" not in item:
            continue
        _apply_context(
            by_name[name].setdefault("context", {}),
            item["context"],
            _ORCHESTRATOR_CONTEXT,
        )


def _check(key: str, value: Any, kind: str, lo: float, hi: float) -> Any:
    checkers: dict[str, Callable[[], Any]] = {
        "str": lambda: _check_str(key, value, int(lo), int(hi)),
        "int": lambda: _check_int(key, value, int(lo), int(hi)),
        "float": lambda: _check_float(key, value, lo, hi),
        "model": lambda: _check_model(key, value),
    }
    return checkers[kind]()


def _check_str(key: str, value: Any, lo: int, hi: int) -> str:
    if not isinstance(value, str):
        raise ValueError(f"{key} must be a string")
    text = value.strip()
    if not lo <= len(text) <= hi:
        raise ValueError(f"{key} length must be {lo}..{hi}")
    return text


def _check_int(key: str, value: Any, lo: int, hi: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"{key} must be an integer")
    if not lo <= value <= hi:
        raise ValueError(f"{key} must be {lo}..{hi}")
    return value


def _check_float(key: str, value: Any, lo: float, hi: float) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{key} must be a number")
    number = float(value)
    if not lo <= number <= hi:
        raise ValueError(f"{key} must be {lo}..{hi}")
    return number


def _check_model(key: str, value: Any) -> str:
    text = _check_str(key, value, 3, 120)
    provider, _, model = text.partition("/")
    if not provider or not model or "/" in model or " " in text:
        raise ValueError(f"{key} must look like provider/model")
    if len(text.split("/")) != _MODEL_RE_PARTS:
        raise ValueError(f"{key} must look like provider/model")
    return text
