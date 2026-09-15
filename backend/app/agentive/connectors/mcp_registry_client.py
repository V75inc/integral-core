"""Official MCP Registry client — browse proxy for ADR-010 catalog enrichment.

Fetches ``registry.modelcontextprotocol.io`` server.json entries, normalizes
them for Integral's mount flow (ADR-009), and caches responses in-process.
Install-time only — mounted connectors never depend on the registry at runtime.
"""

from __future__ import annotations

import fnmatch
import logging
import time
from collections import OrderedDict
from typing import Any, Dict, List, Literal, Optional, Tuple

import httpx

from app.config import settings

logger = logging.getLogger(__name__)

InstallTier = Literal["direct_http", "http_with_auth", "stdio_package", "unsupported"]

# Bounded LRU. Keys include caller-supplied query/cursor/name values, so an
# unbounded dict grows with distinct queries and only ever evicts on a
# read-after-expiry (link_preview.py caps its own cache for the same reason).
_CACHE: "OrderedDict[str, Tuple[float, Any]]" = OrderedDict()
_CACHE_TTL_SECONDS = 900  # 15 minutes
_CACHE_MAX_ENTRIES = 512


def _cache_get(key: str) -> Optional[Any]:
    row = _CACHE.get(key)
    if not row:
        return None
    expires_at, value = row
    if time.monotonic() > expires_at:
        _CACHE.pop(key, None)
        return None
    _CACHE.move_to_end(key)  # LRU recency
    return value


def _cache_set(key: str, value: Any) -> None:
    _CACHE[key] = (time.monotonic() + _CACHE_TTL_SECONDS, value)
    _CACHE.move_to_end(key)
    while len(_CACHE) > _CACHE_MAX_ENTRIES:
        _CACHE.popitem(last=False)


def reset_registry_cache() -> None:
    """Test helper — clear in-process registry cache."""
    _CACHE.clear()


def _base_url() -> str:
    return (settings.MCP_REGISTRY_BASE_URL or "").rstrip("/")


def _allowlist_patterns() -> List[str]:
    raw = settings.MCP_REGISTRY_INSTALL_ALLOWLIST or ""
    return [p.strip() for p in raw.split(",") if p.strip()]


def is_registry_install_allowed(server_name: str) -> bool:
    """Only matching server names may install when an allowlist is set.

    Empty allowlist fails closed in production (no registry auto-install).
    ``TESTING=1`` keeps allow-all so unit tests need not configure patterns.
    """
    import os

    patterns = _allowlist_patterns()
    if not patterns:
        return os.environ.get("TESTING") == "1"
    return any(fnmatch.fnmatch(server_name, pat) for pat in patterns)


def _header_requires_secret(header: Dict[str, Any]) -> bool:
    if header.get("isSecret"):
        return True
    value = str(header.get("value") or "")
    return "{" in value and "}" in value


def _remote_transport_type(remote: Dict[str, Any]) -> str:
    return str(remote.get("type") or "").strip().lower()


def _pick_streamable_http_remote(
    remotes: List[Dict[str, Any]],
) -> Optional[Dict[str, Any]]:
    for remote in remotes:
        rtype = _remote_transport_type(remote)
        if rtype in ("streamable-http", "streamable_http", "http"):
            return remote
    return None


def classify_install_tier(server_doc: Dict[str, Any]) -> InstallTier:
    """Classify how an official registry entry can be mounted in Integral v1."""
    remotes = server_doc.get("remotes") or []
    if not isinstance(remotes, list):
        remotes = []

    remote = _pick_streamable_http_remote(remotes)
    if remote and remote.get("url"):
        headers = remote.get("headers") or []
        if isinstance(headers, list) and any(
            isinstance(h, dict) and _header_requires_secret(h) for h in headers
        ):
            return "http_with_auth"
        return "direct_http"

    packages = server_doc.get("packages") or []
    if isinstance(packages, list) and packages:
        return "stdio_package"

    return "unsupported"


def _auth_header_prompts(remote: Dict[str, Any]) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    headers = remote.get("headers") or []
    if not isinstance(headers, list):
        return out
    for h in headers:
        if not isinstance(h, dict):
            continue
        if not _header_requires_secret(h):
            continue
        name = str(h.get("name") or "").strip()
        if not name:
            continue
        out.append(
            {
                "name": name,
                "description": str(h.get("description") or ""),
                "required": bool(h.get("isRequired", True)),
            }
        )
    return out


def _env_var_prompts(server_doc: Dict[str, Any]) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    packages = server_doc.get("packages") or []
    if not isinstance(packages, list):
        return out
    for pkg in packages:
        if not isinstance(pkg, dict):
            continue
        for ev in pkg.get("environmentVariables") or []:
            if not isinstance(ev, dict):
                continue
            name = str(ev.get("name") or "").strip()
            if not name:
                continue
            out.append(
                {
                    "name": name,
                    "description": str(ev.get("description") or ""),
                    "required": bool(ev.get("isRequired", False)),
                    "is_secret": bool(ev.get("isSecret", False)),
                }
            )
    return out


def _server_doc(wrapped: Dict[str, Any]) -> Dict[str, Any]:
    return wrapped.get("server") if isinstance(wrapped.get("server"), dict) else wrapped


def _official_meta(wrapped: Dict[str, Any]) -> Dict[str, Any]:
    meta = wrapped.get("_meta") if isinstance(wrapped.get("_meta"), dict) else {}
    official = meta.get("io.modelcontextprotocol.registry/official")
    return official if isinstance(official, dict) else {}


def _is_latest_wrapped(wrapped: Dict[str, Any]) -> bool:
    return bool(_official_meta(wrapped).get("isLatest"))


def _collapse_to_latest_per_name(
    raw_servers: List[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    """Keep one registry row per server name — prefer isLatest, else last seen."""
    latest_by_name: Dict[str, Dict[str, Any]] = {}
    order: List[str] = []
    for item in raw_servers:
        if not isinstance(item, dict):
            continue
        name = str(_server_doc(item).get("name") or "").strip()
        if not name:
            continue
        existing = latest_by_name.get(name)
        if existing is None:
            latest_by_name[name] = item
            order.append(name)
            continue
        if _is_latest_wrapped(item) and not _is_latest_wrapped(existing):
            latest_by_name[name] = item
    return [latest_by_name[name] for name in order]


def _stdio_manual_recipe(server_doc: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    packages = server_doc.get("packages") or []
    if not isinstance(packages, list) or not packages:
        return None
    pkg = packages[0] if isinstance(packages[0], dict) else {}
    registry_type = str(pkg.get("registryType") or "").strip().lower()
    identifier = str(pkg.get("identifier") or "").strip()
    version = str(pkg.get("version") or server_doc.get("version") or "").strip()
    runtime_hint = str(pkg.get("runtimeHint") or "").strip()
    transport = pkg.get("transport") if isinstance(pkg.get("transport"), dict) else {}
    transport_type = str(transport.get("type") or "stdio").strip().lower()

    command = ""
    args: List[str] = []
    if registry_type == "npm":
        command = "npx"
        args = ["-y", f"{identifier}@{version}"] if version else ["-y", identifier]
    elif registry_type == "pypi":
        command = runtime_hint or "uvx"
        args = [f"{identifier}=={version}"] if version else [identifier]
    elif registry_type == "oci":
        command = runtime_hint or "docker"
        args = ["run", "-i", "--rm", identifier]
    else:
        command = runtime_hint or registry_type or "stdio"
        args = [identifier] if identifier else []

    return {
        "transport": transport_type if transport_type in ("stdio",) else "stdio",
        "command": command,
        "args": args,
        "package_registry": registry_type,
        "package_identifier": identifier,
        "package_version": version,
        "note": (
            "Stdio/npm/docker installs require the runtime on the Integral "
            "backend host. Use manual mount unless MCP_REGISTRY_ENABLE_STDIO_INSTALL "
            "is enabled and the server is allowlisted."
        ),
    }


def normalize_registry_entry(
    wrapped: Dict[str, Any],
) -> Dict[str, Any]:
    """Normalize one registry list item to McpRegistryEntry wire shape."""
    server_doc = _server_doc(wrapped)
    official = _official_meta(wrapped)

    name = str(server_doc.get("name") or "").strip()
    title = str(server_doc.get("title") or name or "MCP Server").strip()
    description = str(server_doc.get("description") or "").strip()
    version = str(server_doc.get("version") or "").strip()
    install_tier = classify_install_tier(server_doc)

    remotes = server_doc.get("remotes") or []
    remote = _pick_streamable_http_remote(remotes if isinstance(remotes, list) else [])
    remote_url = str(remote.get("url") or "").strip() if remote else None

    repo = (
        server_doc.get("repository")
        if isinstance(server_doc.get("repository"), dict)
        else {}
    )
    repository_url = str(repo.get("url") or "").strip() or None

    return {
        "name": name,
        "title": title,
        "description": description,
        "version": version,
        "install_tier": install_tier,
        "install_allowed": is_registry_install_allowed(name) if name else False,
        "is_latest": bool(official.get("isLatest", False)),
        "remote_url": remote_url,
        "repository_url": repository_url,
        "auth_header_prompts": _auth_header_prompts(remote) if remote else [],
        "env_var_prompts": _env_var_prompts(server_doc),
        "manual_recipe": (
            _stdio_manual_recipe(server_doc)
            if install_tier == "stdio_package"
            else None
        ),
    }


async def _fetch_registry(path: str, params: Dict[str, Any]) -> Dict[str, Any]:
    cache_key = f"{path}?{sorted(params.items())}"
    cached = _cache_get(cache_key)
    if cached is not None:
        return cached

    url = f"{_base_url()}{path}"
    timeout = float(settings.MCP_REGISTRY_HTTP_TIMEOUT_SECONDS or 15)
    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            resp = await client.get(url, params=params)
            resp.raise_for_status()
            data = resp.json()
    except httpx.HTTPError as exc:
        logger.warning("mcp_registry_client: fetch failed %s: %s", url, exc)
        raise ValueError(f"MCP registry unavailable: {exc}") from exc

    if not isinstance(data, dict):
        raise ValueError("MCP registry returned unexpected payload")

    _cache_set(cache_key, data)
    return data


async def search_registry_servers(
    *,
    q: str = "",
    cursor: str = "",
    limit: int = 20,
) -> Dict[str, Any]:
    """Search official MCP registry — latest version of each server only."""
    lim = max(1, min(int(limit or 20), 50))
    params: Dict[str, Any] = {"limit": lim, "version": "latest"}
    if q.strip():
        params["search"] = q.strip()
    if cursor.strip():
        params["cursor"] = cursor.strip()

    data = await _fetch_registry("/v0/servers", params)
    raw_servers = data.get("servers") or []
    entries: List[Dict[str, Any]] = []
    if isinstance(raw_servers, list):
        for item in _collapse_to_latest_per_name(raw_servers):
            entries.append(normalize_registry_entry(item))

    meta = data.get("metadata") if isinstance(data.get("metadata"), dict) else {}
    return {
        "entries": entries,
        "next_cursor": str(meta.get("nextCursor") or "") or None,
        "count": len(entries),
    }


async def get_registry_server(name: str) -> Dict[str, Any]:
    """Fetch latest matching registry entry by exact server name."""
    key = (name or "").strip()
    if not key:
        raise ValueError("server name required")

    data = await _fetch_registry(
        "/v0/servers", {"search": key, "limit": 20, "version": "latest"}
    )
    raw_servers = data.get("servers") or []
    if not isinstance(raw_servers, list):
        raise ValueError(f"registry server not found: {key!r}")

    matches: List[Dict[str, Any]] = []
    for item in raw_servers:
        if not isinstance(item, dict):
            continue
        if str(_server_doc(item).get("name") or "").strip() == key:
            matches.append(item)

    if not matches:
        raise ValueError(f"registry server not found: {key!r}")

    latest = next((m for m in matches if _is_latest_wrapped(m)), matches[0])
    return normalize_registry_entry(latest)


def build_mount_preview(entry: Dict[str, Any]) -> Dict[str, Any]:
    """Build mount preview from a normalized registry entry (no secrets)."""
    tier = entry.get("install_tier")
    name = entry.get("name") or ""
    title = entry.get("title") or name

    preview: Dict[str, Any] = {
        "registry_name": name,
        "registry_version": entry.get("version") or "",
        "install_tier": tier,
        "display_name": title,
        "transport": None,
        "url": None,
        "command": None,
        "args": [],
        "auth_header_prompts": entry.get("auth_header_prompts") or [],
        "env_var_prompts": entry.get("env_var_prompts") or [],
        "manual_recipe": entry.get("manual_recipe"),
        "can_auto_mount": tier in ("direct_http", "http_with_auth")
        and bool(entry.get("install_allowed")),
    }

    if tier in ("direct_http", "http_with_auth"):
        preview["transport"] = "streamable_http"
        preview["url"] = entry.get("remote_url")
    elif tier == "stdio_package" and entry.get("manual_recipe"):
        recipe = entry["manual_recipe"]
        preview["transport"] = "stdio"
        preview["command"] = recipe.get("command")
        preview["args"] = list(recipe.get("args") or [])

    return preview


def to_mount_request(
    entry: Dict[str, Any],
    *,
    secrets: Optional[Dict[str, str]] = None,
) -> Dict[str, Any]:
    """Map normalized registry entry + user secrets → MountMcpConnectorRequest dict."""
    secrets = secrets or {}
    tier = entry.get("install_tier")
    name = str(entry.get("name") or "")
    if not is_registry_install_allowed(name):
        raise ValueError(f"registry server not allowlisted: {name!r}")

    if tier == "stdio_package":
        # Unreachable by design, and saying so is the honest answer.
        #
        # A stdio mount spawns a process on the API host, so
        # ``mcp_client._resolve_trusted_stdio_command`` re-derives the command
        # from the in-repo catalog by ``catalog_slug`` and refuses anything
        # without one (that check exists because a free-form command in
        # ``auth_state`` was a live RCE). A registry entry has no catalog slug
        # — its recipe IS free-form — so discovery refused the mount even with
        # ``MCP_REGISTRY_ENABLE_STDIO_INSTALL`` on. The flag gated a door the
        # vetting layer had already welded shut, and the operator got
        # "connector has no vetted catalog_slug" after being told to enable a
        # setting. Refuse here, where the intent is, with the actual reason.
        raise ValueError(
            "This registry entry runs as a local process, and Integral only "
            "spawns commands vetted in its own connector catalog — a registry "
            "recipe cannot be vetted at install time. Use a hosted (HTTP) MCP "
            "server, or add this server to the connector catalog in-repo."
        )
        recipe = entry.get("manual_recipe") or {}
        env: Dict[str, str] = {}
        for prompt in entry.get("env_var_prompts") or []:
            if not isinstance(prompt, dict):
                continue
            pkey = str(prompt.get("name") or "")
            if not pkey:
                continue
            if pkey in secrets:
                env[pkey] = secrets[pkey]
            elif prompt.get("required"):
                raise ValueError(f"missing required env var: {pkey}")
        return {
            "transport": "stdio",
            "command": recipe.get("command") or "",
            "args": list(recipe.get("args") or []),
            "env": env or None,
            "display_name": entry.get("title") or name,
            "registry_name": name,
            "registry_version": entry.get("version") or "",
        }

    if tier not in ("direct_http", "http_with_auth"):
        raise ValueError(f"cannot auto-mount install tier: {tier!r}")

    url = entry.get("remote_url") or ""
    if not url:
        raise ValueError("registry entry has no streamable HTTP remote URL")

    headers: Dict[str, str] = {}
    for prompt in entry.get("auth_header_prompts") or []:
        if not isinstance(prompt, dict):
            continue
        hname = str(prompt.get("name") or "")
        if not hname:
            continue
        if hname in secrets:
            headers[hname] = secrets[hname]
        elif prompt.get("required"):
            raise ValueError(f"missing required auth header: {hname}")

    return {
        "transport": "streamable_http",
        "url": url,
        "headers": headers or None,
        "display_name": entry.get("title") or name,
        "registry_name": name,
        "registry_version": entry.get("version") or "",
    }
