"""Outbound MCP client — mount an external MCP server as a workspace Connector.

I-CON-06: this is a *capability* adapter (tool discovery + invoke), not a
``SyncConnector``. Chat-vendor stub ``mcp_stub_connector`` is a different
surface; do not overload it.

Credentials (``auth_state["headers"]``) never appear in logs or ChangeEvent
snapshots. ``last_error`` is truncated and does not echo header values.
"""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from datetime import timedelta
from typing import Any, AsyncIterator, Dict, List, Optional, Tuple
from urllib.parse import urlparse

import httpx
from mcp.client.session import ClientSession
from mcp.client.streamable_http import streamable_http_client

from app.agentive.nodes import Connector
from app.services.hooks.registry import (
    register_workspace_tools,
    unregister_bundle_registrations,
)
from app.services.url_safety import (
    guarded_async_client,
    pin_public_dns,
    validate_outbound_http_url,
)
from app.utils.time import utc_now_iso

logger = logging.getLogger(__name__)

MCP_SUBCLASS_SLUG = "mcp"

#: One dispatch contract for MCP tools, shared with ``mcp_mount``. Keep these
#: in step: ``run_tool`` resolves this ``module:callable`` and the proxy reads
#: the ``_mcp_*`` keys off the spec.
_PROXY_HANDLER_REF = "app.agentive.connectors.mcp_proxy:invoke"
MCP_TRANSPORT = "streamable_http"
_CONNECT_TIMEOUT_SECONDS = 15.0
_ERROR_MAX_LEN = 500


def mcp_bundle_slug(connector_id: str) -> str:
    """Registry key for this connector's tools in the workspace table."""
    return f"mcp:{connector_id}"


def connector_short_id(connector_id: str) -> str:
    """Stable suffix used in advertised tool names (node id after last ``.``)."""
    return (connector_id or "").rsplit(".", 1)[-1]


def mcp_tool_name(connector_id: str, remote_name: str) -> str:
    """Namespace a remote MCP tool so it never collides with ``integral_*``.

    Delegates to ``mcp_mount.tool_key_for`` so the two mount paths derive ONE
    name. They did not: this module used the full 24-hex node id and passed the
    remote name through unsanitized, while ``mcp_mount`` truncated to 12 chars
    and sanitized. HTTP connectors register through this module but are
    rehydrated after a restart — and refreshed via ``/mcp/refresh`` — through
    ``mcp_mount``, so every HTTP-mounted tool silently changed name on the
    first backend restart, breaking anything holding the old one (a skill's
    allowed-tools, an unresolved staged card, the model's own context).

    Converging on the ``mcp_mount`` form is the safe direction: it is what the
    registry already contains after any restart, it sanitizes names a model API
    would otherwise reject, and its shorter prefix leaves more of the 64-char
    budget for the remote name.
    """
    from app.agentive.connectors.mcp_mount import tool_key_for

    return tool_key_for(connector_id, remote_name)


async def _ensure_mcp_policies(connector: Connector, actor_id: str) -> None:
    """Attach tool.invoke after skip_default_policy MCP creates (I-CON-04)."""
    from app.agentive.connectors.mcp_mount import materialize_mcp_policies

    try:
        await materialize_mcp_policies(connector=connector, actor_id=actor_id)
    except Exception:  # noqa: BLE001 — discover/mount must still finish
        logger.exception(
            "mcp_adapter: MCP policy materialization failed for %s", connector.id
        )


def _safe_error_message(exc: BaseException) -> str:
    """Human-readable error that must not leak credential header values."""
    text = f"{type(exc).__name__}: {exc}"
    lowered = text.lower()
    if "authorization" in lowered or "bearer " in lowered:
        text = f"{type(exc).__name__}: remote MCP request failed"
    return text[:_ERROR_MAX_LEN]


def _first_http_status_error(exc: BaseException) -> Optional[httpx.HTTPStatusError]:
    """Unwrap ExceptionGroup / context to the httpx status that actually failed."""
    if isinstance(exc, httpx.HTTPStatusError):
        return exc
    nested = getattr(exc, "exceptions", None)
    if nested:
        for inner in nested:
            if isinstance(inner, BaseException):
                found = _first_http_status_error(inner)
                if found is not None:
                    return found
    for linked in (exc.__cause__, exc.__context__):
        if isinstance(linked, BaseException) and linked is not exc:
            found = _first_http_status_error(linked)
            if found is not None:
                return found
    return None


def _google_error_message(response: httpx.Response) -> str:
    try:
        payload = response.json()
    except Exception:  # noqa: BLE001
        return (response.text or "").strip()[:300]
    err = payload.get("error") if isinstance(payload, dict) else None
    if isinstance(err, dict):
        return str(err.get("message") or err.get("status") or "").strip()[:500]
    if isinstance(err, str):
        return err.strip()[:500]
    return ""


_GOOGLE_MCP_403_HINTS = {
    "drivemcp.googleapis.com": (
        "Google Drive MCP",
        "drive.googleapis.com",
        "drivemcp.googleapis.com",
    ),
    "gmailmcp.googleapis.com": (
        "Gmail MCP",
        "gmail.googleapis.com",
        "gmailmcp.googleapis.com",
    ),
    "sheetsmcp.googleapis.com": (
        "Google Sheets MCP",
        "sheets.googleapis.com",
        "sheetsmcp.googleapis.com",
    ),
}


def map_mcp_discover_error(exc: BaseException, url: str) -> BaseException:
    """Turn Google Workspace MCP 403 into an operator-actionable error."""
    from app.api.errors import BadRequestError, ConnectorAuthError

    http_err = _first_http_status_error(exc)
    if http_err is None:
        return exc
    status = int(http_err.response.status_code)
    google_msg = _google_error_message(http_err.response)
    host = urlparse(url or "").netloc.lower()
    hint_spec = _GOOGLE_MCP_403_HINTS.get(host)
    if status == 403 and hint_spec:
        product, product_api, mcp_api = hint_spec
        hint = (
            f"{product} returned 403. Enable {product_api} and {mcp_api} "
            "on the Cloud project that owns this OAuth client, then reconnect."
        )
        if google_msg:
            hint = f"{hint} Google: {google_msg}"
        return BadRequestError(message=hint, details={"status": 403})
    if status in (401, 403):
        return ConnectorAuthError(
            message=google_msg or f"MCP server returned HTTP {status}",
            details={"reauth_required": status == 401, "status": status},
        )
    return exc


async def _set_health(
    connector: Connector,
    *,
    status: str,
    error: Optional[str] = None,
) -> None:
    connector.health_status = status
    connector.last_error = error
    connector.last_health_at = utc_now_iso()
    connector.updated_at = connector.last_health_at
    await connector.save()


def plain_auth_state(connector: Connector) -> Dict[str, Any]:
    """Decrypted copy of the connector's ``auth_state`` (F-7, at-rest crypto)."""
    from app.agentive.services.connector_registry_node import decrypt_auth_state

    return decrypt_auth_state(getattr(connector, "auth_state", None) or {})


def store_auth_state(connector: Connector, auth: Dict[str, Any]) -> None:
    """Assign ``auth_state``, encrypting credential-bearing values at rest."""
    from app.agentive.services.connector_registry_node import encrypt_auth_state

    connector.auth_state = encrypt_auth_state(dict(auth))


def _auth_url_and_headers(connector: Connector) -> Tuple[str, Dict[str, str]]:
    auth = plain_auth_state(connector)
    url = str(auth.get("url") or "").strip()
    from app.agentive.connectors.mcp_oauth import mcp_request_headers

    return url, mcp_request_headers(auth)


async def _refresh_oauth_if_needed(connector: Connector) -> None:
    from app.agentive.connectors.mcp_oauth import (
        OAUTH_STATUS_AUTHORIZED,
        oauth_access_expired,
        refresh_mcp_oauth_tokens,
    )

    auth = plain_auth_state(connector)
    oauth = auth.get("oauth")
    if not isinstance(oauth, dict):
        return
    if oauth.get("status") != OAUTH_STATUS_AUTHORIZED:
        return
    if not oauth_access_expired(oauth):
        return
    tokens = await refresh_mcp_oauth_tokens(oauth)
    oauth = dict(oauth)
    oauth["tokens"] = tokens
    auth["oauth"] = oauth
    store_auth_state(connector, auth)
    await connector.save()


@asynccontextmanager
async def _mcp_session(connector: Connector) -> AsyncIterator[ClientSession]:
    await _refresh_oauth_if_needed(connector)
    url, headers = _auth_url_and_headers(connector)
    if not url:
        raise ValueError("MCP connector is missing auth_state.url")
    # F-2: the mount URL was validated once at mount time and never again —
    # neither on later sessions (the operator can PATCH ``auth_state.url``)
    # nor on any redirect hop, because ``create_mcp_http_client`` follows
    # redirects unconditionally. Revalidate, guard the hops, pin the host.
    await validate_outbound_http_url(url)
    timeout = httpx.Timeout(_CONNECT_TIMEOUT_SECONDS)
    http_client = guarded_async_client(headers=headers or None, timeout=timeout)
    with pin_public_dns(url):
        async with http_client:
            async with streamable_http_client(url, http_client=http_client) as streams:
                read_stream, write_stream, _get_sid = streams
                session = ClientSession(
                    read_stream,
                    write_stream,
                    read_timeout_seconds=timedelta(seconds=_CONNECT_TIMEOUT_SECONDS),
                )
                async with session:
                    await session.initialize()
                    yield session


def _remote_annotations(tool: Any) -> Dict[str, Any]:
    """The remote's ``annotations`` block, normalised to a plain dict.

    DISPLAY ONLY. ``readOnlyHint`` here is asserted by the very server the
    bless gate exists to constrain, so it never classifies a tool — see
    ``connectors/mcp_tool_class``. It is carried so the approval card can say
    "the server reports this as read-only", which is information the approver
    should have precisely because it is unverified.

    The stdio path always carried this; the HTTP path dropped it, and every
    hosted server in the catalog is HTTP — so the card's note was unreachable
    on the only transport that had servers behind it.
    """
    ann = getattr(tool, "annotations", None)
    if ann is None:
        return {}
    if isinstance(ann, dict):
        return dict(ann)
    dump = getattr(ann, "model_dump", None)
    if callable(dump):
        try:
            return {k: v for k, v in dump().items() if v is not None}
        except Exception:  # noqa: BLE001
            return {}
    return {}


def _remote_title(tool: Any) -> str:
    """Human label for the tool, preferring the spec field.

    MCP 2025-06-18 puts this on ``Tool.title``; some servers (Google's hosted
    Gmail MCP, observed 2026-09-10) put it under ``annotations.title`` instead.
    Read both so the inspector shows "Search email threads" rather than
    ``search_threads``.
    """
    title = getattr(tool, "title", None)
    if isinstance(title, str) and title.strip():
        return title.strip()
    ann_title = _remote_annotations(tool).get("title")
    return ann_title.strip() if isinstance(ann_title, str) else ""


def _tool_spec_from_remote(connector_id: str, tool: Any) -> Dict[str, Any]:
    """Registry spec for one remote tool — same dispatch contract as stdio.

    ``run_tool`` routes an MCP call by looking for ``_mcp_connector_id`` on the
    spec and otherwise falls through to ``resolve_handler(handler_ref)``. This
    function used to emit ``handler_ref="mcp"`` (no ``module:callable`` colon)
    and carry the connector under ``connector_id``, so every HTTP-mounted tool
    registered fine and then raised ``HookMisconfiguredError`` the moment it was
    invoked — the whole streamable_http mount path was uncallable while looking
    healthy. The stdio path (``mcp_mount._specs_from_tools``) always emitted the
    proxy contract; there is now one contract, not two.

    ``source`` / ``connector_id`` / ``remote_name`` are kept because
    ``_persistable_discovered`` and the connector-detail surfaces read them.
    """
    remote_name = str(getattr(tool, "name", "") or "")
    input_schema = getattr(tool, "inputSchema", None)
    if not isinstance(input_schema, dict):
        input_schema = {"type": "object", "properties": {}}
    return {
        "key": mcp_tool_name(connector_id, remote_name),
        "source": "mcp",
        "connector_id": connector_id,
        "remote_name": remote_name,
        "title": _remote_title(tool),
        "description": str(getattr(tool, "description", None) or ""),
        "input_schema": input_schema,
        "privileged": False,
        "handler_ref": _PROXY_HANDLER_REF,
        "_mcp_connector_id": connector_id,
        "_mcp_remote_name": remote_name,
        # Display only — see connectors/mcp_tool_class.py. NEVER gate on this.
        "_mcp_annotations": _remote_annotations(tool),
    }


def _discovered_tools(connector: Connector) -> List[Dict[str, Any]]:
    """Tools live on ``auth_state`` (Connector has no ``discovered_tools`` field)."""
    auth = getattr(connector, "auth_state", None) or {}
    raw = auth.get("discovered_tools")
    return list(raw) if isinstance(raw, list) else []


def _set_discovered_tools(connector: Connector, tools: List[Dict[str, Any]]) -> None:
    # Read the STORED dict (not the decrypted copy) so re-saving the node does
    # not round-trip ciphertext through plaintext; ``store_auth_state`` is
    # idempotent over already-encrypted values either way.
    auth = dict(getattr(connector, "auth_state", None) or {})
    auth["discovered_tools"] = tools
    store_auth_state(connector, auth)


def _persistable_discovered(specs: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Drop registry-only keys before persisting on the Connector node."""
    out: List[Dict[str, Any]] = []
    for spec in specs:
        out.append(
            {
                "name": spec.get("remote_name") or "",
                "title": spec.get("title") or "",
                "description": spec.get("description") or "",
                "input_schema": spec.get("input_schema")
                or {"type": "object", "properties": {}},
                "annotations": dict(spec.get("_mcp_annotations") or {}),
            }
        )
    return out


def _specs_from_discovered(
    connector_id: str, discovered: List[Dict[str, Any]]
) -> List[Dict[str, Any]]:
    specs: List[Dict[str, Any]] = []
    for item in discovered or []:
        remote_name = str(item.get("name") or "").strip()
        if not remote_name:
            continue
        input_schema = item.get("input_schema")
        if not isinstance(input_schema, dict):
            input_schema = {"type": "object", "properties": {}}
        specs.append(
            {
                "key": mcp_tool_name(connector_id, remote_name),
                "source": "mcp",
                "connector_id": connector_id,
                "remote_name": remote_name,
                "title": str(item.get("title") or ""),
                "description": str(item.get("description") or ""),
                "input_schema": input_schema,
                "privileged": False,
                # Same dispatch contract as _tool_spec_from_remote — this is
                # the post-restart rehydration path and carried the identical
                # uncallable shape.
                "handler_ref": _PROXY_HANDLER_REF,
                "_mcp_connector_id": connector_id,
                "_mcp_remote_name": remote_name,
                "_mcp_annotations": dict(item.get("annotations") or {}),
            }
        )
    return specs


def register_mcp_tools(
    connector: Connector, specs: Optional[List[Dict[str, Any]]] = None
) -> int:
    """Register (or re-register) this connector's tools into the workspace table.

    Idempotent: unregisters the previous ``mcp:{id}`` bundle first. Does not
    require a process restart (I-CON-06).
    """
    workspace_id = str(getattr(connector, "workspace_id", "") or "").strip()
    if not workspace_id:
        logger.warning(
            "mcp_adapter: connector %s has no workspace_id; skipping tool registration",
            connector.id,
        )
        return 0
    if specs is None:
        specs = _specs_from_discovered(connector.id, _discovered_tools(connector))
    slug = mcp_bundle_slug(connector.id)
    unregister_bundle_registrations(workspace_id, slug)
    if specs:
        register_workspace_tools(workspace_id, slug, specs)
    # Canonical slug keys (shared across rows) — same refcount contract as
    # the mount path; row keys above stay the source of truth for removal.
    try:
        from app.agentive.connectors.mcp_mount import (
            canonical_mcp_bundle_slug,
            canonical_mcp_specs_from_discovered,
            catalog_slug_for_connector,
            track_canonical_mcp_row,
        )
        from app.services.hooks.registry import register_workspace_tools as _register

        slug_name = catalog_slug_for_connector(connector)
        if slug_name and specs:
            discovered = [
                {
                    "name": s.get("remote_name") or "",
                    "description": s.get("description") or "",
                    "input_schema": s.get("input_schema") or {},
                    "annotations": s.get("_mcp_annotations") or {},
                }
                for s in specs
            ]
            canonical = canonical_mcp_specs_from_discovered(slug_name, discovered)
            _register(workspace_id, canonical_mcp_bundle_slug(slug_name), canonical)
            track_canonical_mcp_row(workspace_id, slug_name, connector.id)
    except Exception:  # noqa: BLE001 — row keys are registered; canonical is extra
        logger.exception("mcp_adapter: canonical registration failed")
    try:
        from app.agentive.workspace_agent_profile import invalidate_workspace_profile

        invalidate_workspace_profile(workspace_id)
    except Exception:  # noqa: BLE001
        logger.exception(
            "mcp_adapter: profile invalidation failed for %s", workspace_id
        )
    return len(specs)


async def discover(connector: Connector) -> List[Dict[str, Any]]:
    """Call remote ``tools/list``, persist specs, register, update health."""
    try:
        async with _mcp_session(connector) as session:
            listed = await session.list_tools()
            tools = list(getattr(listed, "tools", None) or [])
        specs = [
            _tool_spec_from_remote(connector.id, t)
            for t in tools
            if getattr(t, "name", None)
        ]
        _set_discovered_tools(connector, _persistable_discovered(specs))
        await _set_health(connector, status="ok", error=None)
        register_mcp_tools(connector, specs)
        await _ensure_mcp_policies(connector, getattr(connector, "owner", "") or "")
        return specs
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "mcp_adapter: discover failed for %s: %s",
            connector.id,
            _safe_error_message(exc),
        )
        await _set_health(connector, status="error", error=_safe_error_message(exc))
        url, _headers = _auth_url_and_headers(connector)
        mapped = map_mcp_discover_error(exc, url)
        if mapped is not exc:
            raise mapped from exc
        raise


def _call_tool_text(result: Any) -> str:
    parts: List[str] = []
    for block in list(getattr(result, "content", None) or []):
        text = getattr(block, "text", None)
        if text:
            parts.append(str(text))
    return "\n".join(parts).strip()


def _call_tool_payload(result: Any) -> Any:
    text = _call_tool_text(result)
    structured = getattr(result, "structuredContent", None)
    if structured is not None:
        return structured
    if text:
        return {"result": text}
    return {"result": None}


async def mount_mcp_connector(
    *,
    owner_id: str,
    workspace_id: str,
    url: str,
    headers: Optional[Dict[str, str]] = None,
) -> Connector:
    """Create an MCP Connector, discover tools, and register them (no restart)."""
    from app.agentive.services.connector_registry_node import create_connector

    url = (url or "").strip()
    if not url:
        raise ValueError("url is required")
    if not workspace_id:
        raise ValueError("workspace_id is required")

    await validate_outbound_http_url(url)

    from app.agentive.connectors.mcp_oauth import (
        OAUTH_STATUS_PENDING,
        mcp_oauth_redirect_uri,
        probe_mcp_authorization,
        sign_mcp_oauth_state,
        start_mcp_oauth_session,
    )
    from app.api.errors import ConnectorAuthError

    probe = await probe_mcp_authorization(url, headers)
    if probe.status_code == 401 and (headers or {}).get("Authorization"):
        raise ConnectorAuthError(
            message="The MCP server rejected the supplied credentials",
            details={"reauth_required": True, "status": 401},
        )

    if probe.needs_oauth:
        oauth = await start_mcp_oauth_session(
            url,
            probe_response=probe.response,
            redirect_uri=mcp_oauth_redirect_uri(),
        )
        connector = await create_connector(
            owner=owner_id,
            kind="mcp",
            auth_state={
                "url": url,
                "headers": dict(headers or {}),
                "transport": MCP_TRANSPORT,
                "oauth": oauth,
            },
            capabilities=["mcp.invoke"],
            skip_default_policy=True,
            workspace_id=workspace_id,
        )
        connector.subclass_slug = MCP_SUBCLASS_SLUG
        connector.workspace_id = workspace_id
        await connector.save()
        await _ensure_mcp_policies(connector, owner_id)
        state = sign_mcp_oauth_state(owner_id, connector.id)
        auth = dict(connector.auth_state or {})
        pending = dict(auth.get("oauth") or {})
        pending["state"] = state
        pending["status"] = OAUTH_STATUS_PENDING
        auth["oauth"] = pending
        store_auth_state(connector, auth)
        await _set_health(connector, status="unknown", error=None)
        return connector

    connector = await create_connector(
        owner=owner_id,
        kind="mcp",
        auth_state={
            "url": url,
            "headers": dict(headers or {}),
            "transport": MCP_TRANSPORT,
        },
        capabilities=["mcp.invoke"],
        skip_default_policy=True,
        workspace_id=workspace_id,
    )
    connector.subclass_slug = MCP_SUBCLASS_SLUG
    connector.workspace_id = workspace_id
    await connector.save()
    await _ensure_mcp_policies(connector, owner_id)
    try:
        await discover(connector)
    except Exception:
        try:
            from app.agentive.services.connector_registry_node import (
                delete_connector,
            )

            await delete_connector(connector.id)
        except Exception:  # noqa: BLE001
            logger.exception(
                "mcp_adapter: compensate-delete failed for %s", connector.id
            )
        raise
    refreshed = await Connector.get(connector.id)
    return refreshed if refreshed is not None else connector


async def begin_pre_registered_mcp_oauth(
    *,
    owner_id: str,
    workspace_id: str,
    url: str,
    oauth: Dict[str, Any],
) -> Connector:
    """Create an MCP Connector in pending OAuth without probing the URL.

    Used when the remote MCP does not 401 on initialize (Google Drive) but
    still requires a Bearer token for real work. The caller supplies a
    pre-registered OAuth session dict from ``start_pre_registered_oauth_session``.
    """
    from app.agentive.connectors.mcp_oauth import (
        OAUTH_STATUS_PENDING,
        sign_mcp_oauth_state,
    )
    from app.agentive.services.connector_registry_node import create_connector

    url = (url or "").strip()
    if not url:
        raise ValueError("url is required")
    if not workspace_id:
        raise ValueError("workspace_id is required")
    connector = await create_connector(
        owner=owner_id,
        kind="mcp",
        auth_state={
            "url": url,
            "headers": {},
            "transport": MCP_TRANSPORT,
            "oauth": dict(oauth),
        },
        capabilities=["mcp.invoke"],
        skip_default_policy=True,
        workspace_id=workspace_id,
    )
    connector.subclass_slug = MCP_SUBCLASS_SLUG
    connector.workspace_id = workspace_id
    await connector.save()
    await _ensure_mcp_policies(connector, owner_id)
    state = sign_mcp_oauth_state(owner_id, connector.id)
    auth = dict(connector.auth_state or {})
    pending = dict(auth.get("oauth") or {})
    pending["state"] = state
    pending["status"] = OAUTH_STATUS_PENDING
    auth["oauth"] = pending
    store_auth_state(connector, auth)
    await _set_health(connector, status="unknown", error=None)
    return connector


async def complete_mcp_oauth(
    *,
    user_id: str,
    code: str,
    state: str,
) -> Connector:
    """Exchange the authorization code, persist tokens, then discover tools."""
    from app.agentive.connectors.mcp_oauth import (
        OAUTH_STATUS_AUTHORIZED,
        exchange_mcp_oauth_code,
        verify_mcp_oauth_state,
    )
    from app.api.errors import (
        BadRequestError,
        ConnectorAuthError,
        ResourceNotFoundError,
    )

    connector_id = verify_mcp_oauth_state(state, user_id)
    if not connector_id:
        raise ConnectorAuthError(
            message="MCP OAuth callback rejected — state token invalid or expired",
            details={"reauth_required": False, "reason": "state_mismatch"},
        )
    connector = await Connector.get(connector_id)
    if connector is None or connector.owner != user_id:
        raise ResourceNotFoundError(
            message=f"Connector {connector_id!r} not found",
            details={"connector_id": connector_id},
        )
    if (getattr(connector, "subclass_slug", "") or "") != MCP_SUBCLASS_SLUG:
        raise BadRequestError(message="Connector is not an MCP mount")
    # F-9: completing OAuth exchanges tokens and hot-registers this server's
    # tools into the workspace. Ownership is a scalar that survives removal
    # from that workspace, so re-check authority here — the callback is a
    # lifecycle mutation, not a read.
    from app.agentive.api.connectors import _require_connector_workspace_authority

    await _require_connector_workspace_authority(user_id, connector)
    auth = plain_auth_state(connector)
    oauth = dict(auth.get("oauth") or {})
    if oauth.get("state") != state:
        raise ConnectorAuthError(
            message="MCP OAuth callback rejected — state token invalid or expired",
            details={"reauth_required": False, "reason": "state_mismatch"},
        )
    tokens = await exchange_mcp_oauth_code(oauth, code)
    oauth["tokens"] = tokens
    oauth["status"] = OAUTH_STATUS_AUTHORIZED
    oauth.pop("code_verifier", None)
    oauth.pop("code_challenge", None)
    oauth.pop("state", None)
    auth["oauth"] = oauth
    store_auth_state(connector, auth)
    await connector.save()
    await discover(connector)
    refreshed = await Connector.get(connector.id)
    return refreshed if refreshed is not None else connector
