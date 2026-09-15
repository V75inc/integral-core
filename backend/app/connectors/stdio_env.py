"""Merge catalog env defaults and persist stdio MCP token files.

Intuit's QuickBooks MCP server rotates the refresh token on each use and
writes the new value to ``QUICKBOOKS_TOKEN_STORE_PATH``. Stashing the
original token on ``auth_state.env`` would break the next spawn.
"""

from __future__ import annotations

import os
import re
import uuid
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

# backend/app/connectors/stdio_env.py → backend/.data/mcp-stdio
TOKEN_STORE_ROOT = Path(__file__).resolve().parents[2] / ".data" / "mcp-stdio"

_QB_REQUIRED_AFTER_FILL = (
    "QUICKBOOKS_CLIENT_ID",
    "QUICKBOOKS_CLIENT_SECRET",
    "QUICKBOOKS_REFRESH_TOKEN",
    "QUICKBOOKS_REALM_ID",
)
_QB_PERSIST_ENV = ("QUICKBOOKS_TOKEN_STORE_PATH", "QUICKBOOKS_ENVIRONMENT")
_SAFE_DIR = re.compile(r"[^a-zA-Z0-9._-]+")
_DISABLE_KEYS = (
    "QUICKBOOKS_DISABLE_WRITE",
    "QUICKBOOKS_DISABLE_UPDATE",
    "QUICKBOOKS_DISABLE_DELETE",
)


def spawn_env_from_quickbooks_oauth(
    *,
    auth_state: Dict[str, Any],
    refresh_token: str,
    realm_id: str,
    env_defaults: Optional[Dict[str, str]] = None,
) -> Dict[str, str]:
    """Build Intuit MCP process env from an Integral OAuth callback."""
    from app.config import settings

    env: Dict[str, str] = dict(env_defaults or auth_state.get("env_defaults") or {})
    client_id = (
        str(auth_state.get("client_id") or "").strip()
        or (settings.QUICKBOOKS_CLIENT_ID or os.getenv("QUICKBOOKS_CLIENT_ID") or "")
    ).strip()
    client_secret = (
        str(auth_state.get("client_secret") or "").strip()
        or (
            settings.QUICKBOOKS_CLIENT_SECRET
            or os.getenv("QUICKBOOKS_CLIENT_SECRET")
            or ""
        )
    ).strip()
    environment = (
        str(auth_state.get("environment") or "").strip()
        or (settings.QUICKBOOKS_ENVIRONMENT or "sandbox")
    ).strip()
    env.update(
        {
            "QUICKBOOKS_CLIENT_ID": client_id,
            "QUICKBOOKS_CLIENT_SECRET": client_secret,
            "QUICKBOOKS_REFRESH_TOKEN": (refresh_token or "").strip(),
            "QUICKBOOKS_REALM_ID": (realm_id or "").strip(),
            "QUICKBOOKS_ENVIRONMENT": environment,
        }
    )
    for key in _DISABLE_KEYS:
        override = str(auth_state.get(key) or "").strip()
        if override:
            env[key] = override
    return env


def merge_install_env(
    entry: Dict[str, Any], collected: Dict[str, str]
) -> Dict[str, str]:
    """env_defaults, then settings/process env for empty optional fields, then secrets."""
    from app.config import settings

    merged: Dict[str, str] = dict(entry.get("env_defaults") or {})
    for field in entry.get("auth", {}).get("fields") or []:
        if not isinstance(field, dict):
            continue
        name = str(field.get("name") or "").strip()
        if not name or collected.get(name):
            continue
        env_val = (os.getenv(name) or "").strip()
        if env_val:
            merged[name] = env_val
            continue
        settings_val = getattr(settings, name, None)
        if isinstance(settings_val, str) and settings_val.strip():
            merged[name] = settings_val.strip()
    merged.update({k: v for k, v in collected.items() if v})
    return merged


def _dotenv_escape(value: str) -> str:
    if any(ch in value for ch in " \n\r#\"'\\"):
        escaped = value.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n")
        return f'"{escaped}"'
    return value


def write_dotenv_file(path: Path, values: Dict[str, str]) -> Path:
    """Write a 0600 dotenv file and return its resolved path."""
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = [
        f"{key}={_dotenv_escape(str(value))}"
        for key, value in values.items()
        if key and str(value)
    ]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    os.chmod(path, 0o600)
    return path.resolve()


def persist_quickbooks_token_store(
    *,
    workspace_id: str,
    env: Dict[str, str],
) -> Tuple[Path, Dict[str, str], Dict[str, str]]:
    """Write Intuit credentials to a token file.

    Returns ``(path, spawn_env, persist_env)``. ``spawn_env`` is passed to
    the child on first mount. ``persist_env`` is what stays on
    ``auth_state.env`` so later spawns read the rotated refresh token from
    the file instead of a stale copy.
    """
    missing = [
        key for key in _QB_REQUIRED_AFTER_FILL if not (env.get(key) or "").strip()
    ]
    if missing:
        raise ValueError(
            "QuickBooks MCP requires "
            + ", ".join(missing)
            + " (install sheet or QUICKBOOKS_* server env)."
        )
    safe_ws = _SAFE_DIR.sub("_", (workspace_id or "ws").strip())[:64] or "ws"
    path = write_dotenv_file(
        TOKEN_STORE_ROOT / safe_ws / f"{uuid.uuid4().hex}.env",
        dict(env),
    )
    spawn_env = dict(env)
    spawn_env["QUICKBOOKS_TOKEN_STORE_PATH"] = str(path)
    persist_env = {key: spawn_env[key] for key in _QB_PERSIST_ENV if spawn_env.get(key)}
    return path, spawn_env, persist_env


def delete_token_store_file(path: Any) -> bool:
    """Delete one persisted stdio token file. Returns True when removed.

    #17: ``persist_quickbooks_token_store`` wrote the client secret and the
    refresh token to disk and nothing ever deleted them — unmounting or
    deleting the connector left a 0600 dotenv full of live Intuit credentials
    under ``backend/.data/mcp-stdio/`` forever.

    Refuses any path outside ``TOKEN_STORE_ROOT``: the value arrives from
    ``auth_state.env``, which an operator can PATCH, so an unguarded unlink
    here would be an arbitrary-file-delete primitive.
    """
    raw = str(path or "").strip()
    if not raw:
        return False
    try:
        resolved = Path(raw).resolve()
        root = TOKEN_STORE_ROOT.resolve()
    except OSError:
        return False
    if not resolved.is_relative_to(root):
        return False
    try:
        resolved.unlink()
        return True
    except FileNotFoundError:
        return False
    except OSError:
        return False


def purge_token_store_for_auth_state(auth_state: Dict[str, Any]) -> int:
    """Delete every token-store file referenced by a connector's auth_state."""
    removed = 0
    env = (auth_state or {}).get("env")
    if isinstance(env, dict):
        for key in _QB_PERSIST_ENV[:1]:  # QUICKBOOKS_TOKEN_STORE_PATH
            if delete_token_store_file(env.get(key)):
                removed += 1
    return removed
