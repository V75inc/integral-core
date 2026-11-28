"""MCP client session helpers — stdio + streamable HTTP (ADR-009).

Discovers and invokes tools on an *external* MCP server. Not the Integral
outbound MCP server (``app.agentive.mcp.server``).
"""

from __future__ import annotations

import asyncio
import logging
import re
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from datetime import timedelta
from typing import Any, AsyncIterator, Dict, List, Optional

logger = logging.getLogger(__name__)

# Default per-call / health-probe budget (seconds).
_DEFAULT_TIMEOUT = 30.0

# First stdio spawn of a catalog package such as
# ``npx -y github:intuit/quickbooks-online-mcp-server`` runs that package's
# ``prepare`` build before the process speaks MCP. A cold build on this
# host takes about a minute. The 30s budget killed the process mid-install,
# left a partial npx cache, and the approved tool call never reached Intuit.
# Tool calls spawn a new process too, so they share this budget. A warm
# process still returns as soon as it responds.
_STDIO_DISCOVERY_TIMEOUT = 150.0

# ADR-009 §8 health vocabulary — the ONLY values written to
# ``Connector.health_status``. The stdio path used to write "healthy" while the
# HTTP path wrote "ok", so any consumer switching on the field was wrong for
# one of the two transports (``test_mcp_connector_http`` had to accept both).
HEALTH_OK = "ok"
HEALTH_DEGRADED = "degraded"
HEALTH_ERROR = "error"
HEALTH_UNKNOWN = "unknown"


@dataclass(frozen=True)
class RemoteTool:
    """One tool advertised by a remote MCP server."""

    name: str
    description: str
    input_schema: Dict[str, Any]
    #: The remote's own side-effect hints. DISPLAY ONLY — never gate on these;
    #: the server supplying them is the party the gate constrains.
    annotations: Dict[str, Any] = field(default_factory=dict)


def guarded_mcp_http_client(
    headers: Optional[Dict[str, str]] = None,
    timeout: Any = None,
    auth: Any = None,
) -> Any:
    """``McpHttpClientFactory`` that re-validates every redirect hop (F-2).

    Drop-in for ``mcp.shared._httpx_utils.create_mcp_http_client``, which
    always sets ``follow_redirects=True`` and revalidates nothing.
    """
    from app.services.url_safety import guarded_async_client

    return guarded_async_client(headers=headers, timeout=timeout, auth=auth)


def _session_timeout(
    auth_state: Dict[str, Any], manifest: Optional[dict] = None
) -> float:
    """Per-request budget for one MCP session, including stdio startup.

    HTTP sessions have no build step. Stdio sessions do: ``initialize`` and
    ``call_tool`` cannot start until ``npx`` has finished preparing the
    package, and that work is inside the same read timeout.
    """
    try:
        transport = _resolve_mcp_transport(auth_state, manifest)
    except ValueError:
        return _DEFAULT_TIMEOUT
    if transport == "stdio":
        return _STDIO_DISCOVERY_TIMEOUT
    return _DEFAULT_TIMEOUT


def _transport_from_auth(auth_state: Dict[str, Any]) -> str:
    raw = str((auth_state or {}).get("transport") or "stdio").strip().lower()
    if raw not in ("stdio", "streamable_http"):
        raise ValueError(f"unsupported MCP transport: {raw!r}")
    return raw


def _package_mcp_transport(source: Any) -> Optional[str]:
    """Return a package MCP transport, or None when that package omits it."""
    if not isinstance(source, dict):
        return None
    capabilities = source.get("capabilities")
    if not isinstance(capabilities, dict) or "mcp" not in capabilities:
        return None
    mcp = capabilities.get("mcp")
    if not isinstance(mcp, dict) or "transport" not in mcp:
        return None
    raw = str(mcp.get("transport") or "").strip().lower()
    if raw not in ("stdio", "streamable_http"):
        raise ValueError("package does not declare an MCP transport")
    return raw


def _resolve_mcp_transport(
    auth_state: Dict[str, Any], manifest: Optional[dict] = None
) -> str:
    """Return the MCP transport from the package, then the catalog."""
    found = _package_mcp_transport(manifest)
    slug = str((auth_state or {}).get("catalog_slug") or "").strip()
    if found is None and slug:
        from app.connectors.package_runtime import admitted_manifest

        found = _package_mcp_transport(admitted_manifest(slug))
    if found is None and slug:
        from app.connectors.catalog_loader import get_catalog_entry

        try:
            entry = get_catalog_entry(slug)
        except KeyError:
            entry = {}
        catalog = str((entry or {}).get("transport") or "").strip().lower()
        if catalog in ("stdio", "streamable_http"):
            found = catalog
        elif catalog:
            raise ValueError("package does not declare an MCP transport")
    if found:
        return found
    if slug:
        raise ValueError("package does not declare an MCP transport")
    return _transport_from_auth(auth_state)


# Execution-selecting vars: never accepted from connector data under any
# circumstance. PATH decides which binary "npx" resolves to; the rest decide
# what that binary loads. This is a backstop — the allowlist below is the
# primary control.
_NEVER_FROM_CONNECTOR: frozenset[str] = frozenset(
    {
        "PATH",
        "NODE_OPTIONS",
        "NODE_PATH",
        "NPM_CONFIG_REGISTRY",
        "NPM_CONFIG_PREFIX",
        "NPM_CONFIG_USERCONFIG",
        "NPM_CONFIG_GLOBALCONFIG",
        "GIT_SSH",
        "GIT_SSH_COMMAND",
        "GIT_CONFIG_GLOBAL",
        "GIT_CONFIG_SYSTEM",
        "GIT_EXTERNAL_DIFF",
        "BASH_ENV",
        "ENV",
        "SHELLOPTS",
        "LD_PRELOAD",
        "LD_LIBRARY_PATH",
        "LD_AUDIT",
        "DYLD_INSERT_LIBRARIES",
        "DYLD_LIBRARY_PATH",
        "DYLD_FRAMEWORK_PATH",
        "PYTHONSTARTUP",
        "PYTHONPATH",
        "PYTHONHOME",
        "PYTHONEXECUTABLE",
    }
)


def _mcp_spawn_keys(source: Any) -> Optional[set[str]]:
    """Return spawn env keys when a package declares them."""
    if not isinstance(source, dict):
        return None
    capabilities = source.get("capabilities")
    if not isinstance(capabilities, dict) or "mcp" not in capabilities:
        return None
    mcp = capabilities.get("mcp")
    if not isinstance(mcp, dict):
        raise ValueError("package does not declare spawn env")
    if "env" not in mcp and "generated_env" not in mcp:
        return None
    allowed: set[str] = set()
    if "env" in mcp:
        package_env = mcp.get("env")
        if not isinstance(package_env, dict):
            raise ValueError("package does not declare spawn env")
        allowed.update(str(key) for key in package_env)
    if "generated_env" in mcp:
        generated = mcp.get("generated_env")
        if not isinstance(generated, list):
            raise ValueError("package does not declare spawn env")
        allowed.update(str(key) for key in generated)
    return allowed


def _spawn_mcp(auth_state: Dict[str, Any], manifest: Optional[dict] = None) -> dict:
    """Return the MCP block whose spawn env declaration is in force."""
    found = _mcp_spawn_keys(manifest)
    mcp_source: Any = manifest if found is not None else None
    if found is None:
        slug = str((auth_state or {}).get("catalog_slug") or "").strip()
        from app.connectors.package_runtime import admitted_manifest

        admitted = admitted_manifest(slug) if slug else None
        if _mcp_spawn_keys(admitted) is not None:
            mcp_source = admitted
    if not isinstance(mcp_source, dict):
        return {}
    mcp = (mcp_source.get("capabilities") or {}).get("mcp") or {}
    return mcp if isinstance(mcp, dict) else {}


def _allowed_spawn_env_keys(
    auth_state: Dict[str, Any], manifest: Optional[dict] = None
) -> frozenset[str]:
    """Env keys a connector may contribute to the spawn, from its package.

    A stored snapshot that declares spawn env wins. An older snapshot falls
    back to the admitted package, then the catalog. Anything else in
    ``auth_state["env"]`` is dropped.
    """
    slug = str((auth_state or {}).get("catalog_slug") or "").strip()
    package_keys = _mcp_spawn_keys(manifest)
    if package_keys is None and slug:
        from app.connectors.package_runtime import admitted_manifest

        package_keys = _mcp_spawn_keys(admitted_manifest(slug))
    if package_keys is not None:
        return frozenset(package_keys) - _NEVER_FROM_CONNECTOR
    allowed: set[str] = set()
    if slug:
        from app.connectors.catalog_loader import get_catalog_entry

        try:
            entry = get_catalog_entry(slug)
        except KeyError:
            entry = {}
        allowed.update(str(k) for k in (entry.get("env_defaults") or {}))
        auth = entry.get("auth") if isinstance(entry.get("auth"), dict) else {}
        for auth_field in (auth or {}).get("fields") or []:
            if isinstance(auth_field, dict) and auth_field.get("name"):
                allowed.add(str(auth_field["name"]))
    return frozenset(allowed) - _NEVER_FROM_CONNECTOR


def _build_spawn_env(
    auth_state: Dict[str, Any],
    config: Optional[Dict[str, Any]] = None,
    manifest: Optional[dict] = None,
) -> Dict[str, str]:
    """Environment for a stdio MCP subprocess — ALLOWLIST, not denylist.

    A denylist is the wrong shape here and was a live RCE: the catalog command
    is ``npx``, resolved through ``PATH``, so an attacker-supplied
    ``auth_state.env`` containing ``PATH=/tmp/evil`` ran their binary under a
    "vetted" command. Enumerating the dangerous keys can never be complete —
    ``PATH``, ``NODE_PATH``, ``NPM_CONFIG_*``, ``GIT_CONFIG_*`` and friends all
    redirect execution — so the connector may only contribute keys its own
    catalog entry declares. The process environment comes from
    ``get_default_environment()`` (the MCP SDK's already-filtered safe base,
    which supplies PATH) and is never overridden by connector data.
    """
    from mcp.client.stdio import get_default_environment

    env_dict: Dict[str, str] = dict(get_default_environment())
    supplied = (auth_state or {}).get("env")
    allowed = _allowed_spawn_env_keys(auth_state, manifest)
    if isinstance(supplied, dict):
        for key, value in supplied.items():
            name = str(key)
            if name in allowed:
                env_dict[name] = str(value)
    package_env = _spawn_mcp(auth_state, manifest).get("env")
    if isinstance(package_env, dict):
        for key, spec in package_env.items():
            if key not in allowed or not isinstance(spec, dict):
                continue
            name = str(key)
            if "value" in spec:
                env_dict[name] = str(spec["value"])
            elif spec.get("secret"):
                found = (auth_state or {}).get(spec["secret"])
                if found:
                    env_dict[name] = str(found)
            elif spec.get("config"):
                key = spec["config"]
                found = config.get(key) if isinstance(config, dict) else None
                if found is None:
                    found = (auth_state or {}).get(key)
                if found is not None:
                    env_dict[name] = str(found)
            elif spec.get("oauth"):
                oauth = (auth_state or {}).get("oauth")
                found = oauth.get(spec["oauth"]) if isinstance(oauth, dict) else None
                if not found:
                    found = (auth_state or {}).get(spec["oauth"])
                if found:
                    env_dict[name] = str(found)
    return env_dict


def _freeform_stdio_allowed() -> bool:
    """Free-form (attacker-choosable) stdio commands.

    Permitted under TESTING=1 (pytest fixtures), when INTEGRAL_ALLOW_STDIO_MCP
    is enabled, or when DEBUG mode is active. In production deployments without
    this flag, free-form stdio is refused to prevent arbitrary command execution.
    """
    import os

    if os.environ.get("TESTING") == "1":
        return True
    if os.environ.get("INTEGRAL_ALLOW_STDIO_MCP", "").strip().lower() in (
        "1",
        "true",
        "yes",
    ):
        return True
    return os.environ.get("DEBUG", "").strip().lower() in ("1", "true", "yes")


def _package_http_mcp_url(
    source: Any, config: Optional[Dict[str, Any]]
) -> Optional[str]:
    """Return a package HTTP MCP URL, or None when that package omits it."""
    if not isinstance(source, dict):
        return None
    capabilities = source.get("capabilities")
    if not isinstance(capabilities, dict) or "mcp" not in capabilities:
        return None
    mcp = capabilities.get("mcp")
    if not isinstance(mcp, dict) or "url" not in mcp:
        return None
    transport = str(mcp.get("transport") or "streamable_http").strip().lower()
    if transport != "streamable_http":
        return None
    url = mcp.get("url")
    if not isinstance(url, str) or not url.strip():
        raise ValueError("package does not declare an MCP URL")
    from app.connectors.package_runtime import render

    rendered = str(render(url.strip(), config or {}, {})).strip()
    if not rendered:
        raise ValueError("package does not declare an MCP URL")
    return rendered


def _resolve_http_mcp_url(
    auth_state: Dict[str, Any],
    manifest: Optional[dict] = None,
    config: Optional[Dict[str, Any]] = None,
) -> str:
    """Return the HTTP MCP URL from the package, then the catalog."""
    found = _package_http_mcp_url(manifest, config)
    slug = str((auth_state or {}).get("catalog_slug") or "").strip()
    if found is None and slug:
        from app.connectors.package_runtime import admitted_manifest

        found = _package_http_mcp_url(admitted_manifest(slug), config)
    if found is None and slug:
        from app.connectors.catalog_loader import get_catalog_entry

        try:
            entry = get_catalog_entry(slug)
        except KeyError:
            entry = {}
        catalog_url = str((entry or {}).get("url") or "").strip()
        if catalog_url:
            found = catalog_url
    if found:
        return found
    if slug:
        raise ValueError("package does not declare an MCP URL")
    url = str((auth_state or {}).get("url") or "").strip()
    if not url:
        raise ValueError("streamable_http MCP connector requires auth_state.url")
    return url


def _package_stdio_command(source: Any) -> Optional[tuple[str, list[str]]]:
    """Return a package stdio command, or None when that package omits it."""
    if not isinstance(source, dict):
        return None
    capabilities = source.get("capabilities")
    if not isinstance(capabilities, dict) or "mcp" not in capabilities:
        return None
    mcp = capabilities.get("mcp")
    if not isinstance(mcp, dict) or "command" not in mcp:
        return None
    command = mcp.get("command")
    if (
        not isinstance(command, list)
        or not command
        or any(not isinstance(part, str) or not part.strip() for part in command)
    ):
        raise ValueError("package does not declare a stdio command")
    transport = str(mcp.get("transport") or "stdio").strip().lower()
    if transport != "stdio":
        raise ValueError("package does not declare a stdio command")
    return command[0].strip(), [part.strip() for part in command[1:]]


def _resolve_trusted_stdio_command(
    auth_state: Dict[str, Any],
    manifest: Optional[dict] = None,
) -> tuple[str, list[str]]:
    """Return the ``(command, args)`` to spawn for a stdio MCP connector.

    Command and args are re-derived server-side from the vetted connector
    catalog keyed by ``auth_state['catalog_slug']`` — they are NEVER trusted
    from ``auth_state`` on a normal deployment. Any authenticated user can
    create a ``kind="mcp"`` Connector with arbitrary ``auth_state`` and reach
    this path via ``/health`` or ``/mcp/refresh``; trusting the persisted
    ``command`` there is remote code execution (S-MCP-RCE). A forged connector
    that names a real ``catalog_slug`` therefore launches only that catalog
    entry's curated command, and one that names none is refused.

    Free-form commands are permitted only under ``TESTING=1`` (the test
    fixtures spawn a local echo server by path).
    """
    slug = str((auth_state or {}).get("catalog_slug") or "").strip()
    # A persisted manifest can supply data but cannot select executable code.
    if not slug and manifest:
        slug = str(manifest.get("id") or "").strip()
    if slug:
        from app.connectors.package_discovery import first_party_package_dir
        from app.connectors.package_runtime import admitted_manifest

        bundled = admitted_manifest(slug) if first_party_package_dir(slug) else None
        if bundled and bundled.get("trust", {}).get("tier") == "trusted":
            found = _package_stdio_command(bundled)
            if found is not None:
                return found
    if slug:
        from app.connectors.catalog_loader import get_catalog_entry

        try:
            entry = get_catalog_entry(slug)
        except KeyError as exc:
            raise ValueError(
                f"stdio MCP connector references unknown catalog slug {slug!r}"
            ) from exc
        if (entry.get("transport") or "").strip().lower() != "stdio":
            raise ValueError(f"catalog entry {slug!r} is not an stdio MCP package")
        command = str(entry.get("command") or "").strip()
        if not command:
            raise ValueError(f"catalog entry {slug!r} declares no stdio command")
        return command, [str(a) for a in (entry.get("args") or [])]

    if _freeform_stdio_allowed():
        command = str((auth_state or {}).get("command") or "").strip()
        if not command:
            raise ValueError("stdio MCP connector requires auth_state.command")
        return command, [str(a) for a in ((auth_state or {}).get("args") or [])]

    raise ValueError(
        "stdio MCP command is not permitted: connector has no vetted "
        "catalog_slug. Install from the connector catalog or MCP registry "
        "instead of setting a free-form command."
    )


@asynccontextmanager
async def open_mcp_session(
    auth_state: Dict[str, Any],
    *,
    timeout: float = _DEFAULT_TIMEOUT,
    config: Optional[Dict[str, Any]] = None,
    manifest: Optional[dict] = None,
) -> AsyncIterator[Any]:
    """Yield an initialized ``ClientSession`` for the connector's auth_state.

    Caller must not retain the session after the context exits — stdio
    subprocesses and HTTP streams are torn down on exit.
    """
    from mcp import ClientSession

    from app.agentive.services.connector_registry_node import decrypt_auth_state

    # F-7: secrets in ``auth_state`` (stdio ``env``, static ``headers``, the
    # nested OAuth token bundle) are encrypted at rest. Callers hand us the
    # raw node dict — decrypt once here, at the point of use, so every entry
    # point (mount, refresh, health, proxy invoke) gets it for free.
    auth_state = decrypt_auth_state(auth_state)
    transport = _resolve_mcp_transport(auth_state, manifest)
    if transport == "stdio":
        from mcp.client.stdio import StdioServerParameters, stdio_client

        command, args = await asyncio.to_thread(
            _resolve_trusted_stdio_command, auth_state, manifest
        )
        env_dict = await asyncio.to_thread(
            _build_spawn_env, auth_state, config, manifest
        )
        params = StdioServerParameters(command=command, args=args, env=env_dict)
        async with stdio_client(params) as (read, write):
            async with ClientSession(
                read, write, read_timeout_seconds=timedelta(seconds=timeout)
            ) as session:
                await session.initialize()
                yield session
        return

    # streamable_http
    from mcp.client.streamable_http import streamablehttp_client

    url = _resolve_http_mcp_url(auth_state, manifest, config)
    from app.services.url_safety import pin_public_dns, validate_outbound_http_url

    # F-2: validating the mount URL and then handing it to the SDK client was
    # not a control — ``create_mcp_http_client`` hard-codes
    # ``follow_redirects=True``, so a public MCP server could 302 the session
    # onto ``http://169.254.169.254/…``. The guarded factory re-validates every
    # hop; ``pin_public_dns`` closes the rebinding window on the first one.
    await validate_outbound_http_url(url)
    from app.agentive.connectors.mcp_oauth import mcp_request_headers

    header_dict = mcp_request_headers(auth_state) or None
    with pin_public_dns(url):
        async with streamablehttp_client(
            url,
            headers=header_dict,
            timeout=timeout,
            httpx_client_factory=guarded_mcp_http_client,
        ) as (
            read,
            write,
            _get_session_id,
        ):
            async with ClientSession(
                read, write, read_timeout_seconds=timedelta(seconds=timeout)
            ) as session:
                await session.initialize()
                yield session


async def list_remote_tools(
    auth_state: Dict[str, Any],
    config: Optional[Dict[str, Any]] = None,
    manifest: Optional[dict] = None,
) -> List[RemoteTool]:
    """Connect, ``list_tools``, return normalized RemoteTool list."""
    from app.agentive.services.connector_registry_node import decrypt_auth_state

    state = decrypt_auth_state(auth_state)
    timeout = _session_timeout(state, manifest)
    async with open_mcp_session(
        auth_state, timeout=timeout, config=config, manifest=manifest
    ) as session:
        result = await session.list_tools()
        tools: List[RemoteTool] = []
        for t in getattr(result, "tools", None) or []:
            schema = getattr(t, "inputSchema", None) or getattr(t, "input_schema", None)
            if not isinstance(schema, dict):
                schema = {"type": "object", "properties": {}}
            ann = getattr(t, "annotations", None)
            tools.append(
                RemoteTool(
                    name=str(getattr(t, "name", "") or ""),
                    description=str(getattr(t, "description", "") or ""),
                    input_schema=dict(schema),
                    annotations=(
                        {
                            "readOnlyHint": bool(getattr(ann, "readOnlyHint", False)),
                            "destructiveHint": bool(
                                getattr(ann, "destructiveHint", False)
                            ),
                        }
                        if ann is not None
                        else {}
                    ),
                )
            )
        return [t for t in tools if t.name]


class McpRemoteToolError(RuntimeError):
    """A failed remote call with a bounded, credential-free classification."""

    def __init__(self, message: str, error_code: str = "remote_error"):  # noqa: B042
        super().__init__(message)
        self.error_code = error_code


def _remote_tool_error(result: Any) -> McpRemoteToolError:
    # MCP tool failures carry arbitrary server text. Extract only a recognized
    # HTTP status, never release that text (or structured content) to the model.
    texts = [
        str(getattr(block, "text", ""))[:4096]
        for block in (getattr(result, "content", None) or [])[:8]
    ]
    status = re.search(
        r"(?:HTTP(?:/\d(?:\.\d)?)?\s+|[Cc]lient error ['\"]|[Ss]tatus(?:[_ ]code)?[\s:=]+)(401|403|429)\b",
        "\n".join(texts),
    )
    if status and status.group(1) in {"401", "403"}:
        return McpRemoteToolError(
            f"The remote service refused authentication (HTTP {status.group(1)}). Check the connector credentials in Settings → Connectors.",
            "remote_authentication_failed",
        )
    if status:
        return McpRemoteToolError(
            "The remote service rate-limited the request (HTTP 429). Retry after its limit resets.",
            "remote_rate_limited",
        )
    return McpRemoteToolError("Remote MCP tool reported an error")


async def call_remote_tool(
    auth_state: Dict[str, Any],
    tool_name: str,
    arguments: Optional[Dict[str, Any]] = None,
    config: Optional[Dict[str, Any]] = None,
    manifest: Optional[dict] = None,
) -> Any:
    """Connect, ``call_tool``, return structured content or text payload."""
    from app.agentive.services.connector_registry_node import decrypt_auth_state

    timeout = _session_timeout(decrypt_auth_state(auth_state), manifest)
    async with open_mcp_session(
        auth_state, timeout=timeout, config=config, manifest=manifest
    ) as session:
        result = await session.call_tool(tool_name, arguments=dict(arguments or {}))
    if bool(getattr(result, "isError", False)):
        # Protocol failure is authoritative even when the server supplies
        # structured content or text. Never receipt it as a successful read
        # or mark connector health OK. Do not echo arbitrary remote error
        # bodies (which may contain credentials) into logs or model context.
        raise _remote_tool_error(result)
    # Prefer structuredContent when present; else flatten text blocks.
    structured = getattr(result, "structuredContent", None)
    if structured is not None:
        return structured
    content = getattr(result, "content", None) or []
    texts: List[str] = []
    for block in content:
        text = getattr(block, "text", None)
        if text is not None:
            texts.append(str(text))
    if texts:
        return {"text": "\n".join(texts)} if len(texts) > 1 else {"text": texts[0]}
    return {"ok": True, "raw": str(result)}


_TASKGROUP_NOISE = (
    "unhandled errors in a taskgroup",
    "unhandled exception in a taskgroup",
)


def _iter_causes(exc: BaseException, seen: set) -> Any:
    """Walk an exception's group members, cause and context, once each."""
    if id(exc) in seen:
        return
    seen.add(id(exc))
    yield exc
    for inner in getattr(exc, "exceptions", None) or ():
        if isinstance(inner, BaseException):
            yield from _iter_causes(inner, seen)
    for linked in (exc.__cause__, exc.__context__):
        if isinstance(linked, BaseException):
            yield from _iter_causes(linked, seen)


def describe_mcp_failure(exc: BaseException) -> str:
    """Operator-readable one-liner for any MCP client failure.

    The MCP SDK runs its transport inside an anyio TaskGroup, so the exception
    that escapes is usually an ``ExceptionGroup`` whose ``str()`` is
    "unhandled errors in a TaskGroup (1 sub-exception)". That string was being
    persisted verbatim to ``Connector.last_error`` by the health, refresh and
    registry paths — and ``last_error`` is what the operator reads in the UI.
    The same underlying 403 that produced an actionable sentence at install
    produced that noise on the next health check.

    Walks to the most specific leaf, prefers an HTTP status, and never echoes
    a credential.
    """
    seen: set = set()
    leaves = [
        e
        for e in _iter_causes(exc, seen)
        if not any(n in str(e).lower() for n in _TASKGROUP_NOISE)
    ]
    for candidate in leaves:
        response = getattr(candidate, "response", None)
        status = getattr(response, "status_code", None)
        if status is not None:
            body = ""
            try:
                body = (response.text or "").strip()[:200]
            except Exception:  # noqa: BLE001
                body = ""
            detail = f" — {body}" if body else ""
            return _scrub_credentials(f"HTTP {status} from the MCP server{detail}")
    for candidate in leaves:
        text = str(candidate).strip()
        if text:
            return _scrub_credentials(f"{type(candidate).__name__}: {text}")
    return _scrub_credentials(f"{type(exc).__name__}: {exc}")


def _scrub_credentials(text: str) -> str:
    """Never let a bearer token reach a persisted error string."""
    lowered = text.lower()
    if "authorization" in lowered or "bearer " in lowered:
        return (
            "The MCP server rejected the request; details withheld because the "
            "error text carried credential material."
        )
    return text[:500]


async def probe_mcp_health(
    auth_state: Dict[str, Any],
    config: Optional[Dict[str, Any]] = None,
    manifest: Optional[dict] = None,
) -> Dict[str, Any]:
    """Health probe: list_tools round-trip. Returns status dict (never raises)."""
    try:
        tools = await list_remote_tools(auth_state, config, manifest)
        return {
            "ok": True,
            "status": HEALTH_OK,
            "tool_count": len(tools),
            "error": None,
        }
    except Exception as exc:  # noqa: BLE001 — health must never raise
        message = describe_mcp_failure(exc)
        logger.warning("mcp health probe failed: %s", message)
        return {
            "ok": False,
            "status": HEALTH_ERROR,
            "tool_count": 0,
            "error": message,
        }
