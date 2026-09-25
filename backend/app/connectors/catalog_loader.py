"""Load curated in-repo connector packages (ADR-010 catalog layer).

Being present in ``catalog/*.yaml`` is the vetting gate. Invalid YAML or
an incomplete document raises at load time so tests and boot fail loud.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List, Optional

import yaml

CATALOG_DIR = Path(__file__).resolve().parent / "catalog"

_VALID_CATEGORIES = frozenset({"native", "mcp_server", "mcp_package"})
_VALID_KINDS = frozenset({"sync", "mcp"})
_VALID_AUTH = frozenset({"oauth2", "api_key", "env", "none", "headers"})
_VALID_TRANSPORT = frozenset({"stdio", "streamable_http"})
_VALID_FIELD_CONTROLS = frozenset({"text", "toggle"})

_CACHE: Optional[List[Dict[str, Any]]] = None
_CACHE_STAMP: Optional[tuple] = None


def reset_catalog_cache() -> None:
    """Test helper — drop the in-process catalog cache."""
    global _CACHE, _CACHE_STAMP
    _CACHE = None
    _CACHE_STAMP = None


def _catalog_stamp(root: Path) -> tuple:
    return tuple(
        sorted((path.name, path.stat().st_mtime_ns) for path in root.glob("*.yaml"))
    )


def _require_str(doc: Dict[str, Any], key: str, *, path: Path) -> str:
    value = doc.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{path.name}: {key!r} is required")
    return value.strip()


def _normalize_oauth(
    raw: Any, *, path: Path, kind: str, auth_type: str, transport: Optional[str]
) -> Optional[Dict[str, Any]]:
    """Pre-registered OAuth (Google Drive). Probe-driven MCP OAuth uses none.

    Stdio MCP oauth2 (QuickBooks) uses Integral's native Intuit consent
    flow — no catalog ``oauth:`` HTTP endpoints.
    """
    if raw is None:
        if kind == "mcp" and auth_type == "oauth2" and transport != "stdio":
            raise ValueError(
                f"{path.name}: oauth block is required for HTTP MCP oauth2 packages"
            )
        return None
    if not isinstance(raw, dict):
        raise ValueError(f"{path.name}: oauth must be a mapping")
    authorization_endpoint = str(raw.get("authorization_endpoint") or "").strip()
    token_endpoint = str(raw.get("token_endpoint") or "").strip()
    if not authorization_endpoint or not token_endpoint:
        raise ValueError(
            f"{path.name}: oauth.authorization_endpoint and "
            "oauth.token_endpoint are required"
        )
    scopes_raw = raw.get("scopes") or []
    if not isinstance(scopes_raw, list):
        raise ValueError(f"{path.name}: oauth.scopes must be a list")
    scopes = [str(s).strip() for s in scopes_raw if str(s).strip()]
    if not scopes:
        raise ValueError(f"{path.name}: oauth.scopes must not be empty")
    extra_raw = raw.get("extra_authorize_params") or {}
    if extra_raw and not isinstance(extra_raw, dict):
        raise ValueError(f"{path.name}: oauth.extra_authorize_params must be a mapping")
    extra = {
        str(k).strip(): str(v).strip()
        for k, v in dict(extra_raw).items()
        if str(k).strip() and str(v).strip()
    }
    method = str(raw.get("token_endpoint_auth_method") or "client_secret_post").strip()
    return {
        "authorization_endpoint": authorization_endpoint,
        "token_endpoint": token_endpoint,
        "scopes": scopes,
        "extra_authorize_params": extra,
        "token_endpoint_auth_method": method or "client_secret_post",
    }


def _field_default(value: Any) -> Optional[str]:
    if value is None:
        return None
    if isinstance(value, bool):
        return "true" if value else "false"
    text = str(value).strip()
    return text or None


def _normalize_fields(raw: Any, *, path: Path) -> List[Dict[str, Any]]:
    if raw is None:
        return []
    if not isinstance(raw, list):
        raise ValueError(f"{path.name}: auth.fields must be a list")
    out: List[Dict[str, Any]] = []
    for item in raw:
        if not isinstance(item, dict):
            raise ValueError(f"{path.name}: auth.fields entries must be objects")
        name = str(item.get("name") or "").strip()
        if not name:
            raise ValueError(f"{path.name}: auth field missing name")
        control = str(item.get("control") or "text").strip() or "text"
        if control not in _VALID_FIELD_CONTROLS:
            raise ValueError(f"{path.name}: invalid auth field control {control!r}")
        out.append(
            {
                "name": name,
                "label": str(item.get("label") or name).strip(),
                "secret": bool(item.get("secret", False)),
                "required": bool(item.get("required", True)),
                "control": control,
                "default": _field_default(item.get("default")),
                "hint": str(item.get("hint") or "").strip() or None,
            }
        )
    return out


def _normalize_env_defaults(raw: Any, *, path: Path) -> Dict[str, str]:
    if raw is None:
        return {}
    if not isinstance(raw, dict):
        raise ValueError(f"{path.name}: env_defaults must be a mapping")
    out: Dict[str, str] = {}
    for key, value in raw.items():
        name = str(key).strip()
        if not name:
            raise ValueError(f"{path.name}: env_defaults keys must be non-empty")
        text = "" if value is None else str(value).strip()
        if text:
            out[name] = text
    return out


def _normalize_entry(doc: Dict[str, Any], *, path: Path) -> Dict[str, Any]:
    if not isinstance(doc, dict):
        raise ValueError(f"{path.name}: catalog document must be a mapping")
    slug = _require_str(doc, "slug", path=path)
    stem = path.stem
    if slug != stem:
        raise ValueError(f"{path.name}: slug {slug!r} must match filename {stem!r}")
    category = _require_str(doc, "category", path=path)
    if category not in _VALID_CATEGORIES:
        raise ValueError(f"{path.name}: invalid category {category!r}")
    kind = _require_str(doc, "kind", path=path)
    if kind not in _VALID_KINDS:
        raise ValueError(f"{path.name}: invalid kind {kind!r}")
    auth = doc.get("auth") if isinstance(doc.get("auth"), dict) else {}
    auth_type = str(auth.get("type") or "").strip()
    if auth_type not in _VALID_AUTH:
        raise ValueError(f"{path.name}: invalid auth.type {auth_type!r}")
    impl = (
        doc.get("implementation") if isinstance(doc.get("implementation"), dict) else {}
    )
    transport = str(doc.get("transport") or "").strip() or None
    if transport and transport not in _VALID_TRANSPORT:
        raise ValueError(f"{path.name}: invalid transport {transport!r}")
    args = doc.get("args") or []
    if not isinstance(args, list):
        raise ValueError(f"{path.name}: args must be a list")
    command = str(doc.get("command") or "").strip() or None
    if kind == "mcp" and transport == "stdio" and not command:
        raise ValueError(f"{path.name}: stdio MCP packages require command")
    oauth = _normalize_oauth(
        doc.get("oauth"),
        path=path,
        kind=kind,
        auth_type=auth_type,
        transport=transport,
    )
    hidden_raw = doc.get("hidden", False)
    if not isinstance(hidden_raw, bool):
        raise ValueError(f"{path.name}: hidden must be a boolean")
    successor = doc.get("deprecated_in_favor_of")
    if successor is not None:
        successor = str(successor).strip() or None
    if successor and not hidden_raw:
        raise ValueError(f"{path.name}: deprecated_in_favor_of requires hidden: true")
    return {
        "slug": slug,
        "display_name": _require_str(doc, "display_name", path=path),
        "description": str(doc.get("description") or "").strip(),
        "category": category,
        "kind": kind,
        "icon": str(doc.get("icon") or slug).strip() or slug,
        "vetted": True,
        "auth": {
            "type": auth_type,
            "fields": _normalize_fields(auth.get("fields"), path=path),
        },
        "implementation": {
            "sync_connector_class": str(impl.get("sync_connector_class") or "").strip()
            or None,
        },
        "transport": transport,
        "url": str(doc.get("url") or "").strip() or None,
        "command": command,
        "args": [str(a) for a in args],
        "oauth": oauth,
        "env_defaults": _normalize_env_defaults(doc.get("env_defaults"), path=path),
        "hidden": hidden_raw,
        "deprecated_in_favor_of": successor,
        # Remote tool names this vetted entry certifies as read-only. The ONLY
        # source allowed to downgrade a mounted MCP tool out of the bless gate
        # — the remote's own readOnlyHint is attacker-controlled (ADR-010 §6,
        # see connectors/mcp_tool_class.py).
        "read_only_tools": [
            str(n).strip() for n in (doc.get("read_only_tools") or []) if str(n).strip()
        ],
    }


def load_catalog(*, catalog_dir: Optional[Path] = None) -> List[Dict[str, Any]]:
    """Return all vetted catalog packages, sorted by display_name."""
    global _CACHE, _CACHE_STAMP
    root = catalog_dir or CATALOG_DIR
    stamp = _catalog_stamp(root) if root.is_dir() else None
    if catalog_dir is None and _CACHE is not None and _CACHE_STAMP == stamp:
        return list(_CACHE)
    if not root.is_dir():
        raise ValueError(f"connector catalog directory missing: {root}")
    entries: List[Dict[str, Any]] = []
    for path in sorted(root.glob("*.yaml")):
        try:
            raw = yaml.safe_load(path.read_text(encoding="utf-8"))
        except yaml.YAMLError as exc:
            raise ValueError(f"{path.name}: invalid YAML: {exc}") from exc
        entries.append(_normalize_entry(raw or {}, path=path))
    entries.sort(key=lambda e: e["display_name"].lower())
    if catalog_dir is None:
        _CACHE = list(entries)
        _CACHE_STAMP = stamp
    return entries


def get_catalog_entry(
    slug: str, *, catalog_dir: Optional[Path] = None
) -> Dict[str, Any]:
    """Return the catalog document for ``slug``, or raise KeyError."""
    key = (slug or "").strip()
    if not key:
        raise ValueError("catalog slug required")
    for entry in load_catalog(catalog_dir=catalog_dir):
        if entry["slug"] == key:
            return entry
    raise KeyError(key)


def load_visible_catalog(*, catalog_dir: Optional[Path] = None) -> List[Dict[str, Any]]:
    """Return catalog packages eligible for NEW installs (hidden excluded).

    Hidden entries stay resolvable via :func:`get_catalog_entry` so existing
    mounts keep working — they just never appear in the library list and refuse
    installs at the API layer.
    """
    return [e for e in load_catalog(catalog_dir=catalog_dir) if not e.get("hidden")]
